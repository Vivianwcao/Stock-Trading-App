from snaptrade import get_snaptrade_auth
import logging
import json
from handlers import (
    on_page_load,
    click_update_activities_by_account,
    click_update_orders_and_get_transactions_by_account,
    click_update_positions_and_get_latest_analysis_by_account,
    trigger_update_positions_bulk,
    click_get_latest_accounts,
)
from update_tables import (
    update_accounts,
    update_account_nickname,
    update_wealth_simple_account_id,
)
from queries import (
    create_tables,
    get_all_active_accounts,
    get_latest_analysis_all_accounts,
    get_transactions_all_accounts,
    get_analysis_by_account_by_snapshot,
    compare_analysis_by_account_across_snapshots,
)
import sqlite3

# ── Logging ─────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # required for lambda
logging.basicConfig(level=logging.INFO)  # required for local

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",  # Allows Netlify frontend to fetch data
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
}


# ── Action Controllers ───────────────────────────────────────────────────────
def handle_on_page_load(snaptrade, conn, data):
    return on_page_load(conn)


def handle_update_activities(snaptrade, conn, data):
    return click_update_activities_by_account(
        snaptrade,
        conn,
        data.get("account_id"),
        seconds=60,
        activities_hours=4,
        is_bulk=False,
    )


def handle_update_orders_and_get_transactions(snaptrade, conn, data):
    return click_update_orders_and_get_transactions_by_account(
        snaptrade, conn, data.get("account_id"), seconds=60
    )


def handle_update_positions_event_bridge(snaptrade, conn, data):
    return trigger_update_positions_bulk(snaptrade, conn, "scheduled")


def handle_update_positions_and_get_latest_analysis_by_account(snaptrade, conn, data):
    return click_update_positions_and_get_latest_analysis_by_account(
        snaptrade, conn, data.get("account_id"), "manual", seconds=60
    )


def handle_get_accounts(snaptrade, conn, data):
    accounts = get_all_active_accounts(conn)
    return {"status": "success", "data": accounts}


def handle_get_transactions_all_accounts(snaptrade, conn, data):
    transactions = get_transactions_all_accounts(conn)
    return {"status": "success", "data": transactions}


def handle_get_analysis_by_account_by_date(snaptrade, conn, data):
    analysis = get_analysis_by_account_by_snapshot(
        conn, data.get("account_id"), data.get("sync_date")
    )
    return {"status": "success", "data": analysis}


def handle_compare_analysis_by_account_across_snapshots(snaptrade, conn, data):
    analysis = compare_analysis_by_account_across_snapshots(
        conn, data.get("account_id"), data.get("sync_dates")
    )
    return {"status": "success", "data": analysis}


def handle_get_latest_analysis_all_accounts(snaptrade, conn, data):
    return get_latest_analysis_all_accounts(conn)


def handle_init_db(snaptrade, conn, data):
    create_tables(conn)
    return {"status": "success"}


def handle_update_and_get_accounts(snaptrade, conn, data):
    return click_get_latest_accounts(snaptrade, conn, minutes=10)


def handle_update_wealth_simple_account_id(snaptrade, conn, data):
    update_wealth_simple_account_id(
        conn, data.get("account_id"), data.get("ws_account_id")
    )
    return {"status": "success"}


def handle_update_account_nickname(snaptrade, conn, data):
    return update_account_nickname(conn, data.get("account_id"), data.get("nickname"))


# ── Action Registry ──────────────────────────────────────────────────────────
ACTION_REGISTRY = {
    "init_db": handle_init_db,
    "on_page_load": handle_on_page_load,
    "update_activities_by_account": handle_update_activities,
    "update_orders_and_get_transactions_by_account": handle_update_orders_and_get_transactions,
    "update_and_get_accounts": handle_update_and_get_accounts,
    "update_positions_event_bridge": handle_update_positions_event_bridge,
    "update_positions_and_get_latest_analysis_by_account": handle_update_positions_and_get_latest_analysis_by_account,
    "get_all_accounts": handle_get_accounts,
    "get_transactions_all_accounts": handle_get_transactions_all_accounts,
    "get_latest_analysis_all_accounts": handle_get_latest_analysis_all_accounts,
    "get_analysis_by_account_by_snapshot": handle_get_analysis_by_account_by_date,
    "compare_analysis_by_account_across_snapshots": handle_compare_analysis_by_account_across_snapshots,
    "update_nickname": handle_update_account_nickname,
    "update_wealth_simple_account_id": handle_update_wealth_simple_account_id,
}


def app_handler(event, context):
    try:
        logger.info(json.dumps(event))
        action = event.get("action")
        data = event.get("data", {})

        controller = ACTION_REGISTRY.get(action)
        if not controller:
            return {
                "statusCode": 400,
                "headers": HEADERS,
                "body": json.dumps({"status": "fail", "error": "Invalid action"}),
            }

        snaptrade = get_snaptrade_auth()
        # Connect to local database file (creates stocks.db automatically)
        conn = sqlite3.connect("stocks.db")

        try:
            conn.row_factory = sqlite3.Row

            conn.execute("PRAGMA foreign_keys = ON")
            # writes commit straight to stocks.db, and temporary files are automatically deleted instantly
            conn.execute("PRAGMA journal_mode = DELETE;")

            res = controller(snaptrade, conn, data)

            return {"statusCode": 200, "headers": HEADERS, "body": json.dumps(res)}
        finally:
            conn.close()

    except Exception as e:
        logger.exception("Request failed.")
        return {
            "statusCode": 500,
            "headers": HEADERS,
            "body": json.dumps(
                {"status": "fail", "error": f"{type(e).__name__}: {str(e)}"}
            ),
        }


if __name__ == "__main__":
    app_handler(
        # {"action": "update_and_get_accounts"},
        # {
        #     "action": "update_orders_and_get_transactions_by_account",
        #     "data": {"account_id": "4cd8021d-56b3-4b8d-93b6-12976d587a08"},
        # },
        {
            "action": "update_activities_by_account",
            "data": {"account_id": "4cd8021d-56b3-4b8d-93b6-12976d587a08"},
        },
        # {
        #     "action": "update_positions_and_get_latest_analysis_by_account",
        #     "data": {"account_id": "4cd8021d-56b3-4b8d-93b6-12976d587a08"},
        # },
        # {"action": "get_all_accounts"},
        # {
        #     "action": "update_account_nickname",
        #     "data": {
        #         "account_id": "0170ad7d-dc73-48aa-a4b2-61767f8472fc",
        #         "nickname": "vivian_fhsa",
        #     },
        # },
        # {
        #     "action": "update_wealth_simple_account_id",
        #     "data": {
        #         "account_id": "8bbc2e4f-feef-457d-b9a7-476a28f9fbc8",
        #         "ws_account_id": "HC05761K0CAD",
        #     },
        # },
        None,
    )
