from utils import to_dicts, to_dict
from update_tables import TRANSPORT_TYPES


def get_all_active_accounts(client):
    res = client.execute("""
            select *
            from accounts
            where status='open'
            and balance > 10
        """)
    return to_dicts(res)


def get_trading_activities_single_account(
    client, account_id, symbol, start_date, end_date, types=TRANSPORT_TYPES
):
    """Only buy and sell transactions, no divident, tax, interests or transfer"""
    client.execute(
        """
        WITH cleaned as (
        SELECT
            trade_date,
            symbol,
            type,
            price,
            units,
            -amount amount,
            sum(units) over(order by trade_date) current_units
        from activities
        WHERE
            account_id = '12740fda-39c7-4199-b569-c3f8ac982a6c'
            AND trade_date >= '2022-01-01'
            and type in ('BUY', 'SELL')
            AND symbol = 'BTE.TO'
        ),
        with_pre_units as (
        SELECT
            *,
            lag(current_units) over(order by trade_date) pre_units
        from cleaned 
        ),
        grouped as (
        SELECT
            *,
            count(*) filter(where pre_units is null or 
            (current_units>=0 and pre_units<0)) 
            over (order by trade_date) reset
        FROM with_pre_units
        ),
        partitioned as (
        SELECT
            *,
            round(sum(amount) over(partition by reset order by trade_date), 4) current_balance,
            sum(units) filter(where type = 'BUY') over(partition by reset order by trade_date) bought_units,
            round(sum(amount) filter(where type = 'BUY') over(partition by reset order by trade_date), 4) bought_balance,
            coalesce(round(sum(amount) filter(where type = 'BUY') over(partition by reset order by trade_date)
            /sum(units) filter(where type = 'BUY') over(partition by reset order by trade_date), 4), price) avg_bought_price,
            sum(units) filter(where type = 'SELL') over(partition by reset order by trade_date) sold_units,
            round(sum(amount) filter(where type = 'SELL') over(partition by reset order by trade_date), 4) sold_balance,
            coalesce(round(sum(amount) filter(where type = 'SELL') over(partition by reset order by trade_date)
            /sum(units) filter(where type = 'SELL') over(partition by reset order by trade_date), 4), price) avg_sold_price
        FROM grouped
        )
        SELECT
        *
        FROM partitioned
        ORDER BY
        trade_date;
        """,
        (),
    )
