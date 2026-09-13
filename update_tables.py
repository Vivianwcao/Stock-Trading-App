from datetime import datetime
import logging
from retrieve_snaptrade_data import (
    get_accounts,
    get_activities,
    get_orders_last_24hrs,
    get_account_positions,
)
from utils import x_days_ago

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
    accounts_list = get_accounts(snaptrade)
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
            (
                (
                    account["id"],
                    account["number"],
                    account["meta"]["type"],
                    account["meta"]["status"],
                    account["balance"]["total"]["amount"],
                    account["sync_status"]["transactions"]["first_transaction_date"],
                    account["institution_name"],
                    account["meta"]["currency"],
                    account["sync_status"]["holdings"]["last_successful_sync"],
                )
                for account in accounts_list
            ),
        )
    logger.info("Updated accounts table successfully via HTTP batch.")


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
    activities_list = get_activities(
        snaptrade, account_id, ",".join(TRANSPORT_TYPES), start_date=start_date
    )
    with conn:
        cursor.executemany(
            insert_activities_query,
            (
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
                for activity in activities_list
            ),
        )
        update_last_fetched(conn, "activities", account_id)
    logger.info(
        f"Successfully synced {cursor.rowcount} activities for account: {account_id} from {start_date}, and updated last_fetched successfully"
    )
    return cursor.rowcount


# update activities with recent orders (per WS account)
def update_recent_orders(snaptrade, conn, account_id):

    # from orders (real time update)
    # API fetch orders per WS account

    orders_list = get_orders_last_24hrs(snaptrade, account_id)

    records = []
    for order in orders_list:
        price = float(order["execution_price"])
        qty = float(order["filled_quantity"])

        records.append(
            (
                order["brokerage_order_id"],
                account_id,
                order["universal_symbol"]["raw_symbol"],
                order["action"],
                price,
                qty,
                price * qty,
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
    return row_count


def update_account_nickname(conn, account_id: str, nickname: str | None):
    try:
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
    except Exception as e:
        logger.exception(f"Failed to update nickname for account {account_id}")
        return {"status": "fail", "error": f"{type(e).__name__}: {str(e)}"}


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
