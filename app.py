import sqlite3
import os
import json
from snaptrade import get_snaptrade_auth
import logging
import time
from update_activities import (
    init_db,
    update_accounts,
    update_activities,
    update_recent_orders,
)
from utils import calculate_wait_time

# ── Logging ─────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # required for lambda
logging.basicConfig(level=logging.INFO)  # required for local

# return structure
# {
#   "status": "success | cooldown | fail",
#   "data": { ... } | null,
#   "error": "Error description string" | null
# }

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",  # Allows Netlify frontend to fetch data
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
}


def click_update_all_activities(snaptrade, conn, hours=4, is_bulk=False):
    hrs, mins, secs = calculate_wait_time(conn, api_source="activities", hours=hours)
    if hrs == mins == secs == 0:
        # ready tp update:
        update_accounts(snaptrade, conn)

        accounts = get_all_active_accounts(conn)
        if not accounts:
            return {
                "status": "fail",
            }

        account_ids = {account["id"]: {} for account in accounts}
        for account_id, info in account_ids.items():
            try:
                hrs, mins, secs = calculate_wait_time(
                    conn, api_source="activities", account_id=account_id, hours=hours
                )
                if hrs == mins == secs == 0:
                    # ready tp update:
                    update_activities(snaptrade, conn, account_id, is_bulk)
                    info["status"] = "success"
                else:
                    info["status"] = "cooldown"
                    info["data"] = {"hours": hrs, "minutes": mins, "seconds": secs}
            except Exception as e:
                logger.exception(
                    f"Failed to sync account: {account_id}. Continuing to next account."
                )
                info["status"] = "fail"
                info["error"] = f"{type(e).__name__}: {str(e)}"

                continue

        return {"status": "success", "data": account_ids}
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def click_update_orders_by_account(snaptrade, conn, account_id, seconds=30):
    hrs, mins, secs = calculate_wait_time(
        conn, api_source="orders", account_id=account_id, seconds=seconds
    )
    if hrs == mins == secs == 0:
        # ready tp update:
        update_recent_orders(snaptrade, conn, account_id)
        return {
            "status": "success",
        }
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def get_all_active_accounts(conn):
    rows = conn.execute("""
            select id
            from accounts
            where status='open'
            and balance > 10
        """).fetchall()

    return [dict(row) for row in rows] if rows else None


def handler(event, context):
    try:
        logger.info(json.dumps(event))

        action = event.get("action")
        data = event.get("data")

        snaptrade = get_snaptrade_auth()
        # 1. Connect to local database file (creates portfolio.db automatically)
        conn = sqlite3.connect("stocks.db")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        try:
            conn.executescript(
                "drop table activities; drop table accounts; drop table last_fetched;"
            )
            init_db(conn)  # run once
            if action == "update_all_activities":
                res = click_update_all_activities(snaptrade, conn, hours=4)
            elif action == "update_orders_by_account":
                res = click_update_orders_by_account(
                    snaptrade, conn, account_id=data, seconds=30
                )
            elif action == "get_all_account":
                res = get_all_active_accounts(conn)
            else:
                return {
                    "statusCode": 400,
                    "headers": HEADERS,
                    "body": json.dumps({"status": "fail", "error": "Invalid action"}),
                }
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
    handler({}, None)
