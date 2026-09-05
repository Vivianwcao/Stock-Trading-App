import json
import logging

from queries import init_db, get_all_active_accounts
from update_tables import (
    update_account_nickname,
    update_accounts,
    update_activities,
    update_recent_orders,
)
from utils import calculate_wait_time, to_dicts

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


def create_tables(client):
    client.executescript(
        "drop table activities; drop table accounts; drop table last_fetched;"
    )
    init_db(client)  # run once


def click_update_all_activities(snaptrade, client, hours=4, is_bulk=False):
    hrs, mins, secs = calculate_wait_time(client, api_source="activities", hours=hours)
    if hrs == mins == secs == 0:
        # ready tp update:
        update_accounts(snaptrade, client)

        accounts = get_all_active_accounts(client)
        if not accounts:
            return {"status": "fail", "error": "No active accounts found"}

        account_ids = {account["id"]: {} for account in accounts}
        for account_id, info in account_ids.items():
            try:
                hrs, mins, secs = calculate_wait_time(
                    client, api_source="activities", account_id=account_id, hours=hours
                )
                if hrs == mins == secs == 0:
                    # ready tp update:

                    rows_updated = update_activities(
                        snaptrade, client, account_id, is_bulk
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


def click_update_orders_by_account(snaptrade, client, account_id, seconds=30):
    hrs, mins, secs = calculate_wait_time(
        client, api_source="orders", account_id=account_id, seconds=seconds
    )
    if hrs == mins == secs == 0:
        # ready tp update:
        rows_updated = update_recent_orders(snaptrade, client, account_id)
        return {"status": "success", "data": {"rows_updated": rows_updated}}
    return {
        "status": "cooldown",
        "data": {"hours": hrs, "minutes": mins, "seconds": secs},
    }


def click_update_nickname(client, account_id: str, nickname: str | None):
    try:
        updated_name = update_account_nickname(client, account_id, nickname)
        return {
            "status": "success",
            "data": {"account_id": account_id, "nickname": updated_name},
        }
    except Exception as e:
        logger.exception(f"Failed to update nickname for account {account_id}")
        return {"status": "fail", "error": f"{type(e).__name__}: {str(e)}"}
