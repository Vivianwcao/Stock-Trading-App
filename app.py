import sqlite3
import os
import json
from snaptrade import get_snaptrade_auth
import logging
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


def check_elapsed_timedelta(conn, account_ids=None):
    if account_ids:
        placeholder = ",".join("?" for _ in account_ids)
        row = conn.execute(
            f"""
            select
                max(fetched_at) 
            from activities
            where account_id in ({placeholder})
        """,
            account_ids,
        ).fetchone()
    else:
        # all accounts
        row = conn.execute("""
            select
                max(fetched_at) 
            from activities
        """).fetchone()

    if row["fetched_at"] is None:
        return True  # never fetched

    last_fetch_time = datetime.fromisoformat(row["fetched_at"].replaced("Z", "+00:00"))
    current_time = datetime.now(timezone.utc)
    return current_time - last_fetch_time


def click_update_all_activities(conn, period):
    pass


def handler(event, context):
    logger.info(json.dumps(event))

    snaptrade = get_snaptrade_auth()
    # 1. Connect to local database file (creates portfolio.db automatically)
    with sqlite3.connect("stocks.db") as conn:
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            # conn.executescript("drop table activities")
            # init_db(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    handler({}, None)
