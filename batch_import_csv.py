from utils import get_turso_client
import libsql_client
import duckdb
from update_tables import insert_activities_query


def get_cutoff_date(turso_client):
    res = turso_client.execute(
        """
        SELECT
            account_id,
            wealth_simple_account_id,
            substr(min(trade_date), 1, 19) || '.000000Z' cutoff
        from accounts acc
        join activities act
            on acc.id = act.account_id
        where wealth_simple_account_id is not null
        group by wealth_simple_account_id
    """
    )
    return res.rows


def extract_from_csv(turso_client, conn):
    # 1. Fetch cutoffs from Turso
    cutoffs = get_cutoff_date(turso_client)

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
    res = conn.execute("""
        select
            uuid()::varchar,
            c.account_id,
            case 
                when ends_with(symbol, '.TO') 
                then left(symbol, length(symbol) - 3)
                else symbol
            end,
            activity_sub_type,
            unit_price::double,
            quantity::double,
            net_cash_amount::double,
            commission::double,
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
    """).fetchall()

    return res


def insert_activities(turso_client, conn, batch_size=250):
    res = extract_from_csv(turso_client, conn)
    statements = [libsql_client.Statement(insert_activities_query, row) for row in res]

    rows_updated = 0
    for i in range(0, len(res), batch_size):
        chunk = statements[i : i + batch_size]
        batch_results = turso_client.batch(chunk)
        rows_updated += sum(r.rows_affected for r in batch_results)
        print(f"Batch: {i}: Successfully synced {rows_updated} activities.")

    print(f"Successfully synced {rows_updated} activities in total.")


if __name__ == "__main__":
    conn = duckdb.connect()
    client = get_turso_client()

    insert_activities(client, conn)

    conn.close()
    client.close()
