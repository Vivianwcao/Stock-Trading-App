import sqlite3
from datetime import datetime, timedelta, timezone
import os
import json
from xmlrpc.client import TRANSPORT_ERROR
from retrieve_data import get_accounts, get_activities
from utils import create_accounts_mapper


# date helpers
def to_api_date(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()


def x_days_ago(x):
    return (datetime.now(timezone.utc).date() - timedelta(days=x)).isoformat()


table_name = "activities"
insert_query = f"""
            insert or ignore into {table_name} (
                id, account_id, account_name, symbol, type, price, 
                units, amount, fee, currency, trade_date, API_batch_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(id) do nothing;
        """


TRANSPORT_TYPES = (
    "BUY",
    "SELL",
    "INTEREST",
    "DIVIDEND",
    "WITHDRAWAL",
    "REI",
    "STOCK_DIVIDEND",
    "FEE",
    "TAX",
    "TRANSFER",
    "SPLIT",
    "SUBSTITUTE_DIVIDEND",
    "CONTRIBUTION",
)


# one time
def init_db(conn):
    conn.executescript(f"""
        create table if not exists api_batches (
            id integer primary key,
            fetched_at text not null 
                default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
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
            trade_date text,
            API_batch_id integer references api_batches(id)
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
            API_batch_id default null
        )
    """)


def write_data(
    conn,
    is_bulk=False,
):
    get_accounts()
    create_accounts_mapper()
    with open("accounts_mapper.json", "r", encoding="utf-8") as f:
        mapper = json.load(f)

    all_records = []

    for account in mapper.items():
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
        # API fetch per WS account
        try:
            activities_list = get_activities(
                account_id, ",".join(TRANSPORT_TYPES), start_date=start_date
            )

        except Exception as e:
            print(f"API fetching on account: {account_id} failed. Error: {e}")
            return

        with conn:
            cursor = conn.exexute("insert into api_batches default values")
            batch_id = cursor.lastrowid

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
                    batch_id,
                )
            )

        with conn:
            conn.executemany(
                insert_query,
                all_records,
            )
        print("inserted all records successfully")


if __name__ == "__main__":
    # 1. Connect to local database file (creates portfolio.db automatically)
    with sqlite3.connect("stocks.db") as conn:
        # return query rows as dictionary-like objects
        conn.row_factory = sqlite3.Row
        # in SQLite, foreign_keys is a per-connection setting
        conn.execute("PRAGMA foreign_keys = ON")
        # conn.executescript("drop table activities; drop table api_batches;")
        # init_db(conn)
