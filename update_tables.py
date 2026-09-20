from datetime import datetime
import logging
from retrieve_snaptrade_data import (
    get_accounts,
    get_activities,
    get_orders_last_24hrs,
    get_account_positions,
)
from utils import x_days_ago
import json
from snaptrade_client.exceptions import ApiException

# ── Logging ─────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# date helpers
def to_api_date(timestamp: str) -> str:
    if not timestamp:
        return None
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()


insert_activities_query = """
            insert or ignore into activities (
                id, account_id, symbol, type, price, 
                units, amount, fee, currency, trade_date, source
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """


# Snaptrade orders only includes buy and sell.
TRANSPORT_TYPES = (
    "BUY",
    "SELL",
    "DIVIDEND",
    "SUBSTITUTE_DIVIDEND",
    "CONTRIBUTION",
    "WITHDRAWAL",
    "REI",
    "STOCK_DIVIDEND",
    "INTEREST",
    "FEE",
    "TAX",
    "OPTIONEXPIRATION",
    "OPTIONASSIGNMENT",
    "OPTIONEXERCISE",
    "TRANSFER",
    "SPLIT",
)


def update_accounts(snaptrade, conn):
    try:
        accounts = get_accounts(snaptrade)
    except ApiException as e:
        try:
            body = e.body if isinstance(e.body, dict) else json.loads(e.body)
            error = body.get("detail") or body.get("error") or str(e)
        except (json.JSONDecodeError, TypeError):
            error = str(e)
        return {"status": "fail", "error": error}

    with conn:
        conn.executemany(
            """
                insert into accounts (
                    id, account_name, account_type, status, balance, 
                    first_transaction_date, institution, currency, last_successful_sync
                )
                values(?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                    status = excluded.status,
                    balance = excluded.balance,
                    last_successful_sync = excluded.last_successful_sync;
                """,
            [
                (
                    account["id"],  # type: ignore
                    account["number"],
                    account["meta"]["type"],
                    account["meta"]["status"],
                    account["balance"]["total"]["amount"],
                    account["sync_status"]["transactions"]["first_transaction_date"],
                    account["institution_name"],
                    account["meta"]["currency"],
                    account["sync_status"]["holdings"]["last_successful_sync"],
                )
                for account in accounts
            ],
        )
    logger.info("Updated accounts table successfully via HTTP batch.")
    return {"status": "success"}


