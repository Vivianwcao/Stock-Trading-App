import sqlite3
from datetime import datetime, timedelta, timezone
import os
import json
from xmlrpc.client import TRANSPORT_ERROR
from retrieve_data import (
    get_accounts,
    get_activities,
    get_orders_last_24hrs,
    get_account_positions,
)
from utils import create_accounts_mapper
from snaptrade import get_snaptrade_auth


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
def init_db(conn):
    conn.executescript("""
        create table if not exists accounts (
            id text primary key, --snaptrade account_id
            account_name text not null, --tfsa-absvdfh
            account_type text not null,
            status text,
            first_transaction_date text,
            institution text,
            currency text default 'CAD',
            last_successful_sync text not null -- utc timestamp from api
        );
        create table if not exists activities (
            id text primary key,
            account_id text not null,
            symbol text not null,
            type text not null,
            price real,
            units real,
            amount real,
            fee real,
            currency text,
            trade_date text not null,
            source text not null,

            foreign key (account_id)
                references acounts(id)
            unique (trade_date, account_id, symbol, type, price, units)
        );
    """)


def update_accounts(conn):
    accounts_list = get_accounts(snaptrade)
    records = [
        (
            account["id"],
            account["number"],
            account["meta"]["type"],
            account["meta"]["status"],
            account["sync_status"]["transactions"]["first_transaction_date"],
            account["institution_name"],
            account["meta"]["currency"],
            account["sync_status"]["holdings"]["last_successful_sync"],
        )
        for account in accounts_list
    ]
    with conn:
        conn.executemany(
            """
            insert into accounts (
            id, account_name, account_type, status, first_transaction_date,
            institution, currency, last_successful_sync)
            values(?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do update set
                status = excluded.status,
                last_successful_sync = excluded.last_successful_sync;
        """,
            records,
        )
    print("Updated accounts table successfully.")


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
                snaptrade, account_id, ",".join(TRANSPORT_TYPES), start_date=start_date
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
                    activity["symbol"]["symbol"],
                    activity["type"],
                    activity["price"],
                    activity["units"],
                    activity["amount"],
                    activity["fee"],
                    activity["currency"]["code"],
                    activity["trade_date"],
                    "api_activities",
                )
            )

    with conn:
        conn.executemany(
            insert_activities_query,
            all_records,
        )
    print(f"inserted all activities records from {start_date} successfully")


# update activities with recent orders (per WS account)
def fetch_recent_orders(conn, account_id):
    all_records = []

    # from orders (real time update)
    # API fetch orders per WS account
    try:
        orders_list = get_orders_last_24hrs(snaptrade, account_id)

    except Exception as e:
        print(f"API fetching recent orders on account: {account_id} failed. Error: {e}")
        return

    for order in orders_list:
        price = float(order["execution_price"])
        qty = float(order["filled_quantity"])
        all_records.append(
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
            )
        )

    with conn:
        conn.executemany(
            insert_activities_query,
            all_records,
        )
    print("inserted all order records from last 24 hours successfully")


if __name__ == "__main__":
    snaptrade = get_snaptrade_auth()
    get_account_positions(snaptrade, "12740fda-39c7-4199-b569-c3f8ac982a6c")

    # 1. Connect to local database file (creates portfolio.db automatically)
    with sqlite3.connect("stocks.db") as conn:
        # return query rows as dictionary-like objects
        conn.row_factory = sqlite3.Row
        # in SQLite, foreign_keys is a per-connection setting
        conn.execute("PRAGMA foreign_keys = ON")
        # conn.executescript("drop table activities
        # ")
        # init_db(conn)
