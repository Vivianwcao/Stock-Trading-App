-- run:
sqlite3 stocks.db "PRAGMA journal_mode = DELETE;"
-- to inspect the active journal mode of your database
sqlite3 stocks.db "PRAGMA journal_mode;"

create table if not exists positions(
    id integer primary key,
    account_id text,
    symbol text,
    holdings real not null,
    current_price real not null,
    cost_basis real not null,
    trigger text not null,
    last_successful_sync text,

    Foreign Key (account_id) 
    REFERENCES accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_transactions ON activities(account_id, symbol, trade_date);

DROP VIEW IF EXISTS transactions;

CREATE VIEW IF NOT EXISTS transactions AS
WITH recursive
  cleaned AS (
    SELECT
      act.id,
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
      ) AS holdings_per_stock
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
      
      lag(holdings_per_stock) OVER (
        PARTITION BY nickname, symbol
        ORDER BY trade_date
      ) AS pre_holdings_per_stock
    FROM cleaned
  ),
  grouped AS (
    SELECT
      *,
      count(*) FILTER (
        WHERE
          pre_trade_type = 'SELL'
          AND type = 'BUY'
          AND pre_holdings_per_stock <= 0
      ) OVER (
        PARTITION BY nickname, symbol
        ORDER BY trade_date
      ) AS cycles
    FROM with_pres 
  ),
  partitioned AS (
    SELECT
      *,
      -- ideally this should equal to holdings_per_stock as there shouldn't be negative holdings
      -- For safety in case negative holdings due to missing data or calculating errors.
      sum(units) OVER (
        PARTITION BY nickname, symbol, cycles
        ORDER BY trade_date
      ) AS holdings_per_cycle,
      
      sum(amount) FILTER (
        WHERE type = 'DIVIDEND'
      ) OVER (
        PARTITION BY nickname, symbol, cycles
        ORDER BY trade_date
      ) AS dividend_balance,

    row_number() over(
      PARTITION BY nickname, symbol, cycles
      ORDER BY trade_date, id) rn

    FROM grouped
  ),
  tree as (
    select
      id,
      nickname,
      symbol,
      type,
      price,
      units,
      amount,
      cycles,
      holdings_per_cycle,
      rn,
      case when type = 'BUY' then amount
        else 0
      end cost,
      case when type = 'BUY' then price
        else 0
      end avg_cost
    from partitioned
    -- seeds condition
    where rn = 1

    union all

    select
      p.id,
      p.nickname,
      p.symbol,
      p.type,
      p.price,
      p.units,
      p.amount,
      p.cycles,
      p.holdings_per_cycle,
      p.rn,
      case when p.type = 'BUY' then t.cost + p.amount
      when p.type = 'SELL' then t.cost - t.avg_cost * p.units
      else t.cost
      end as cost,

      case when p.type = 'BUY' then abs(coalesce((t.cost + p.amount)/nullif(p.holdings_per_cycle, 0), p.price))
      else t.avg_cost
      end as avg_cost
    from partitioned p
    join tree t 
    on p.nickname = t.nickname
    and p.symbol = t.symbol
    and p.cycles = t.cycles
    and p.rn = t.rn + 1
  )
  SELECT
    p.account_id,
    t.nickname,
    p.trade_date,
    t.symbol,
    t.type,
    t.price,
    t.units,
    t.amount,
    t.cycles,
    t.holdings_per_cycle,
    round(p.dividend_balance, 4) AS dividend_balance,
    round(cost, 4) AS cost,
    round(avg_cost, 4) AS avg_cost,
    CASE
      WHEN t.type = 'SELL' THEN round(
        (t.price - avg_cost) * 100 / nullif(avg_cost, 0),
        2
      )
    END AS return_percentage,
    CASE
      WHEN t.type = 'SELL' THEN round((t.price - avg_cost) * abs(t.units), 2)
    END AS realized_profit
  FROM tree t
  join partitioned p
  using(id);

