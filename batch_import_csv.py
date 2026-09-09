from utils import get_turso_client
import libsql_client
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

import_csv_query = """
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
        where trade_date < cutoff
    """


# **only batch import csv after activities is filled with API data
def batch_import_csv(turso_client, conn, batch_size=250):
    # 1. Fetch cutoffs from Turso
    res = turso_client.execute(get_cutoff_dates_query)
    cutoffs = res.rows

    # 2. Create temporary mapping table
    conn.execute("""
        create temp table cutoffs (
            account_id varchar,
            wealth_simple_account_id varchar,
            cutoff varchar
        )
    """)

    # 3. Insert Turso cutoffs into DuckDB
    conn.executemany("insert into cutoffs values (?, ?, ?)", cutoffs)

    # 4. Query CSV joined with Cutoffs
    res = conn.execute(import_csv_query).fetchall()

    # Write into activities
    statements = [libsql_client.Statement(insert_activities_query, row) for row in res]

    rows_updated = 0
    for i in range(0, len(res), batch_size):
        chunk = statements[i : i + batch_size]
        batch_results = turso_client.batch(chunk)
        rs_updated = sum(r.rows_affected for r in batch_results)
        rows_updated += rs_updated
        print(f"Batch: {i}: Successfully synced {rs_updated} activities.")

    print(f"Successfully synced {rows_updated} activities in total.")


if __name__ == "__main__":
    conn = duckdb.connect()
    client = get_turso_client()

    batch_import_csv(client, conn)

    conn.close()
    client.close()
