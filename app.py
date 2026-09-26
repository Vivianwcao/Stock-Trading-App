from snaptrade import get_snaptrade_auth
import logging
import json
import os
from dotenv import load_dotenv
from handlers import (
    on_page_load,
    click_update_activities_and_get_transactions_by_account,
    click_update_orders_and_get_transactions_by_account,
    click_update_positions_and_get_latest_analysis_by_account,
    trigger_update_positions_bulk,
    click_get_latest_accounts,
    click_get_transactions_active_stocks_by_nickname,
)
from update_tables import update_account_nickname
from queries import (
    get_all_active_accounts,
    get_latest_analysis_all_accounts,
    get_latest_analysis_by_account,
    get_analysis_by_account_by_snapshot,
    compare_analysis_by_account_across_snapshots,
    get_transactions_by_stocks_by_nickname,
)
from utils import json_default
import psycopg2
import psycopg2.extras


# ── Logging ─────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # required for lambda
logging.basicConfig(level=logging.INFO)  # required for local


# ── Action Controllers ───────────────────────────────────────────────────────
def handle_on_page_load(snaptrade, conn, data):
    return on_page_load(conn)


def handle_update_activities_and_get_transactions_by_account(snaptrade, conn, data):
    return click_update_activities_and_get_transactions_by_account(
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
    return trigger_update_positions_bulk(snaptrade, conn, data.get("trigger"))


def handle_get_latest_analysis_by_account(snaptrade, conn, data):
    analysis = get_latest_analysis_by_account(conn, data.get("account_id"))
    return {"status": "success", "data": analysis}


def handle_update_positions_and_get_latest_analysis_by_account(snaptrade, conn, data):
    return click_update_positions_and_get_latest_analysis_by_account(
        snaptrade, conn, data.get("account_id"), "manual", seconds=60
    )


def handle_get_accounts(snaptrade, conn, data):
    accounts = get_all_active_accounts(conn)
    return {"status": "success", "data": accounts}


def handle_get_transactions_on_recent_active_stocks_by_nickname(snaptrade, conn, data):
    return click_get_transactions_active_stocks_by_nickname(conn, data.get("nickname"))


def handle_get_all_transactions_by_symbol_by_nickname(snaptrade, conn, data):
    nickname = data.get("nickname")
    symbol = data.get("symbol")
    if not nickname or not symbol:
        return {"status": "fail", "error": "nickname and symbol required"}
    transactions = get_transactions_by_stocks_by_nickname(conn, nickname, [symbol])
    return {"status": "success", "data": transactions}


def handle_get_analysis_by_account_by_date(snaptrade, conn, data):
    account_id = data.get("account_id")
    sync_date = data.get("sync_date")
    if not account_id or not sync_date:
        return {"status": "fail", "error": "nickname and sync_date required"}
    analysis = get_analysis_by_account_by_snapshot(conn, account_id, sync_date)
    return {"status": "success", "data": analysis}


def handle_compare_analysis_by_account_across_snapshots(snaptrade, conn, data):
    account_id = data.get("account_id")
    sync_dates = data.get("sync_dates")
    if not account_id or not sync_dates:
        return {"status": "fail", "error": "account_id and sync_dates required"}
    analysis = compare_analysis_by_account_across_snapshots(
        conn, account_id, sync_dates
    )
    return {"status": "success", "data": analysis}


def handle_get_latest_analysis_all_accounts(snaptrade, conn, data):
    return get_latest_analysis_all_accounts(conn)


def handle_update_and_get_accounts(snaptrade, conn, data):
    return click_get_latest_accounts(snaptrade, conn, minutes=10)


def handle_update_account_nickname(snaptrade, conn, data):
    return update_account_nickname(conn, data.get("account_id"), data.get("nickname"))


# ── Action Registry ──────────────────────────────────────────────────────────
ACTION_REGISTRY = {
    "on_page_load": handle_on_page_load,
    "update_activities_and_get_transactions_by_account": handle_update_activities_and_get_transactions_by_account,
    "update_orders_and_get_transactions_by_account": handle_update_orders_and_get_transactions,
    "update_and_get_accounts": handle_update_and_get_accounts,
    "update_positions_event_bridge": handle_update_positions_event_bridge,
    "update_positions_and_get_latest_analysis_by_account": handle_update_positions_and_get_latest_analysis_by_account,
    "get_all_accounts": handle_get_accounts,
    "get_transactions_on_recent_active_stocks_by_nickname": handle_get_transactions_on_recent_active_stocks_by_nickname,
    "get_all_transactions_by_symbol_by_nickname": handle_get_all_transactions_by_symbol_by_nickname,
    "get_latest_analysis_all_accounts": handle_get_latest_analysis_all_accounts,
    "get_latest_analysis_by_account": handle_get_latest_analysis_by_account,
    "get_analysis_by_account_by_snapshot": handle_get_analysis_by_account_by_date,
    "compare_analysis_by_account_across_snapshots": handle_compare_analysis_by_account_across_snapshots,
    "update_nickname": handle_update_account_nickname,
}


HEADERS = {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}


def app_handler(event, context):
    try:
        # logger.info(json.dumps(event))

        headers_in = event.get("headers") or {}
        password = headers_in.get("x-app-password")

        if password != os.environ.get("APP_PASSWORD"):
            return {
                "statusCode": 401,
                "headers": HEADERS,
                "body": json.dumps({"status": "fail", "error": "Incorrect password"}),
            }

        body = json.loads(event.get("body") or "{}")
        action = body.get("action")
        data = body.get("data", {})

        controller = ACTION_REGISTRY.get(action)
        if not controller:
            return {
                "statusCode": 400,
                "headers": HEADERS,
                "body": json.dumps({"status": "fail", "error": "Invalid action"}),
            }

        snaptrade = get_snaptrade_auth()

        # Local testing
        conn = psycopg2.connect(
            os.environ["DATABASE_URL"],
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

        # conn = psycopg2.connect(
        #     os.environ["DATABASE_URL_POOLED"],
        #     cursor_factory=psycopg2.extras.RealDictCursor,
        # )

        try:
            res = controller(snaptrade, conn, data)

            return {
                "statusCode": 200,
                "headers": HEADERS,
                "body": json.dumps(
                    res,
                    default=json_default,
                ),
            }
        finally:
            conn.close()

    except Exception as e:
        logger.exception("Request failed.")
        return {
            "statusCode": 500,
            "headers": HEADERS,
            "body": json.dumps(
                {"status": "fail", "error": f"{type(e).__name__}: {str(e)}"},
                default=json_default,
            ),
        }
