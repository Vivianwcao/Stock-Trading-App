CREATE INDEX IF NOT EXISTS idx_transactions ON activities(account_id, symbol, trade_date);
CREATE VIEW IF NOT EXISTS transactions AS
WITH cleaned AS (
    SELECT
        account_id,
        trade_date,
        symbol,
        TYPE,
        price,
        units,
        amount,
        -- for reset cycles
        sum(units) over(
            PARTITION by account_id,
            symbol
            ORDER BY
                trade_date
        ) rolling_units
    FROM
        activities
    WHERE TYPE IN ('BUY', 'SELL', 'DIVIDEND')
),
with_pres AS (
    SELECT
        *,
        lag(TYPE) over(
            PARTITION by account_id,
            symbol
            ORDER BY
                trade_date
        ) pre_type,
        (
            SELECT
                TYPE
            FROM
                cleaned
            WHERE
                account_id = c.account_id
                AND symbol = c.symbol
                AND trade_date < c.trade_date
                AND TYPE <> 'DIVIDEND'
            ORDER BY
                trade_date DESC
            LIMIT
                1
        ) pre_trade_type, 
        -- substr(
        --     max(CASE WHEN type <> 'DIVIDEND' THEN trade_date || '#' || type END) 
        --         OVER (PARTITION BY account_id, symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
        --     instr(
        --         max(CASE WHEN type <> 'DIVIDEND' THEN trade_date || '#' || type END) 
        --             OVER (PARTITION BY account_id, symbol ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
        --         '#'
        --     ) + 1
        -- ) AS pre_trade_type,
        lag(rolling_units) over(
            PARTITION by account_id,
            symbol
            ORDER BY
                trade_date
        ) pre_rolling_units
    FROM
        cleaned c
),
sell_all_grouped AS (
    SELECT
        *,
        count(*) filter(
            WHERE
                (
                    pre_trade_type = 'SELL'
                    AND TYPE = 'BUY'
                    AND pre_rolling_units <= 0
                )
        ) over (
            PARTITION by account_id,
            symbol
            ORDER BY
                trade_date
        ) cycles
    FROM
        with_pres
),
sell_grouped AS (
    SELECT
        *,
        count(*) filter(
            WHERE
                pre_type = 'SELL'
        ) over(
            PARTITION by account_id,
            symbol,
            cycles
            ORDER BY
                trade_date
        ) sell_cycles
    FROM
        sell_all_grouped
),
partitioned AS (
    SELECT
        *,
        sum(units) filter(
            WHERE
                TYPE = 'BUY'
        ) over(
            PARTITION by account_id,
            symbol,
            cycles
            ORDER BY
                trade_date
        ) bought_units,
        sum(amount) filter(
            WHERE
                TYPE = 'BUY'
        ) over(
            PARTITION by account_id,
            symbol,
            cycles
            ORDER BY
                trade_date
        ) bought_balance,
        sum(amount) filter(
            WHERE
                TYPE = 'BUY'
        ) over(
            PARTITION by account_id,
            symbol,
            cycles
            ORDER BY
                trade_date
        ) / sum(units) filter(
            WHERE
                TYPE = 'BUY'
        ) over(
            PARTITION by account_id,
            symbol,
            cycles
            ORDER BY
                trade_date
        ) avg_bought_price,
        sum(amount) filter(
            WHERE
                TYPE = 'DIVIDEND'
        ) over(
            PARTITION by account_id,
            symbol,
            cycles,
            sell_cycles
            ORDER BY
                trade_date
        ) dividend_balance
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
    round(avg_bought_price, 4) avg_bought_price,
    dividend_balance,
    CASE
        WHEN TYPE = 'SELL' THEN round(
            (
                amount + coalesce(dividend_balance, 0) - avg_bought_price * units
            ) * 100 / nullif(avg_bought_price * units, 0),
            2
        )
    END return_percentage,
    CASE
        WHEN TYPE = 'SELL' THEN round(
            amount + coalesce(dividend_balance, 0) - avg_bought_price * units,
            2
        )
    END realized_profit
FROM
    partitioned;

