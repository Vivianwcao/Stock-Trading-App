from utils import to_dicts, to_dict
from update_tables import TRANSPORT_TYPES


def get_all_active_accounts(client):
    res = client.execute("""
            select *
            from accounts
            where status='open'
            # and balance > 10
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
            account_id,
            trade_date,
            symbol,
            type,
            price,
            units,
            amount,
            -- for reset cycles
            sum(units) over(partition by symbol order by trade_date) rolling_units
        from activities
        WHERE
            account_id in ('b954f136-f892-429e-9a4f-103f0d723ff8', '12740fda-39c7-4199-b569-c3f8ac982a6c')
            AND trade_date >= '2022-01-01'
            and type in ('BUY', 'SELL', 'DIVIDEND')
            -- AND symbol = 'BCE.TO'
        ),
        with_pres as (
        SELECT
            *,
            lag(type) over(partition by symbol order by trade_date) pre_type,
            (
            SELECT
                type
            from cleaned
            where symbol = c.symbol
                and trade_date < c.trade_date
                and type <> 'DIVIDEND'
            order by trade_date DESC
            limit 1
            ) pre_trade_type,
            lag(rolling_units) over(partition by symbol order by trade_date) pre_rolling_units
        from cleaned c
        ),
        sell_all_grouped as (
        SELECT
            *,
            count(*) filter(where 
            (pre_trade_type = 'SELL' and type = 'BUY' and pre_rolling_units<=0)) 
            over (partition by symbol order by trade_date) reset
        FROM with_pres
        ),
        sell_grouped as (
        select
            *,
            count(*) filter(where pre_type='SELL') over(partition by symbol, reset order by trade_date) sell_reset
        from sell_all_grouped
        ),
        partitioned as (
        SELECT
            *,
            sum(units) filter(where type = 'BUY') over(partition by symbol, reset order by trade_date) bought_units,
            round(sum(amount) filter(where type = 'BUY') over(partition by symbol, reset order by trade_date), 2) bought_balance,
            round(sum(amount) filter(where type = 'BUY') over(partition by symbol, reset order by trade_date)
            /sum(units) filter(where type = 'BUY') over(partition by symbol, reset order by trade_date), 3) avg_bought_price,
            sum(amount) filter(where type = 'DIVIDEND') over(partition by symbol, reset, sell_reset order by trade_date) divident_balance
        FROM sell_grouped
        )
        SELECT
        account_id,
        trade_date,
        symbol,
        type,
        price,
        units,
        amount,
        reset,
        sell_reset,
        rolling_units,
        avg_bought_price,
        divident_balance,
        case when type = 'SELL' then round((price + avg_bought_price)/-avg_bought_price, 4) end 盈亏率 
        FROM partitioned;
        """,
        (),
    )
