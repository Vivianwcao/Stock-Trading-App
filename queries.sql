CREATE INDEX IF NOT EXISTS idx_transactions ON activities(account_id, symbol, trade_date);
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
        ) AS cycles
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
            cycles
            ORDER BY
                trade_date
        ) AS sell_cycles
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
            cycles
            ORDER BY
                trade_date
        ) AS bought_units,
        sum(amount) FILTER (
            WHERE
                TYPE = 'BUY'
        ) OVER (
            PARTITION BY account_id,
            symbol,
            cycles
            ORDER BY
                trade_date
        ) AS bought_balance,
        sum(amount) FILTER (
            WHERE
                TYPE = 'BUY'
        ) OVER (
            PARTITION BY account_id,
            symbol,
            cycles
            ORDER BY
                trade_date
        ) / nullif(
            sum(units) FILTER (
                WHERE
                    TYPE = 'BUY'
            ) OVER (
                PARTITION BY account_id,
                symbol,
                cycles
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
            cycles,
            sell_cycles
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
    cycles,
    sell_cycles,
    rolling_units,
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