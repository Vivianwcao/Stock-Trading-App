import sqlite3
from datetime import datetime, timedelta, timezone
import os
import json
from xmlrpc.client import TRANSPORT_ERROR
from retrieve_data import get_accounts, get_activities, get_orders_last_24hrs
from utils import create_accounts_mapper


# date helpers
def to_api_date(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()


def x_days_ago(x):
    return (datetime.now(timezone.utc).date() - timedelta(days=x)).isoformat()


def get_insert_query(table_name, source):
    return f"""
            insert into {table_name} (
                id, account_id, account_name, symbol, type, price, 
                units, amount, fee, currency, trade_date, {source}
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id, trade_date, account_id, symbol, type, price, units) do nothing;
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
def init_db(conn):
    conn.executescript(f"""
        create table if not exists activities (
            id text primary key,
            account_id text not null,
            account_name text not null,
            symbol text not null,
            type text not null,
            price real,
            units real,
            amount real,
            fee real,
            currency text,
            trade_date text not null,
            source text not null,

            unique (trade_date, account_id, symbol, type, price, units)
        );
        create table if not exists manual_activities (            
            id text primary key,
            account_id text not null,
            account_name text not null,
            symbol text not null,
            type text not null check (type in {TRANSPORT_TYPES}),
            price real,
            units real,
            amount real not null,
            fee real defult 0.00,
            currency default 'CAD',
            trade_date text NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            source text not null,

            unique (trade_date, account_id, symbol, type, price, units)
        );
    """)


def erase_manual_activities(conn):
    # keep only non buy/sell activities with the same amount
    # remove all  buy or sell activities
    conn.execute("""
        delete from manual_activities
        where type in ('BUY', 'SELL');
    """)


def fetch_activities(conn, accounts_mapper, is_bulk):
    all_records = []
    start_date = None

    for account in accounts_mapper.items():
        account_id = account["account_id"]

        # find the latest transaction_date obtained from API
        with conn.execute(
            """
            select 
                max(trade_date) latest_date
            from activities
            where account_id = ?
        """,
            (account_id,),
        ) as cursor:
            latest_transaction_date = cursor.fetchone()["latest_date"]

        start_date = (
            None if is_bulk else (to_api_date(latest_transaction_date) or x_days_ago(2))
        )
        # API fetch activities per WS account
        try:
            activities_list = get_activities(
                account_id, ",".join(TRANSPORT_TYPES), start_date=start_date
            )

        except Exception as e:
            print(
                f"API fetching activities on account: {account_id} failed. Error: {e}"
            )
            return

        for activity in activities_list:
            all_records.append(
                (
                    activity["id"],
                    account_id,
                    account["type"],
                    activity["symbol"]["symbol"],
                    activity["type"],
                    activity["price"],
                    activity["units"],
                    activity["amount"],
                    activity["fee"],
                    activity["currency"]["code"],
                    activity["trade_date"],
                )
            )

    with conn:
        conn.executemany(
            get_insert_query("activities", "api_activities"),
            all_records,
        )
    print(f"inserted all activities records from {start_date} successfully")


# update activities with recent orders
def fetch_recent_orders(conn, accounts_mapper):
    all_records = []

    # from orders (real time update)
    for account in accounts_mapper.items():
        account_id = account["account_id"]

        # API fetch orders per WS account
        try:
            orders_list = get_orders_last_24hrs(account_id)

        except Exception as e:
            print(
                f"API fetching recent orders on account: {account_id} failed. Error: {e}"
            )
            return

        for order in orders_list:
            price = int(order["execution_price"])
            qty = int(order["filled_quantity"])
            all_records.append(
                (
                    order["brokerage_order_id"],
                    account_id,
                    account["type"],
                    order["universal_symbol"]["symbol"],
                    order["action"],
                    price,
                    qty,
                    price * qty,
                    0,
                    order["universal_symbol"]["currency"]["code"],
                    order["time_executed"],
                )
            )

    with conn:
        conn.executemany(
            get_insert_query("activities", "api_orders"),
            all_records,
        )
    print("inserted all order records from last 24 hours successfully")


def daily_update(conn):
    get_accounts()
    create_accounts_mapper()

    with open("accounts_mapper.json", "r", encoding="utf-8") as f:
        mapper = json.load(f)

    fetch_activities(conn, mapper, is_bulk=False)
    fetch_recent_orders(conn, mapper)

    erase_manual_activities(conn)


def update_with_orders(conn):
    with open("accounts_mapper.json", "r", encoding="utf-8") as f:
        mapper = json.load(f)

    fetch_recent_orders(conn, mapper)

    erase_manual_activities(conn)


if __name__ == "__main__":
    # 1. Connect to local database file (creates portfolio.db automatically)
    with sqlite3.connect("stocks.db") as conn:
        # return query rows as dictionary-like objects
        conn.row_factory = sqlite3.Row
        # in SQLite, foreign_keys is a per-connection setting
        conn.execute("PRAGMA foreign_keys = ON")
        # conn.executescript("drop table activities
        # ")
        # init_db(conn)
