import sqlite3
import os
import json
from snaptrade import get_snaptrade_auth
import logging
import time
from datetime import datetime, timedelta, timezone
from update_activities import (
    init_db,
    update_accounts,
    fetch_activities,
    fetch_recent_orders,
)

# ── Logging ─────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # required for lambda
logging.basicConfig(level=logging.INFO)  # required for local


# The * means everything after it must be passed as named arguments
def calculate_wait_time(conn, *, is_activities, hours=0, minutes=0):
    # all accounts
    if is_activities:
        row = conn.execute("""
            select
                max(fetched_at) 
            from activities
            where source = 'api_activities'
        """).fetchone()
    else:
        row = conn.execute("""
            select
                max(fetched_at)
            from activities
        """).fetchone()

    current_time = datetime.now(timezone.utc)
    last_fetch_time = datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00"))
    elapsed = current_time - last_fetch_time

    seconds = hours * 3600 + minutes * 60 - int(elapsed.total_seconds())
    if seconds <= 0:
        return (0, 0, 0)
    hrs = seconds // 3600
    mins = seconds % 3600 // 60
    secs = seconds % 60
    return hrs, mins, secs


def click_update_all_activities(snaptrade, conn, hours=4):
    hrs, mins, secs = calculate_wait_time(conn, is_activities=True, hours=hours)
    if hrs == mins == secs == 0:
        # ready tp update:
        update_accounts(snaptrade, conn)
        time.sleep(30)
        fetch_activities(snaptrade, conn)
    else:
        logger.info(
            "Too early to update. Still need to wait for %d hours %d minutes %d seconds.",
            hrs,
            mins,
            secs,
        )
        return hrs, mins, secs


def handler(event, context):
    logger.info(json.dumps(event))

    snaptrade = get_snaptrade_auth()
    # 1. Connect to local database file (creates portfolio.db automatically)
    conn = sqlite3.connect("stocks.db")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        # conn.executescript("drop table activities")
        init_db(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    handler({}, None)