def update_last_fetched(conn, api_source: str, account_id: str):
    conn.execute(
        """
            insert into last_fetched (api_source, account_id) 
            values (?, ?)
            on conflict(api_source, account_id)
            do update set fetched_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (api_source, account_id),
    )


# get activities by account
def update_activities(snaptrade, conn, account_id, is_bulk=False):
    latest_transaction_date = None
    cursor = conn.cursor()
    if not is_bulk:
        # find the latest transaction_date obtained from API
        row = cursor.execute(
            """
            select 
                max(trade_date) latest_date
            from activities
            where account_id = ?
        """,
            (account_id,),
        ).fetchone()
        latest_transaction_date = row["latest_date"] if row else None

    start_date = (
        None
        if is_bulk or not latest_transaction_date
        else (to_api_date(latest_transaction_date) or x_days_ago(2))
    )
    # API fetch activities per WS account
    try:
        activities = get_activities(
            snaptrade, account_id, ",".join(TRANSPORT_TYPES), start_date=start_date
        )["data"]
    except ApiException as e:
        try:
            body = e.body if isinstance(e.body, dict) else json.loads(e.body)
            error = body.get("detail") or body.get("error") or str(e)
        except (json.JSONDecodeError, TypeError):
            error = str(e)
        return {"status": "fail", "error": error}

    with conn:
        cursor.executemany(
            insert_activities_query,
            [
                (
                    activity["id"],
                    account_id,
                    (activity.get("symbol") or {}).get("raw_symbol"),
                    activity["type"],
                    activity["price"],
                    activity["units"],
                    activity["amount"],
                    activity["fee"],
                    activity["currency"]["code"],
                    activity["trade_date"],
                    "api_activities",
                )
                for activity in activities
            ],
        )
        update_last_fetched(conn, "activities", account_id)
    logger.info(
        f"Successfully synced {cursor.rowcount} activities for account: {account_id} from {start_date}, and updated last_fetched successfully"
    )
    return {"status": "success", "data": cursor.rowcount}


# update activities with recent orders (per WS account)
def update_recent_orders(snaptrade, conn, account_id):

    # from orders (real time update)
    # API fetch orders per WS account

    try:
        orders = get_orders_last_24hrs(snaptrade, account_id)["orders"]
    except ApiException as e:
        try:
            body = e.body if isinstance(e.body, dict) else json.loads(e.body)
            error = body.get("detail") or body.get("error") or str(e)
        except (json.JSONDecodeError, TypeError):
            error = str(e)
        return {"status": "fail", "error": error}

    records = []
    for order in orders:
        type = order["action"]
        price = float(order["execution_price"])
        qty = float(order["filled_quantity"])

        if type == "SELL":
            qty *= -1
        amount = price * qty

        if type == "BUY":
            amount *= -1
        records.append(
            (
                order["brokerage_order_id"],
                account_id,
                order["universal_symbol"]["raw_symbol"],
                type,
                price,
                qty,
                amount,
                0,
                order["universal_symbol"]["currency"]["code"],
                order["time_executed"],
                "api_orders",
            )
        )
    with conn:
        row_count = conn.executemany(
            insert_activities_query,
            records,
        ).rowcount

        update_last_fetched(conn, "orders", account_id)
    logger.info(
        f"Successfully synced {row_count} orders for account: {account_id} from last 24 hours, and updated last_fetched successfully"
    )
    return {"status": "success", "data": row_count}


def update_positions_per_account(snaptrade, conn, account_id, trigger):
    try:
        res = get_account_positions(snaptrade, account_id)

    except ApiException as e:
        try:
            body = e.body if isinstance(e.body, dict) else json.loads(e.body)
            error = body.get("detail") or body.get("error") or str(e)
        except (json.JSONDecodeError, TypeError):
            error = str(e)
        return {"status": "fail", "error": error}

    positions, sync_date = res.values()
    cursor = conn.cursor()
    with conn:
        cursor.executemany(
            """
            insert into positions (account_id, symbol, holdings, 
            current_price, cost_basis, trigger, last_successful_sync
            )
            values(?, ?, ?, ?, ?, ?, ?)
        """,
            [
                (
                    account_id,
                    position["instrument"]["raw_symbol"],
                    round(float(position["units"]), 4),
                    round(float(position["price"]), 4),
                    round(float(position["cost_basis"]), 4),
                    trigger,
                    sync_date["as_of"],
                )
                for position in positions
            ],
        )
        update_last_fetched(conn, "positions", account_id)

    return {"status": "success"}


def update_account_nickname(conn, account_id: str, nickname: str | None):
    clean_nickname = nickname.strip() or None if nickname else None
    with conn:
        conn.execute(
            """
                update accounts
                set nickname = ?
                where id = ?
                """,
            (clean_nickname, account_id),
        )
    return {
        "status": "success",
        "data": {"account_id": account_id, "nickname": clean_nickname},
    }


def update_wealth_simple_account_id(conn, account_id: str, ws_account_id: str | None):
    clean_ws_account_id = ws_account_id.strip() or None if ws_account_id else None
    with conn:
        conn.execute(
            """
            update accounts
            set wealth_simple_account_id = ?
            where id = ?
            """,
            (clean_ws_account_id, account_id),
        )
    logger.info(
        "Updated wealth_simple_account_id: %s for account: %s.",
        clean_ws_account_id,
        account_id,
    )
    return clean_ws_account_id
