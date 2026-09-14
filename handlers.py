import logging

from datetime import datetime, timedelta, timezone

from queries import (
    get_all_active_accounts,
    get_all_nicknames,
    get_active_transactions,
    get_accounts_balance_by_nickname,
    get_nicknames_by_ids,
    get_last_fetched,
)
from update_tables import (
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


def click_update_all_activities(snaptrade, conn, hours=4, is_bulk=False):
    hrs, mins, secs = calculate_wait_time(conn, api_source="activities", hours=hours)
    if hrs == mins == secs == 0:
        # ready tp update:
        update_accounts(snaptrade, conn)

        accounts = get_all_active_accounts(conn)
        if not accounts:
            return {"status": "fail", "error": "No active accounts found"}

        account_ids = {account["id"]: {} for account in accounts}
        for account_id, info in account_ids.items():
            try:
                hrs, mins, secs = calculate_wait_time(
                    conn, api_source="activities", account_id=account_id, hours=hours
                )
                if hrs == mins == secs == 0:
                    # ready tp update:

                    rows_updated = update_activities(
                        snaptrade, conn, account_id, is_bulk
                    )
                    info["status"] = "success"
                    info["data"] = {"rows_updated": rows_updated}
                else:
                    info["status"] = "cooldown"
                    info["data"] = {"hours": hrs, "minutes": mins, "seconds": secs}
            except Exception as e:
                logger.exception(
                    f"Failed to sync account: {account_id}. Continuing to next account."
                )
                info["status"] = "fail"
                info["error"] = f"{type(e).__name__}: {str(e)}"

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
        rows_updated = update_recent_orders(snaptrade, conn, account_id)
        return {"status": "success", "data": {"rows_updated": rows_updated}}
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def get_transactions_and_balances(conn, data):
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

    transactions = get_active_transactions(
        conn, nicknames_placeholder, nicknames, start_date, end_date
    )
    balances = get_accounts_balance_by_nickname(conn, nicknames_placeholder, nicknames)
    last_fetched_timestamps = get_last_fetched(conn)
    return {
        "status": "success",
        "transactions": transactions,
        "accounts_balance": balances,
        "last_fetched": last_fetched_timestamps,
    }
