from utils import to_dicts
import csv
import duckdb


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
    return to_dicts(res)


def extract_from_csv():
    duckdb.execute("""
    select
        uuid() id,
        account_id wealth_simple_account_id,
        strftime(
            timezone(
                'UTC', 
                timezone(
                    'America/Vancouver', 
                    (effective_date || ' ' || effective_time)::timestamp
                )
            ),
            '%Y-%m-%dT%H:%M:%S.%fZ'
        ) as utc_time,

    from read_csv_auto('activities.csv', header=True, delimiter=',')
    """)