-- query "transactions" by account_id
WITH recursive
  cleaned AS (
    SELECT
      act.id,
      account_id,
      trade_date,
      symbol,
      type,
      price,
      units,
      amount,
      sum(units) OVER (
        PARTITION BY symbol
        ORDER BY trade_date
      ) AS holdings_per_stock
    from accounts acc
    join activities act
    on acc.id = act.account_id
    WHERE nickname = ?
  ),
  with_pres AS (
    SELECT
      *,
      lag(type) OVER (
        PARTITION BY symbol
        ORDER BY trade_date
      ) AS pre_type,
      
      substr(
        max(
          CASE
            WHEN type <> 'DIVIDEND' THEN trade_date || '#' || type
          END
        ) OVER (
          PARTITION BY symbol
          ORDER BY trade_date 
          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ),
        instr(
          max(
            CASE
              WHEN type <> 'DIVIDEND' THEN trade_date || '#' || type
            END
          ) OVER (
            PARTITION BY symbol
            ORDER BY trade_date 
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
          ),
          '#'
        ) + 1
      ) AS pre_trade_type,
      
      lag(holdings_per_stock) OVER (
        PARTITION BY symbol
        ORDER BY trade_date
      ) AS pre_holdings_per_stock
    FROM cleaned
  ),
  grouped AS (
    SELECT
      *,
      count(*) FILTER (
        WHERE
          pre_trade_type = 'SELL'
          AND type = 'BUY'
          AND pre_holdings_per_stock <= 0
      ) OVER (
        PARTITION BY symbol
        ORDER BY trade_date
      ) AS cycles
    FROM with_pres 
  ),
  partitioned AS (
    SELECT
      *,
      -- ideally this should equal to holdings_per_stock as there shouldn't be negative holdings
      -- For safety in case negative holdings due to missing data or calculating errors.
      sum(units) OVER (
        PARTITION BY symbol, cycles
        ORDER BY trade_date
      ) AS holdings_per_cycle,
      
      sum(amount) FILTER (
        WHERE type = 'DIVIDEND'
      ) OVER (
        PARTITION BY symbol, cycles
        ORDER BY trade_date
      ) AS dividend_balance,

    row_number() over(
      PARTITION BY symbol, cycles
      ORDER BY trade_date, id) rn

    FROM grouped
  ),
  tree as (
    select
      id,
      symbol,
      type,
      price,
      units,
      amount,
      cycles,
      holdings_per_cycle,
      rn,
      case when type = 'BUY' then amount
        else 0
      end cost,
      case when type = 'BUY' then price
        else 0
      end avg_cost
    from partitioned
    -- seeds condition
    where rn = 1

    union all

    select
      p.id,
      p.symbol,
      p.type,
      p.price,
      p.units,
      p.amount,
      p.cycles,
      p.holdings_per_cycle,
      p.rn,
      case when p.type = 'BUY' then t.cost + p.amount
      when p.type = 'SELL' then t.cost - t.avg_cost * p.units
      else t.cost
      end as cost,

      case when p.type = 'BUY' then abs(coalesce((t.cost + p.amount)/nullif(p.holdings_per_cycle, 0), p.price))
      else t.avg_cost
      end as avg_cost
    from partitioned p
    join tree t 
    on p.symbol = t.symbol
    and p.cycles = t.cycles
    and p.rn = t.rn + 1
  )
  SELECT
    ? nickname,
    p.account_id,
    p.trade_date,
    t.symbol,
    t.type,
    t.price,
    t.units,
    t.amount,
    t.cycles,
    t.holdings_per_cycle,
    round(p.dividend_balance, 4) AS dividend_balance,
    round(cost, 4) AS cost,
    round(avg_cost, 4) AS avg_cost,
    CASE
      WHEN t.type = 'SELL' THEN round(
        (t.price - avg_cost) * 100 / nullif(avg_cost, 0),
        2
      )
    END AS return_percentage,
    CASE
      WHEN t.type = 'SELL' THEN round((t.price - avg_cost) * abs(t.units), 2)
    END AS realized_profit
  FROM tree t
  join partitioned p
  using(id);

  
-- in terminal run sqlite3 stocks.db, .exit or .quit to exit sqlite mode in terminal
-- Investigate after csv bulk import and the first activities update
-- We need to keep the last row from api_activities because it has the accurate microseconds for later update
-- non BUY/SELL activities from api can have ws rows comined 
-- find all activities from api_activities
select * 
from activities
where source='api_activities'
order by trade_date desc;

