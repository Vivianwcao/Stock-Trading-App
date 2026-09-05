from utils import to_dicts, to_dict
from update_tables import TRANSPORT_TYPES


# one time
def init_db(client):
    tables = (
        """
        create table if not exists accounts (
            id text primary key, --snaptrade account_id
            account_name text not null, --tfsa-absvdfh
            nickname text, -- added custom/display nickname
            account_type text not null,
            status text,
            balance real,
            first_transaction_date text,
            institution text,
            currency text default 'CAD',
            last_successful_sync text not null -- utc timestamp from api
        );
        """,
        """
        create table if not exists activities (
            id text primary key,
            account_id text not null,
            symbol text,
            type text not null,
            price real,
            units real,
            amount real,
            fee real,
            currency text,
            trade_date text not null,
            source text not null,
            updated_at text not null
                default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

            foreign key (account_id)
                references accounts(id),
            unique (trade_date, account_id, symbol, type, price, units)
        );
        """,
        """
        create table if not exists last_fetched (
            api_source text not null,
            account_id text not null,
            fetched_at text not null
                default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

            primary key(api_source, account_id)""",
    )
    # for statement in tables:
    #     client.execute(statement)

    # 2. Performance Index
    client.execute("""
        create index if not exists idx_transactions
        on activities(account_id, symbol, trade_date);
        """)

    # 3. View Creation
    # Only buy and sell transactions, no divident, tax, interests or transfer
    client.execute(
        """
        create view if not exists transactions as
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
            sum(units) over(partition by account_id, symbol order by trade_date) rolling_units
        from activities
        WHERE type in ('BUY', 'SELL', 'DIVIDEND')
        ),
        with_pres as (
        SELECT
            *,
            lag(type) over(partition by account_id, symbol order by trade_date) pre_type,
            (
            SELECT
                type
            from cleaned
            where account_id = c.account_id
                and symbol = c.symbol
                and trade_date < c.trade_date
                and type <> 'DIVIDEND'
            order by trade_date DESC
            limit 1
            ) pre_trade_type,
            lag(rolling_units) over(partition by account_id, symbol order by trade_date) pre_rolling_units
        from cleaned c
        ),
        sell_all_grouped as (
        SELECT
            *,
            count(*) filter(where 
            (pre_trade_type = 'SELL' and type = 'BUY' and pre_rolling_units<=0)) 
            over (partition by account_id, symbol order by trade_date) cycles
        FROM with_pres
        ),
        sell_grouped as (
        select
            *,
            count(*) filter(where pre_type='SELL') over(partition by account_id, symbol, cycles order by trade_date) sell_cycles
        from sell_all_grouped
        ),
        partitioned as (
        SELECT
            *,
            sum(units) filter(where type = 'BUY') over(partition by account_id, symbol, cycles order by trade_date) bought_units,
            sum(amount) filter(where type = 'BUY') over(partition by account_id, symbol, cycles order by trade_date) bought_balance,
            sum(amount) filter(where type = 'BUY') over(partition by account_id, symbol, cycles order by trade_date)
            /sum(units) filter(where type = 'BUY') over(partition by account_id, symbol, cycles order by trade_date) avg_bought_price,
            sum(amount) filter(where type = 'DIVIDEND') over(partition by account_id, symbol, cycles, sell_cycles order by trade_date) dividend_balance
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
        cycles,
        sell_cycles,
        rolling_units,
        round(avg_bought_price, 4) avg_bought_price,
        dividend_balance,
        case when type='SELL' then round((amount + coalesce(dividend_balance, 0) - avg_bought_price*units)*100/nullif(avg_bought_price*units, 0), 2) end return_percentage,
        case when type='SELL' then round(amount + coalesce(dividend_balance, 0) - avg_bought_price*units, 2) end realized_profit
        FROM partitioned;
        """,
        (),
    )


def get_all_active_accounts(client):
    res = client.execute("""
            select *
            from accounts
            where status='open'
            # and balance > 10
        """)
    return to_dicts(res)
