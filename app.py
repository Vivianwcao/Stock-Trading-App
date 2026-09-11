from snaptrade import get_snaptrade_auth
import logging
import json
from handlers import (
    click_update_all_activities,
    click_update_orders_by_account,
    click_update_nickname,
)
from queries import get_all_active_accounts, get_all_active_transactions
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
def handle_update_all_activities(snaptrade, conn, data):
    return click_update_all_activities(snaptrade, conn, hours=4, is_bulk=False)


def handle_update_orders(snaptrade, conn, data):
    return click_update_orders_by_account(
        snaptrade, conn, data.get("account_id"), seconds=30
    )


def handle_get_accounts(snaptrade, conn, data):
    accounts = get_all_active_accounts(conn)
    return {"status": "success", "data": accounts}


def handle_update_nickname(snaptrade, conn, data):
    return click_update_nickname(conn, data.get("account_id"), data.get("nickname"))


def handle_get_transactions(snaptrade, conn, data):
    transactions = get_all_active_transactions(conn, data)
    return {"status": "success", "data": transactions}


# ── Action Registry ──────────────────────────────────────────────────────────
ACTION_REGISTRY = {
    "update_all_activities": handle_update_all_activities,
    "update_orders_by_account": handle_update_orders,
    "update_nickname": handle_update_nickname,
    "get_all_account": handle_get_accounts,
    "get_transactions": handle_get_transactions,
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
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        try:
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
        # {
        #     "action": "update_orders_by_account",
        #     "data": {"account_id": "4cd8021d-56b3-4b8d-93b6-12976d587a08"},
        # },
        {"action": "update_all_activities"},
        # {"action": "get_all_account"},
        # {
        #     "action": "update_nickname",
        #     "data": {
        #         "account_id": "0170ad7d-dc73-48aa-a4b2-61767f8472fc",
        #         "nickname": "微微首次购房",
        #     },
        # },
        None,
    )
