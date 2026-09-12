import sqlite3
import duckdb
from update_tables import insert_activities_query


get_cutoff_dates_query = """
        SELECT
            acc.id,
            wealth_simple_account_id,
            substr(min(trade_date), 1, 19) || '.000000Z' cutoff
        from accounts acc
        left join activities act
            on acc.id = act.account_id
        where wealth_simple_account_id is not null
        group by wealth_simple_account_id
    """

# Wealth simple csv uses QQU, where SnapTrade API uses HQU
import_csv_query_initial_batch = """
    select
        uuid()::varchar,
        c.account_id,
        case 
            when starts_with(symbol, 'QQU') then 'HQU'
            when ends_with(symbol, '.TO') 
            then left(symbol, length(symbol) - 3)
            else symbol
        end,
        case 
            when activity_type = 'Trade' then activity_sub_type
            else upper(activity_type)
        end,
        coalesce(unit_price, 0)::double,
        case
            when activity_type = 'Trade' then quantity
            when activity_type = 'InternalSecurityTransfer' then quantity
            when activity_type like '%CorporateAction%' then quantity
            else 0
        end::double,
        coalesce(net_cash_amount, 0)::double,
        coalesce(commission, 0)::double,
        currency,
        strftime(
            timezone(
                'UTC', 
                timezone(
                    'America/Vancouver', 
                    (effective_date::varchar || ' ' || effective_time::varchar)::timestamp
                )
            ),
            '%Y-%m-%dT%H:%M:%S.%fZ'
        ) trade_date,
        'wealth_simple_csv'
    from read_csv_auto('activities.csv', header=True) csv
    join cutoffs c
        on csv.account_id = c.wealth_simple_account_id
"""

import_csv_query = import_csv_query_initial_batch + " where trade_date < cutoff"


# DuckDB check - there could be exact identical rows in the csv. we need to keep them all
def check_csv_identical_rows(ddb_conn):
    return ddb_conn.sql("""
        select  
            effective_date, 
            effective_time, 
            account_id, 
            activity_type, 
            activity_sub_type,
            symbol,
            quantity,
            unit_price,
            net_cash_amount,
            count(*) cnt
        from read_csv_auto('activities.csv', header=True)
        group by 
            effective_date, 
            effective_time, 
            account_id, 
            activity_type, 
            activity_sub_type,
            symbol,
            quantity,
            unit_price,
            net_cash_amount
            having count(*)>1
    """)


def import_csv(sql_conn, ddb_conn, is_initial_batch=True):

    # 1. Fetch cutoffs from sql
    with sql_conn:
        cutoffs = sql_conn.execute(get_cutoff_dates_query).fetchall()
    # 2. Create temporary mapping table in duckDB
    ddb_conn.execute("""
        create temp table cutoffs (
            account_id varchar,
            wealth_simple_account_id varchar,
            cutoff varchar
        )
    """)

    # 3. Insert sql cutoffs into DuckDB
    ddb_conn.executemany("insert into cutoffs values (?, ?, ?)", cutoffs)

    if is_initial_batch:
        # Query CSV joined with Cutoffs with no end dates
        rows = ddb_conn.execute(import_csv_query_initial_batch).fetchall()
    else:
        # 4. Query CSV joined with Cutoffs
        rows = ddb_conn.execute(import_csv_query).fetchall()

    with sql_conn:
        row_count = sql_conn.executemany(insert_activities_query, rows).rowcount
    print(f"Successfully synced {row_count} activities in total.")


if __name__ == "__main__":
    ddb_conn = duckdb.connect()
    sql_conn = sqlite3.connect("stocks.db")
    sql_conn.execute("PRAGMA foreign_keys = ON")

    import_csv(sql_conn, ddb_conn, True)

    # check_csv_identical_rows(ddb_conn).show()

    ddb_conn.close()
    sql_conn.close()
