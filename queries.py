from datetime import date, datetime, timedelta
from utils import convert_utc_string_to_timestamp


# one time
def init_db(conn):
    cursor = conn.cursor()

    script = """
        create table if not exists accounts (
            id uuid primary key, --snaptrade account_id
            wealth_simple_account_id varchar(50), --wealth simple account_id
            account_name varchar(50) not null, --tfsa-absvdfh
            nickname varchar(50), -- added custom/display nickname
            account_type varchar(50) not null,
            status varchar(20),
            balance numeric(16, 6),
            first_transaction_date timestamptz,
            institution varchar(50),
            currency varchar(10),
            last_successful_sync timestamptz not null -- utc timestamp from api
        );
        
        create table if not exists activities (
            id uuid primary key,
            account_id uuid not null,
            symbol varchar(12),
            type varchar(50) not null,
            price numeric(16, 6),
            units numeric(16, 6),
            amount numeric(16, 6),
            fee numeric(16, 6),
            currency varchar(10),
            trade_date timestamptz not null,
            source varchar(30) not null,
            updated_at timestamptz not null
                default now(),

            foreign key (account_id)
                references accounts(id)
        );

        create table if not exists last_fetched (
            api_source varchar(30) not null,
            account_id uuid not null,
            fetched_at timestamptz not null
                default now(),

            primary key(api_source, account_id)            
            
            Foreign Key (account_id) 
            REFERENCES accounts(id)
        );

        create table if not exists positions(
            id serial primary key,
            account_id uuid,
            symbol varchar(12),
            holdings numeric(16, 6) not null,
            current_price numeric(16, 6) not null,
            cost_basis numeric(16, 6) not null,
            trigger varchar(30) not null,
            last_successful_sync timestamptz,

            Foreign Key (account_id) 
            REFERENCES accounts(id)
        );
        

        CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_api_dedup 
                ON activities (trade_date, account_id, symbol, type, price, units)
                WHERE source <> 'wealth_simple_csv';

        create index if not exists idx_transactions
        on activities(account_id, symbol, trade_date);            
    """
    cursor.execute(script)

    # 2. View Creation
    cursor.execute(
        """
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
                strpos(
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
        """
    )

    cursor.execute(
        """   
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
        totals as (
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
        join totals
            using(account_id)
        left join dividends
            using(account_id, symbol);
        """
    )


def create_tables(conn):
    cursor = conn.cursor()
    cursor.execute(
        "drop table if exists activities; drop table if exists accounts; drop table if exists last_fetched;"
    )
    init_db(conn)  # run once


def get_all_active_accounts(conn):
    cursor = conn.cursor()
    rows = cursor.execute("""
        select
            id,
            nickname,
            account_type,
            status,
            balance,
            first_transaction_date,
            institution,
            last_successful_sync
        from accounts
        where status='open'
        and balance > 10
    """).fetchall()
    return [dict(r) for r in rows]


def get_nickname_by_account(conn, account_id):
    cursor = conn.cursor()
    nickname = cursor.execute(
        """
        select 
            nickname
        from accounts
        where account_id = %s;
        """,
        (account_id,),
    ).fetchone()
    return nickname["nickname"] if nickname else None


def get_latest_trade_date_by_account(conn, account_id):
    cursor = conn.cursor()
    row = cursor.execute(
        """
            select 
                max(trade_date) latest_date
            from activities
            where account_id = %s
        """,
        (account_id,),
    ).fetchone()
    return row["latest_date"] if row else None


def get_all_stocks_all_nicknames(conn):
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        SELECT
            nickname,
            symbol,
            max(trade_date) latest_date,
            sum(units) holding
        from activities act
        join accounts acc
        on act.account_id = acc.id
        where symbol is not null
        group by
            nickname,
            symbol
        order by 
            nickname,
            symbol,
            latest_date,
            holding;
    """,
    ).fetchall()
    return [dict(row) for row in rows]


# get the recently active stocks for one account
def get_recently_active_stocks_by_nickname(conn, nickname, days=90):
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        SELECT
            symbol
        from activities act
        join accounts acc
        on act.account_id = acc.id
        where nickname = %s
            and symbol is not null
        group by
            symbol
        having max(trade_date) >= now() - (%s * interval '1 day')
            and sum(units) > 0;
        """,
        (nickname, days),
    ).fetchall()
    return [row["symbol"] for row in rows]


