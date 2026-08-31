import json
import logging
from datetime import datetime, timezone


# ── Logging ─────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# The * means everything after it must be passed as named arguments
# only one of the three (hours, minutes, seconds) should be passed
def calculate_wait_time(
    conn, *, api_source, account_id=None, hours=0, minutes=0, seconds=0
):
    if account_id is None:
        # activities, check all accounts
        cursor = conn.execute(
            """
            select
                max(fetched_at) fetched_at
            from last_fetched
            where api_source = ?
        """,
            (api_source,),
        )
    else:
        # orders, check by account
        cursor = conn.execute(
            """
            select
                fetched_at
            from last_fetched
            where account_id = ?
            and api_source = ?
        """,
            (account_id, api_source),
        )
    row = fetch_one_as_dict(cursor)

    # If never fetched before, no wait time is required
    if not row or not row["last_fetch"]:
        return 0, 0, 0

    current_time = datetime.now(timezone.utc)
    last_fetch_time = datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00"))
    elapsed = current_time - last_fetch_time

    total_seconds = hours * 3600 + minutes * 60 + seconds - int(elapsed.total_seconds())
    if total_seconds <= 0:
        return 0, 0, 0
    hrs = total_seconds // 3600
    mins = total_seconds % 3600 // 60
    secs = total_seconds % 3600 % 60
    return hrs, mins, secs


# Dictionary Conversion Helper Functions for Tursor
def fetch_all_as_dict(cursor):
    """Converts a fetchall() result set into a list of dictionaries."""
    if not cursor.description:
        return []
    headers = (col[0] for col in cursor.description)
    return [dict(zip(headers, row)) for row in cursor.fetchall()]


def fetch_one_as_dict(cursor):
    """Converts a fetchone() result into a dictionary."""
    row = cursor.fetchone()
    if not row or not row.description:
        return None
    headers = (col[0] for col in row.description)
    return dict(zip(headers, row))
