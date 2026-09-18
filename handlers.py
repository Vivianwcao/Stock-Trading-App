import logging

from datetime import datetime, timedelta, timezone
import time

from queries import (
    get_all_active_accounts,
    get_all_nicknames,
    get_active_transactions,
    get_nicknames_by_ids,
    get_last_fetched,
    get_analysis,
)
from update_tables import (
    update_accounts,
    update_activities,
    update_recent_orders,
    update_positions_per_account,
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


def click_get_latest_accounts(snaptrade, conn, *, hours=0, minutes=0, seconds=0):
    hrs, mins, secs = calculate_wait_time(
        conn, is_activities=False, hours=hours, minutes=minutes, seconds=seconds
    )
    if hrs == mins == secs == 0:
        # ready tp update:
        update_accounts(snaptrade, conn)
        accounts = get_all_active_accounts(conn)
        return {
            "status": "success",
            "data": {"accounts": accounts},
        }
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def click_update_activities_by_account(
    snaptrade,
    conn,
    account_id,
    *,
    hours=0,
    minutes=0,
    seconds=0,
    activities_hours=0,
    is_bulk=False,
):
    hrs, mins, secs = calculate_wait_time(
        conn,
        is_activities=False,
        account_id=account_id,
        hours=hours,
        minutes=minutes,
        seconds=seconds,
    )
    if hrs == mins == secs == 0:
        # okay to make another API call:
        act_hrs, act_mins, act_secs = calculate_wait_time(
            conn,
            is_activities=True,
            account_id=account_id,
            hours=activities_hours,
        )
        if act_hrs == act_mins == act_secs == 0:
            # ready to update
            rows_updated = update_activities(snaptrade, conn, account_id, is_bulk)
            fetched_at = get_last_fetched(conn, "activities", account_id)
            return {
                "status": "success",
                "data": {"rows_updated": rows_updated, "fetched_at": fetched_at},
            }
        return {
            "status": "cooldown",
            "data": {"hours": act_hrs, "minutes": act_mins, "seconds": act_secs},
        }
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def click_update_orders_and_get_transactions_by_accounts(
    snaptrade,
    conn,
    account_id,
    *,
    hours=0,
    minutes=0,
    seconds=0,
):
    hrs, mins, secs = calculate_wait_time(
        conn,
        is_activities=False,
        account_id=account_id,
        hours=hours,
        minutes=minutes,
        seconds=seconds,
    )
    if hrs == mins == secs == 0:
        # ready tp update:
        rows_updated = update_recent_orders(snaptrade, conn, account_id)
        fetched_at = get_last_fetched(conn, "activities", account_id)
        transactions = get_transactions(conn, {"account_ids": [account_id]})
        return {
            "status": "success",
            "data": {
                "rows_updated": rows_updated,
                "fetched_at": fetched_at,
                "transactions": transactions,
            },
        }
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def click_update_positions_by_account_and_get_analysis_by_account(
    snaptrade,
    conn,
    account_id,
    hours=0,
    minutes=0,
    seconds=0,
):
    hrs, mins, secs = calculate_wait_time(
        conn,
        is_activities=False,
        account_id=account_id,
        hours=hours,
        minutes=minutes,
        seconds=seconds,
    )
    if hrs == mins == secs == 0:
        # ready tp update:
        update_positions_per_account(snaptrade, conn, account_id)
        rows = get_analysis(conn)
        return {"status": "success", "data": rows}
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def get_transactions(conn, data):
    # a list or tuple
    account_ids = data.get("account_ids")

    if not account_ids:
        nicknames = get_all_nicknames(conn)
    else:
        nicknames = get_nicknames_by_ids(conn, account_ids)

    nicknames_placeholder = ",".join("?" for _ in nicknames)

    start_date = data.get("start_date") or "2018-01-01"
    # use tomorrow's date if no end_date provided
    end_date = data.get("end_date") or (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    return get_active_transactions(
        conn, nicknames_placeholder, nicknames, start_date, end_date
    )


def on_page_load(conn):
    transactions = get_transactions(conn, {})
    analysis = get_analysis(conn)
    accounts = get_all_active_accounts(conn)
    rows = conn.execute("select * from last_fetched").fetchall()
    last_fetched = [dict(row) for row in rows]
    return {
        "status": "success",
        "data": {
            "transactions": transactions,
            "analysis": analysis,
            "accounts": accounts,
            "last_fetched": last_fetched,
        },
    }