# get stocks with updates for one account (internal use)
def get_stocks_with_updates_by_account(
    conn, account_id, last_trade_date: datetime | None
):
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        SELECT
            symbol,
            max(trade_date) latest_date,
            sum(units) holding
        from activities act
        where account_id = %s
            and symbol is not null
        group by
            symbol
        having latest_date > coalesce(%s, timestamptz'2017-01-01 00:00:00+00')
        order by
            symbol,
            latest_date,
            holding;
    """,
        (account_id, last_trade_date),
    ).fetchall()
    return [dict(row) for row in rows]


# get transactions on selected stocks by one nickname
def get_transactions_by_stocks_by_nickname(conn, nickname, stocks):
    placeholder = ",".join("%s" for _ in stocks)
    cursor = conn.cursor()
    rows = cursor.execute(
        f"""
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
            WHERE nickname = %s
                and symbol in ({placeholder})
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
                strpos(
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
            %s nickname,
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
        """,
        (nickname, *stocks, nickname),
    ).fetchall()
    return [dict(r) for r in rows]


def get_last_fetched(conn, api_source, account_id):
    cursor = conn.cursor()
    row = cursor.execute(
        """
        select
            account_id,
            fetched_at
        from last_fetched 
        where api_source = %s
        and account_id = %s
        """,
        (api_source, account_id),
    ).fetchone()
    return dict(row) if row else None


def get_latest_analysis_all_accounts(conn):
    cursor = conn.cursor()
    rows = cursor.execute("select * from analysis").fetchall()
    return [dict(r) for r in rows]


def get_snapshot_dates_all_accounts(conn):
    cursor = conn.cursor()
    snapshots = cursor.execute(
        """
        SELECT DISTINCT 
            account_id, 
            last_successful_sync, 
            trigger
        FROM positions
        ORDER BY 
            account_id, 
            last_successful_sync DESC
        """
    ).fetchall()
    return [dict(sn) for sn in snapshots]


def get_snapshot_dates_by_account(conn, account_id):
    cursor = conn.cursor()
    dates = cursor.execute(
        """
        select distinct
            last_successful_sync 
        from positions 
        where account_id = %s
            and trigger = 'scheduled'
        """,
        (account_id,),
    ).fetchall()
    return [dict(sn) for sn in dates]


def get_latest_analysis_by_account(conn, account_id):
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        with latest_positions_date as (
        SELECT
            max(last_successful_sync) latest_pos_date
        from positions
        where account_id = %s  
        ),
        latest_positions as (
        select *
        from positions
        where account_id = %s  
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
        where account_id = %s
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
        where account_id = %s
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
            using(symbol);
        """,
        (account_id, account_id, account_id, account_id),
    ).fetchall()
    return [dict(r) for r in rows]


# external use from front-end (need to convert date string --> date)
def get_analysis_by_account_by_snapshot(conn, account_id, sync_date_str):
    sync_date = convert_utc_string_to_timestamp(sync_date_str)

    if sync_date is None:
        return []
    cursor = conn.cursor()
    rows = cursor.execute(
        """
        with latest_positions as (
        select *
        from positions
        where account_id = %s  
            and last_successful_sync = %s
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
            where account_id = %s
            and symbol is not null
            and trade_date <= %s
            group by
                symbol
        ),
        dividends as (
            select
                symbol,
                max(dividend_balance) dividend_balance
            from transactions
            where account_id = %s
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
        where account_id = %s;
        """,
        (account_id, sync_date, account_id, sync_date, account_id, account_id),
    ).fetchall()
    return [dict(r) for r in rows]


# external use from front-end (need to convert date string --> date)
def compare_analysis_by_account_across_snapshots(conn, account_id, sync_dates_str):
    sync_dates_try = [convert_utc_string_to_timestamp(d) for d in sync_dates_str]
    sync_dates = [d for d in sync_dates_try if d is not None]

    if not sync_dates:
        return []

    placeholder = ",".join("%s" for _ in sync_dates)

    cursor = conn.cursor()
    rows = cursor.execute(
        f"""
        with account_totals as (
            select
                account_id,
                last_successful_sync,
                sum(holdings * cost_basis) total_bought, 
                sum(holdings * current_price) total_current
            from positions
            where account_id = %s 
                and last_successful_sync in ({placeholder})
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
            where account_id = %s 
                and symbol is not null
                and trade_date <= last_successful_sync
            group by
                account_id,
                symbol,
                last_successful_sync
        ),
        dividends as (
            select
                d.account_id,
                d.symbol,
                last_successful_sync,
                max(dividend_balance) dividend_balance
            from latest_valid_dates d
            join transactions t
            on t.account_id = d.account_id
                and t.symbol = d.symbol
                and trade_date = latest_valid_date
            where d.account_id = %s
            group by
                d.account_id,
                d.symbol,
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
        where account_id = %s 
        and last_successful_sync in ({placeholder});
        """,
        (account_id, *sync_dates, account_id, account_id, account_id, *sync_dates),
    ).fetchall()
    return [dict(r) for r in rows]
