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
        CREATE VIEW IF NOT EXISTS transactions AS WITH cleaned AS (
            SELECT
                account_id,
                trade_date,
                symbol,
                TYPE,
                price,
                units,
                amount,
                sum(units) OVER (
                    PARTITION BY account_id,
                    symbol
                    ORDER BY
                        trade_date
                ) AS rolling_units
            FROM
                activities
            WHERE
                TYPE IN ('BUY', 'SELL', 'DIVIDEND')
        ),
        with_pres AS (
            SELECT
                *,
                lag(TYPE) OVER (
                    PARTITION BY account_id,
                    symbol
                    ORDER BY
                        trade_date
                ) AS pre_type,
                
                -- NEED TO REMOVE THIS CTE TO CREATE A VIEW IN TURSO, LIBSQL DOES NOT SUPPORT SUBQUERY IN VIEWS
                -- (
                --     SELECT
                --         TYPE
                --     FROM
                --         cleaned
                --     WHERE
                --         account_id = c.account_id
                --         AND symbol = c.symbol
                --         AND trade_date < c.trade_date
                --         AND TYPE <> 'DIVIDEND'
                --     ORDER BY
                --         trade_date DESC
                --     LIMIT
                --         1
                -- ) pre_trade_type, 

                -- Replaces correlated CTE subquery with a window function
                -- Trick for Turso
                substr(
                    max(
                        CASE
                            WHEN TYPE <> 'DIVIDEND' THEN trade_date || '#' || TYPE
                        END
                    ) OVER (
                        PARTITION BY account_id,
                        symbol
                        ORDER BY
                            trade_date ROWS BETWEEN UNBOUNDED PRECEDING
                            AND 1 PRECEDING
                    ),
                    instr(
                        max(
                            CASE
                                WHEN TYPE <> 'DIVIDEND' THEN trade_date || '#' || TYPE
                            END
                        ) OVER (
                            PARTITION BY account_id,
                            symbol
                            ORDER BY
                                trade_date ROWS BETWEEN UNBOUNDED PRECEDING
                                AND 1 PRECEDING
                        ),
                        '#'
                    ) + 1
                ) AS pre_trade_type,
                lag(rolling_units) OVER (
                    PARTITION BY account_id,
                    symbol
                    ORDER BY
                        trade_date
                ) AS pre_rolling_units
            FROM
                cleaned
        ),
        sell_all_grouped AS (
            SELECT
                *,
                count(*) FILTER (
                    WHERE
                        pre_trade_type = 'SELL'
                        AND TYPE = 'BUY'
                        AND pre_rolling_units <= 0
                ) OVER (
                    PARTITION BY account_id,
                    symbol
                    ORDER BY
                        trade_date
                ) AS reset_cycle -- Renamed from RESET
            FROM
                with_pres
        ),
        sell_grouped AS (
            SELECT
                *,
                count(*) FILTER (
                    WHERE
                        pre_type = 'SELL'
                ) OVER (
                    PARTITION BY account_id,
                    symbol,
                    reset_cycle
                    ORDER BY
                        trade_date
                ) AS sell_reset
            FROM
                sell_all_grouped
        ),
        partitioned AS (
            SELECT
                *,
                sum(units) FILTER (
                    WHERE
                        TYPE = 'BUY'
                ) OVER (
                    PARTITION BY account_id,
                    symbol,
                    reset_cycle
                    ORDER BY
                        trade_date
                ) AS bought_units,
                sum(amount) FILTER (
                    WHERE
                        TYPE = 'BUY'
                ) OVER (
                    PARTITION BY account_id,
                    symbol,
                    reset_cycle
                    ORDER BY
                        trade_date
                ) AS bought_balance,
                sum(amount) FILTER (
                    WHERE
                        TYPE = 'BUY'
                ) OVER (
                    PARTITION BY account_id,
                    symbol,
                    reset_cycle
                    ORDER BY
                        trade_date
                ) / nullif(
                    sum(units) FILTER (
                        WHERE
                            TYPE = 'BUY'
                    ) OVER (
                        PARTITION BY account_id,
                        symbol,
                        reset_cycle
                        ORDER BY
                            trade_date
                    ),
                    0
                ) AS avg_bought_price,
                sum(amount) FILTER (
                    WHERE
                        TYPE = 'DIVIDEND'
                ) OVER (
                    PARTITION BY account_id,
                    symbol,
                    reset_cycle,
                    sell_reset
                    ORDER BY
                        trade_date
                ) AS dividend_balance
            FROM
                sell_grouped
        )
        SELECT
            account_id,
            trade_date,
            symbol,
            TYPE,
            price,
            units,
            amount,
            rolling_units,
            reset_cycle,
            sell_reset,
            round(avg_bought_price, 4) AS avg_bought_price,
            dividend_balance,
            CASE
                WHEN TYPE = 'SELL' THEN round(
                    (
                        amount + coalesce(dividend_balance, 0) - avg_bought_price * units
                    ) * 100 / nullif(avg_bought_price * units, 0),
                    2
                )
            END AS return_percentage,
            CASE
                WHEN TYPE = 'SELL' THEN round(
                    amount + coalesce(dividend_balance, 0) - avg_bought_price * units,
                    2
                )
            END AS realized_profit
        FROM
            partitioned;
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
