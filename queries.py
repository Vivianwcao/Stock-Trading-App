from utils import to_dicts, to_dict
from update_tables import TRANSPORT_TYPES


# one time
def init_db(client):
    tables = (
        """
        create table if not exists accounts (
            id text primary key, --snaptrade account_id
            wealth_simple_account_id text, --wealth simple account_id
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
    client.execute("DROP VIEW IF EXISTS transactions;")
    client.execute(
        """
        CREATE VIEW IF NOT EXISTS transactions AS
        WITH
        cleaned AS (
            SELECT
            wealth_simple_account_id,
            account_id,
            nickname,
            trade_date,
            symbol,
            type,
            price,
            units,
            amount,
            sum(units) OVER (
                PARTITION BY nickname, symbol
                ORDER BY trade_date
            ) AS rolling_units
            from accounts acc
            join activities act
            on acc.id = act.account_id
            WHERE status = 'open'
            and type IN ('BUY', 'SELL', 'DIVIDEND')
        ),
        with_pres AS (
            SELECT
            *,
            lag(type) OVER (
                PARTITION BY nickname, symbol
                ORDER BY trade_date
            ) AS pre_type,
            
            /* Replaces correlated CTE subquery with a window function */
            substr(
                max(
                CASE
                    WHEN type <> 'DIVIDEND' THEN trade_date || '#' || type
                END
                ) OVER (
                PARTITION BY nickname, symbol
                ORDER BY trade_date 
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ),
                instr(
                max(
                    CASE
                    WHEN type <> 'DIVIDEND' THEN trade_date || '#' || type
                    END
                ) OVER (
                    PARTITION BY nickname, symbol
                    ORDER BY trade_date 
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ),
                '#'
                ) + 1
            ) AS pre_trade_type,
            
            lag(rolling_units) OVER (
                PARTITION BY nickname, symbol
                ORDER BY trade_date
            ) AS pre_rolling_units
            FROM cleaned
        ),
        grouped AS (
            SELECT
            *,
            count(*) FILTER (
                WHERE
                pre_trade_type = 'SELL'
                AND type = 'BUY'
                AND pre_rolling_units <= 0
            ) OVER (
                PARTITION BY nickname, symbol
                ORDER BY trade_date
            ) AS cycles
            FROM with_pres  -- FIXED: Added missing source table
        ),
        partitioned AS (
            SELECT
            *,
            sum(units) FILTER (
                WHERE type = 'BUY'
            ) OVER (
                PARTITION BY nickname, symbol, cycles
                ORDER BY trade_date
            ) AS bought_units,
            
            sum(amount) FILTER (
                WHERE type = 'BUY'
            ) OVER (
                PARTITION BY nickname, symbol, cycles
                ORDER BY trade_date
            ) AS bought_balance,
            
            sum(amount) FILTER (
                WHERE type = 'BUY'
            ) OVER (
                PARTITION BY nickname, symbol, cycles
                ORDER BY trade_date
            ) / nullif(
                sum(units) FILTER (
                WHERE type = 'BUY'
                ) OVER (
                PARTITION BY nickname, symbol, cycles
                ORDER BY trade_date
                ),
                0
            ) AS avg_bought_price,
            
            sum(amount) FILTER (
                WHERE type = 'DIVIDEND'
            ) OVER (
                PARTITION BY nickname, symbol, cycles
                ORDER BY trade_date
            ) AS dividend_balance
            FROM grouped
        )
        SELECT
        wealth_simple_account_id,
        account_id,
        nickname,
        trade_date,
        symbol,
        type,
        price,
        units,
        amount,
        rolling_units,
        cycles,
        round(avg_bought_price, 4) AS avg_bought_price,
        round(dividend_balance, 4) AS dividend_balance,
        CASE
            WHEN type = 'SELL' THEN round(
            (amount - avg_bought_price * units) * 100 / nullif(avg_bought_price * units, 0),
            2
            )
        END AS return_percentage,
        CASE
            WHEN type = 'SELL' THEN round(amount - avg_bought_price * units, 2)
        END AS realized_profit
        FROM partitioned;
        """,
        (),
    )


def get_all_active_accounts(client):
    res = client.execute("""
            select *
            from accounts
            where status='open'
            and balance > 10
        """)
    return to_dicts(res)
