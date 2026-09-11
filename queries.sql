CREATE INDEX IF NOT EXISTS idx_transactions ON activities(account_id, symbol, trade_date);

DROP VIEW IF EXISTS transactions;

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
      ) AS rolling_units,
      sum(amount) OVER (
        PARTITION BY nickname
        ORDER BY trade_date
      ) AS account_balance
    from accounts acc
    join activities act
    on acc.id = act.account_id
    WHERE status = 'open'
      and nickname is not null
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
      ) AS dividend_balance,
      sum(amount) filter(where type in ('BUY', 'SELL')) over(partition by nickname, symbol, cycles order by trade_date) trading_balance
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
  round(trading_balance, 4) trading_balance,
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
  END AS realized_profit,
  round(account_balance, 4) account_balance
FROM partitioned;