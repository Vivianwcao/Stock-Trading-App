import logging

from datetime import datetime, timedelta, timezone
import time

from queries import (
    get_all_active_accounts,
    get_all_nicknames,
    get_transactions_all_accounts,
    get_transactions_by_nickname,
    get_last_fetched,
    get_latest_analysis_all_accounts,
    get_latest_analysis_by_account,
    get_snapshot_dates_all_accounts,
    get_snapshot_dates_by_account,
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
        res = update_accounts(snaptrade, conn)

        accounts = get_all_active_accounts(conn)
        response = {
            "status": "success",
            "data": {"accounts": accounts},
        }
        if res.get("status") == "fail":
            response["data"]["snaptrade_response"] = res.get("error")
        return response
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
            res = update_activities(snaptrade, conn, account_id, is_bulk)

            if res.get("status") == "fail":
                return res

            fetched_at = get_last_fetched(conn, "activities", account_id)
            return {
                "status": "success",
                "data": {"rows_updated": res.get("data"), "fetched_at": fetched_at},
            }
        return {
            "status": "cooldown",
            "data": {"hours": act_hrs, "minutes": act_mins, "seconds": act_secs},
        }
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def click_update_orders_and_get_transactions_by_account(
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
        res = update_recent_orders(snaptrade, conn, account_id)

        if res.get("status") == "fail":
            return res

        fetched_at = get_last_fetched(conn, "orders", account_id)
        row = conn.execute(
            "select nickname from accounts where id = ?", (account_id,)
        ).fetchone()
        nickname = row["nickname"] if row else None
        transactions = get_transactions_by_nickname(conn, nickname) if nickname else []
        return {
            "status": "success",
            "data": {
                "rows_updated": res.get("data"),
                "fetched_at": fetched_at,
                "transactions": transactions,
            },
        }
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def trigger_update_positions_bulk(snaptrade, conn, trigger):
    accounts = get_all_active_accounts(conn)
    for account in accounts:
        account_id = account["id"]
        update_positions_per_account(snaptrade, conn, account_id, trigger)
        logger.info(f"Successfully updated positions for account: {account_id}.")
        time.sleep(30)


def click_update_positions_and_get_latest_analysis_by_account(
    snaptrade,
    conn,
    account_id,
    trigger,
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
        res = update_positions_per_account(snaptrade, conn, account_id, trigger)

        if res.get("status") == "fail":
            return res
        sync_dates = get_snapshot_dates_by_account(conn, account_id)
        analysis = get_latest_analysis_by_account(conn, account_id)
        return {
            "status": "success",
            "data": {"sync_dates": sync_dates, "analysis": analysis},
        }
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def on_page_load(conn):
    transactions = get_transactions_all_accounts(conn)
    snapshots = get_snapshot_dates_all_accounts(conn)
    analysis = get_latest_analysis_all_accounts(conn)
    accounts = get_all_active_accounts(conn)
    rows = conn.execute("select * from last_fetched").fetchall()
    last_fetched = [dict(row) for row in rows]
    return {
        "status": "success",
        "data": {
            "transactions": transactions,
            "snapshots": snapshots,
            "analysis": analysis,
            "accounts": accounts,
            "last_fetched": last_fetched,
        },
    }