-- find the ids of wealth simple csv dup rows
select
	substr(trade_date, 1, 19) d,
	count(*) c,
	min(trade_date) ws_trade_date,
	min(id) filter (where source = 'wealth_simple_csv') ws_id
from activities
where date(trade_date) = (
	select 
		date(max(trade_date))
	from activities
)
group by substr(trade_date, 1, 19)
having c = 2
order by trade_date desc;

	-- find all latest rows from csv bulk
select 
	* 
from (
	SELECT 
		act.*,
		nickname,
		row_number() over (partition by nickname, symbol order by trade_date desc) rn
	from activities act
	join accounts acc 
	on act.account_id = acc.id
	where source = 'wealth_simple_csv'
	and symbol is not null
) x
where rn = 1
order by trade_date desc;

-- Delete the dup rows from wealth_simple csv bulk import
with x as(
	select
		substr(trade_date, 1, 19) d,
		count(*) c,
		min(trade_date) ws_trade_date,
		min(id) filter (where source = 'wealth_simple_csv') ws_id
	from activities
	where date(trade_date) = (
		select 
			date(max(trade_date))
		from activities
	)
	group by substr(trade_date, 1, 19)
	having c = 2
)
delete from activities
where id in (
	select ws_id
	from x
);
	
select
  nickname,
  sum(amount) balance
from activities act
join accounts acc 
on act.account_id = acc.id
group by nickname;

DROP VIEW IF EXISTS analysis;

CREATE VIEW IF NOT EXISTS analysis AS
with latest_positions_dates as (
  SELECT
    account_id,
    max(last_successful_sync) latest_pos_date
  from positions
  group by account_id  
),
latest_positions as (
  select *
  from positions
  where (account_id, last_successful_sync) in (
    SELECT
      account_id,
      latest_pos_date
    from latest_positions_dates
  )
),
account_totals as (
	select 
		account_id,
		sum(holdings * cost_basis) total_bought, 
		sum(holdings * current_price) total_current
	from latest_positions
	group by account_id
),
latest_valid_dates as(
	select 
		account_id,
		symbol,
		max(trade_date) latest_valid_date
	from activities
  join latest_positions_dates
  using(account_id)
	where symbol is not null
  and trade_date <= latest_pos_date
	group by 
		account_id,
		symbol
),
dividends as (
	select 
		account_id,
		symbol,
		max(dividend_balance) dividend_balance
	from transactions
	where (account_id, symbol, trade_date) in (
		select 
			account_id,
			symbol,
			latest_valid_date 
		from latest_valid_dates
	)
	group by 
		account_id,
		symbol 
)
select 
	p.account_id,
	p.symbol,
	holdings,
	cost_basis,
	current_price,
	round((current_price - cost_basis)*100 / cost_basis, 2) growth_percentage,
	round(holdings * cost_basis, 4) cost,
	round(total_bought, 4) total_bought,
	round(holdings * cost_basis*100 / total_bought, 2) bought_ratio,
	round(holdings * current_price, 4) current_value,
	round(total_current, 4) total_current,
	round(holdings * current_price*100 / total_current, 2) current_ratio,
	dividend_balance,
  p.last_successful_sync
from latest_positions p
join account_totals
	using(account_id)
left join dividends
	using(account_id, symbol);

-- query analysis on a single account
with latest_positions_date as (
  SELECT
    max(last_successful_sync) latest_pos_date
  from positions
  where account_id = ?  
),
latest_positions as (
  select *
  from positions
  where account_id = ?  
    and last_successful_sync = (
      SELECT
        latest_pos_date
      from latest_positions_date
    )
),
account_totals as (
	select
		sum(holdings * cost_basis) total_bought, 
		sum(holdings * current_price) total_current
	from latest_positions
),
latest_valid_dates as(
	select 
		symbol,
		max(trade_date) latest_valid_date
	from activities
  where account_id = ?
    and symbol is not null
    and trade_date <= (
      select 
        latest_pos_date
      from latest_positions_date
    )
	group by
		symbol
),
dividends as (
	select
		symbol,
		max(dividend_balance) dividend_balance
	from transactions
  where account_id = ?
    and (symbol, trade_date) in (
      select
        symbol,
        latest_valid_date 
      from latest_valid_dates
    )
	group by
		symbol 
)
select 
	p.account_id,
	p.symbol,
	holdings,
	cost_basis,
	current_price,
	round((current_price - cost_basis)*100 / cost_basis, 2) growth_percentage,
	round(holdings * cost_basis, 4) cost,
	round(total_bought, 4) total_bought,
	round(holdings * cost_basis*100 / total_bought, 2) bought_ratio,
	round(holdings * current_price, 4) current_value,
	round(total_current, 4) total_current,
	round(holdings * current_price*100 / total_current, 2) current_ratio,
	dividend_balance,
  p.last_successful_sync
