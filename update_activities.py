import libsql_client
from datetime import datetime, timedelta, timezone
import logging
from retrieve_snaptrade_data import (
    get_accounts,
    get_activities,
    get_orders_last_24hrs,
    get_account_positions,
)
from utils import to_dicts, to_dict

# ── Logging ─────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


# date helpers
def to_api_date(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()


def x_days_ago(x):
    return (datetime.now(timezone.utc).date() - timedelta(days=x)).isoformat()


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


# one time
def init_db(client):
    tables = (
        """
        create table if not exists accounts (
            id text primary key, --snaptrade account_id
            account_name text not null, --tfsa-absvdfh
            nickname text, -- added custom/display nickname
            account_type text not null,
            status text,
            balance real,
            first_transaction_date text,
            institution text,
            currency text default 'CAD',
            last_successful_sync text not null -- utc timestamp from api
        );
        """,
        """
        create table if not exists activities (
            id text primary key,
            account_id text not null,
            symbol text,
            type text not null,
            price real,
            units real,
            amount real,
            fee real,
            currency text,
            trade_date text not null,
            source text not null,
            updated_at text not null
                default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

            foreign key (account_id)
                references accounts(id),
            unique (trade_date, account_id, symbol, type, price, units)
        );
        """,
        """
        create table if not exists last_fetched (
            api_source text not null,
            account_id text not null,
            fetched_at text not null
                default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

            primary key(api_source, account_id)""",
    )
    for statement in tables:
        client.execute(statement)


def update_accounts(snaptrade, client):
    accounts_list = get_accounts(snaptrade)
    statements = [
        libsql_client.Statement(
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
                account["id"],
                account["number"],
                account["meta"]["type"],
                account["meta"]["status"],
                account["balance"]["total"]["amount"],
                account["sync_status"]["transactions"]["first_transaction_date"],
                account["institution_name"],
                account["meta"]["currency"],
                account["sync_status"]["holdings"]["last_successful_sync"],
            ),
        )
        for account in accounts_list
    ]
    # Executes all statements as a single HTTP batch transaction

    if statements:
        client.batch(statements)
        logger.info("Updated accounts table successfully via HTTP batch.")


def update_last_fetched(client, api_source: str, account_id: str):
    client.execute(
        """
            insert into last_fetched (api_source, account_id) 
            values (?, ?)
            on conflict(api_source, account_id)
            do update set fetched_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (api_source, account_id),
    )


# get activities by account
def update_activities(snaptrade, client, account_id, is_bulk=False):
    start_date = None

    # find the latest transaction_date obtained from API
    res = client.execute(
        """
        select 
            max(trade_date) latest_date
        from activities
        where account_id = ?
    """,
        (account_id,),
    )
    row = to_dict(res)
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

    statements = [
        libsql_client.Statement(
            insert_activities_query,
            (
                activity["id"],
                account_id,
                (activity.get("symbol") or {}).get("symbol"),
                activity["type"],
                activity["price"],
                activity["units"],
                activity["amount"],
                activity["fee"],
                activity["currency"]["code"],
                activity["trade_date"],
                "api_activities",
            ),
        )
        for activity in activities_list
    ]
    rows_updated = 0
    if statements:
        batch_results = client.batch(statements)
        rows_updated = sum(r.rows_affected for r in batch_results)

    update_last_fetched(client, "activities", account_id)
    logger.info(
        f"Successfully synced {rows_updated} activities for account: {account_id} from {start_date}, and updated last_fetched successfully"
    )
    return rows_updated


# update activities with recent orders (per WS account)
def update_recent_orders(snaptrade, client, account_id):

    # from orders (real time update)
    # API fetch orders per WS account

    orders_list = get_orders_last_24hrs(snaptrade, account_id)

    statements = []
    for order in orders_list:
        price = float(order["execution_price"])
        qty = float(order["filled_quantity"])
        statements.append(
            libsql_client.Statement(
                insert_activities_query,
                (
                    order["brokerage_order_id"],
                    account_id,
                    order["universal_symbol"]["symbol"],
                    order["action"],
                    price,
                    qty,
                    price * qty,
                    0,
                    order["universal_symbol"]["currency"]["code"],
                    order["time_executed"],
                    "api_orders",
                ),
            )
        )
    rows_updated = 0
    if statements:
        batch_results = client.batch(statements)
        rows_updated = sum(r.rows_affected for r in batch_results)
    update_last_fetched(client, "orders", account_id)
    logger.info(
        f"Successfully synced {rows_updated} orders for account: {account_id} from last 24 hours, and updated last_fetched successfully"
    )
    return rows_updated
