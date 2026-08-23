import sqlite3
from datetime import datetime, timedelta, timezone
import os
import json
from retrieve_data import get_accounts, get_activities
from utils import create_accounts_mapper


# date helpers
def to_api_date(timestamp: str) -> str:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()


def x_days_ago(x):
    return (datetime.now(timezone.utc).date() - timedelta(days=x)).isoformat()


def init_db(conn):
    conn.execute("""
        create table if not exists activities (
            id text primary key,
            account_id text not null,
            account_name text not null,
            stock_name text not null,
            stock_symbol text not null,
            type text not null,
            price real,
            units real,
            amount real,
            fee real,
            currency text,
            trade_date text,
            is_manual integer default 0
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
            and is_manual = 0
        """,
            (account_id,),
        ) as cursor:
            latest_transaction_date = cursor.fetchone()["latest_date"]

        start_date = (
            None if is_bulk else (to_api_date(latest_transaction_date) or x_days_ago(2))
        )

        activities_list = get_activities(account_id, start_date=start_date)

        for activity in activities_list:
            symbol = activity["symbol"]
            all_records.append(
                (
                    activity["id"],
                    account_id,
                    account["type"],
                    symbol["symbol"],
                    symbol["raw_symbol"],
                    activity["type"],
                    activity["price"],
                    activity["units"],
                    activity["amount"],
                    activity["fee"],
                    activity["currency"]["code"],
                    activity["trade_date"],
                    0,  # is_manual flag
                )
            )

        with conn:
            conn.executemany(
                """
                insert or replace into activities (
                    id, account_id, account_name, stock_name, 
                    stock_symbol, type, price, units, amount, 
                    fee, currency, trade_date, is_manual
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                all_records,
            )
        print("inserted all records successfully")


if __name__ == "__main__":
    # 1. Connect to local database file (creates portfolio.db automatically)
    with sqlite3.connect("stocks.db") as conn:
        init_db(conn)