from latest_positions p
cross join account_totals
left join dividends
	using(symbol)
where account_id = ?;


-- get all snapshot dates for a single account from positions
SELECT DISTINCT 
    account_id, 
    last_successful_sync, 
    trigger
FROM positions
ORDER BY 
    account_id, 
    last_successful_sync DESC

-- analysis on a single account by snap date
with latest_positions as (
  select *
  from positions
  where account_id = ?  
    and last_successful_sync = ?
),
account_totals as (
	select
		sum(holdings * cost_basis) total_bought, 
		sum(holdings * current_price) total_current
	from latest_positions
),
latest_valid_dates as(
	select 
		symbol,
		max(trade_date) latest_valid_date
	from activities
  where account_id = ?
    and symbol is not null
    and trade_date <= ?
	group by
		symbol
),
dividends as (
	select
		symbol,
		max(dividend_balance) dividend_balance
	from transactions
  where account_id = ?
    and (symbol, trade_date) in (
      select
        symbol,
        latest_valid_date 
      from latest_valid_dates
    )
	group by
		symbol 
)
select 
	p.account_id,
	p.symbol,
	holdings,
	cost_basis,
	current_price,
	round((current_price - cost_basis)*100 / cost_basis, 2) growth_percentage,
	round(holdings * cost_basis, 4) cost,
	round(total_bought, 4) total_bought,
	round(holdings * cost_basis*100 / total_bought, 2) bought_ratio,
	round(holdings * current_price, 4) current_value,
	round(total_current, 4) total_current,
	round(holdings * current_price*100 / total_current, 2) current_ratio,
	dividend_balance,
  p.last_successful_sync
from latest_positions p
cross join account_totals
left join dividends
	using(symbol)
where account_id = ?;

-- analysis on a single account across all snap dates
with account_totals as (
	select
    account_id,
    last_successful_sync,
		sum(holdings * cost_basis) total_bought, 
		sum(holdings * current_price) total_current
	from positions
  where account_id = ? 
    and last_successful_sync in ()
  group by
    account_id,
    last_successful_sync
),
latest_valid_dates as(
	select
    account_id,
    symbol,
    last_successful_sync,
		max(trade_date) latest_valid_date
	from account_totals t
  join activities a
    using(account_id)
  where account_id = ? 
    and symbol is not null
    and trade_date <= last_successful_sync
	group by
    account_id,
    symbol,
    last_successful_sync
),
dividends as (
	select
    account_id,
		symbol,
    last_successful_sync,
		max(dividend_balance) dividend_balance
	from transactions t
  join latest_valid_dates d
  on t.account_id = d.account_id
    and t.symbol = d.symbol
    and trade_date = latest_valid_date
  where account_id = ?
	group by
    account_id,
		symbol,
    last_successful_sync
)
select 
	account_id,
	symbol,
  last_successful_sync,
	holdings,
	cost_basis,
	current_price,
	round((current_price - cost_basis)*100 / cost_basis, 2) growth_percentage,
	round(holdings * cost_basis, 4) cost,
	round(total_bought, 4) total_bought,
	round(holdings * cost_basis*100 / total_bought, 2) bought_ratio,
	round(holdings * current_price, 4) current_value,
	round(total_current, 4) total_current,
	round(holdings * current_price*100 / total_current, 2) current_ratio,
	dividend_balance
from positions
join account_totals
  using(account_id, last_successful_sync)
left join dividends
	using(account_id, symbol, last_successful_sync)
where positions.account_id = ? 
  and last_successful_sync in ();