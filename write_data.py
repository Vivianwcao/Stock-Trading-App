import sqlite3
from datetime import date, datetime
import os
import json
from retrieve_data import get_accounts, get_activities
from utils import create_accounts_mapper

default_start_date = date(2022, 1, 1)


def init_db(conn):
    conn.execute("""
        create table if not exists activities (
            id integer primary key,
            account_name text not null,
            account_id text not null,
            stock_name text not null,
            stock_symbol text not null,
            type text not null,
            price real,
            units real,
            amount real,
            fee real,
            trade_date text,
            is_manual integer default 0
        )
    """)


def write_data(
    start_date,
    end_date,
    conn,
):
    get_accounts()
    create_accounts_mapper()
    with open("accounts_mapper.json", "r", encoding="utf-8") as f:
        mapper = json.load(f)

    for account in mapper.items():
        activities_list = get_activities(
            account["account_id"],
            account.get("first_transaction_date", default_start_date),
        )


if __name__ == "__main__":
    # 1. Connect to local database file (creates portfolio.db automatically)
    with sqlite3.connect("stocks.db") as conn:
        init_db(conn)
