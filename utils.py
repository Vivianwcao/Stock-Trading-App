import os
import logging
from datetime import datetime, date, timezone
from dotenv import load_dotenv

load_dotenv()


# ── Logging ─────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


def json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Not serializable: {type(obj)}")


def convert_utc_string_to_timestamp(str):
    """API gives '2024-01-15T10:30:00.000Z' — convert to timezone-aware datetime."""
    if not str:
        return None
    return datetime.fromisoformat(str.replace("Z", "+00:00"))


def convert_date_string_to_date(str):
    """API gives '2024-01-15' — convert to a date object."""
    if not str:
        return None
    return date.fromisoformat(str)


# result_set.columns: A tuple/list of column names (e.g., ["id", "account_name", "balance"]).
# result_set.rows: A list of tuples containing positional values (e.g., [("acc_123", "TFSA", 1500.0)], ...).
def to_dicts(result_set):
    """Converts a libsql multi-row ResultSet into a list of dictionaries."""
    return [dict(zip(result_set.columns, row)) for row in result_set.rows]


def to_dict(result_set):
    """Converts a libsql single-row ResultSet into a dictionary (or None)."""
    if not result_set.rows:
        return None
    return dict(zip(result_set.columns, result_set.rows[0]))


def time_delta_calculator(last_fetch_time: datetime, *, hours=0, minutes=0, seconds=0):
    elapsed = datetime.now(timezone.utc) - last_fetch_time

    total_seconds = hours * 3600 + minutes * 60 + seconds - int(elapsed.total_seconds())
    if total_seconds <= 0:
        return 0, 0, 0
    hrs = total_seconds // 3600
    mins = total_seconds % 3600 // 60
    secs = total_seconds % 3600 % 60
    return hrs, mins, secs


# The * means everything after it must be passed as named arguments
# only one of the three (hours, minutes, seconds) should be passed
def calculate_wait_time(
    conn,
    *,
    is_activities=False,
    account_id=None,
    hours=0,
    minutes=0,
    seconds=0,
):
    cursor = conn.cursor()
    if is_activities is False:
        # check per Snaptrade account
        cursor.execute(
            """
            select
                max(fetched_at) fetched_at
            from last_fetched
        """
        )
        latest = cursor.fetchone()
    else:
        # activities batch, check all accounts, update accounts table
        cursor.execute(
            """
            select
                max(fetched_at) fetched_at
            from last_fetched
            where api_source = 'activities'
            and account_id = %s
        """,
            (account_id,),
        )
        latest = cursor.fetchone()

    # If never fetched from API ever before, no wait time is required
    if not latest or not latest["fetched_at"]:
        return 0, 0, 0
    return time_delta_calculator(
        latest["fetched_at"], hours=hours, minutes=minutes, seconds=seconds
    )
