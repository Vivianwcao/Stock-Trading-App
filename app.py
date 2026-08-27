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

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",  # Allows Netlify frontend to fetch data
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
}


def click_update_all_activities(snaptrade, conn, hours=4):
    hrs, mins, secs = calculate_wait_time(conn, hours=hours)
    if hrs == mins == secs == 0:
        # ready tp update:
        update_accounts(snaptrade, conn)
        time.sleep(10)
        update_activities(snaptrade, conn)
        return {"status": "success", "message": "Updated all activities"}
    return {
        "status": "cooldown",
        "wait": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def click_update_orders_by_account(snaptrade, conn, account_id, seconds=30):
    hrs, mins, secs = calculate_wait_time(conn, seconds=seconds)
    if hrs == mins == secs == 0:
        # ready tp update:
        update_recent_orders(snaptrade, conn, account_id)
        return {
            "status": "success",
            "message": f"Updated orders for account {account_id}",
        }
    return {
        "status": "cooldown",
        "wait": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def get_all_accounts(conn):
    rows = conn.execute("select * from accounts").fetchall()
    return [dict(row) for row in rows]


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
                res = get_all_accounts(conn)
            else:
                return {"statusCode": 400, "headers": HEADERS, "body": "Invalid action"}
            return {"statusCode": 200, "headers": HEADERS, "body": json.dumps(res)}
        finally:
            conn.close()
    except Exception as e:
        logger.exception("Request failed.")
        return {"statusCode": 500, "headers": HEADERS, "body": str(e)}


if __name__ == "__main__":
    handler({}, None)
