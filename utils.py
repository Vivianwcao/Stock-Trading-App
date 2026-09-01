import json
import logging
from datetime import datetime, timezone


# ── Logging ─────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# The * means everything after it must be passed as named arguments
# only one of the three (hours, minutes, seconds) should be passed
def calculate_wait_time(
    client, *, api_source, account_id=None, hours=0, minutes=0, seconds=0
):
    if account_id is None:
        # activities, check all accounts
        res = client.execute(
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
        res = client.execute(
            """
            select
                fetched_at
            from last_fetched
            where account_id = ?
            and api_source = ?
        """,
            (account_id, api_source),
        )
    row = to_dict(res)

    # If never fetched before, no wait time is required
    if not row or not row["fetched_at"]:
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


# result_set.columns: A tuple/list of column names (e.g., ["id", "account_name", "balance"]).
# result_set.rows: A list of tuples containing positional values (e.g., [("acc_123", "TFSA", 1500.0)], ...).
def to_dicts(result_set):
    """Converts a multi-row ResultSet into a list of dictionaries."""
    return [dict(zip(result_set.columns, row)) for row in result_set.rows]


def to_dict(result_set):
    """Converts a single-row ResultSet into a dictionary (or None)."""
    if not result_set.rows:
        return None
    return dict(zip(result_set.columns, result_set.rows[0]))
