import os
import libsql_client
from snaptrade import get_snaptrade_auth
import logging
import json
from handlers import (
    click_update_all_activities,
    click_update_orders_by_account,
    click_update_nickname,
)
from queries import get_all_active_accounts

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
def handle_update_all_activities(snaptrade, client, data):
    return click_update_all_activities(snaptrade, client, hours=4, is_bulk=False)


def handle_update_orders(snaptrade, client, data):
    return click_update_orders_by_account(
        snaptrade, client, data.get("account_id"), seconds=30
    )


def handle_get_accounts(snaptrade, client, data):
    accounts = get_all_active_accounts(client)
    return {"status": "success", "data": accounts}


def handle_update_nickname(snaptrade, client, data):
    return click_update_nickname(client, data.get("account_id"), data.get("nickname"))


def get_turso_client():
    return libsql_client.create_client_sync(
        url=os.environ["TURSO_DATABASE_URL"],
        auth_token=os.environ["TURSO_AUTH_TOKEN"],
    )


# ── Action Registry ──────────────────────────────────────────────────────────
ACTION_REGISTRY = {
    "update_all_activities": handle_update_all_activities,
    "update_orders_by_account": handle_update_orders,
    "get_all_account": handle_get_accounts,
    "update_nickname": handle_update_nickname,
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
        client = get_turso_client()

        try:
            res = controller(snaptrade, client, data)
            return {"statusCode": 200, "headers": HEADERS, "body": json.dumps(res)}

        finally:
            client.close()

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
        None,
    )
