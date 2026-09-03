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
                sum(units) over(partition by symbol order by trade_date) rolling_units
            from activities
            WHERE
                account_id = '8bbc2e4f-feef-457d-b9a7-476a28f9fbc8'
                AND trade_date >= '2022-01-01'
                and type in ('BUY', 'SELL')
                -- AND symbol = 'BCE.TO'
            ),
            with_pre_rolling_units as (
            SELECT
                *,
                lag(rolling_units) over(partition by symbol order by trade_date) pre_rolling_units
            from cleaned 
            ),
            grouped as (
            SELECT
                *,
                count(*) filter(where pre_rolling_units is null or 
                (rolling_units>=0 and pre_rolling_units<0)) 
                over (partition by symbol order by trade_date) reset
            FROM with_pre_rolling_units
            ),
            partitioned as (
            SELECT
                *,
                round(sum(amount) over(partition by symbol, reset order by trade_date), 2) current_balance,
                sum(units) filter(where type = 'BUY') over(partition by symbol, reset order by trade_date) bought_units,
                round(sum(amount) filter(where type = 'BUY') over(partition by symbol, reset order by trade_date), 2) bought_balance,
                round(sum(amount) filter(where type = 'BUY') over(partition by symbol, reset order by trade_date)
                /sum(units) filter(where type = 'BUY') over(partition by symbol, reset order by trade_date), 2) avg_bought_price,
                sum(units) filter(where type = 'SELL') over(partition by symbol, reset order by trade_date) sold_units,
                round(sum(amount) filter(where type = 'SELL') over(partition by symbol, reset order by trade_date), 2) sold_balance,
                round(sum(amount) filter(where type = 'SELL') over(partition by symbol, reset order by trade_date)
                /sum(units) filter(where type = 'SELL') over(partition by symbol, reset order by trade_date), 2) avg_sold_price
            FROM grouped
            )
            SELECT
            *
            FROM partitioned;
        """,
        (),
    )
