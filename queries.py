from datetime import date, datetime, timedelta


# one time
def init_db(conn):
    cursor = conn.cursor()
    # To have the fresh database instance automatically inherits DELETE mode (safe for aws lambda)
    # if didn't init DB with delete mode simply run
    # sqlite3 stocks.db "PRAGMA journal_mode = DELETE;"
    cursor.execute("PRAGMA journal_mode = DELETE;")
    cursor.execute("PRAGMA foreign_keys = ON;")

    script = """
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
                references accounts(id)
        );

        create table if not exists last_fetched (
            api_source text not null,
            account_id text not null,
            fetched_at text not null
                default (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

            primary key(api_source, account_id)
        );

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
        

        CREATE UNIQUE INDEX IF NOT EXISTS idx_activities_api_dedup 
                ON activities (trade_date, account_id, symbol, type, price, units)
                WHERE source <> 'wealth_simple_csv';

        create index if not exists idx_transactions
        on activities(account_id, symbol, trade_date);            
    """

    cursor.executescript(script)

    # 2. View Creation
    cursor.executescript(
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
        """
    )

    cursor.executescript(
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
    cursor.executescript(
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


def get_all_nicknames(conn):
    nicknames = conn.execute("""
            select 
                nickname
            from accounts
            where nickname is not null
            and status='open'
            and balance > 10
        """).fetchall()
    return [n["nickname"] for n in nicknames]


# get the recently active stocks for all nicknames
def get_recently_active_stocks_all_nicknames(conn, days=90):
    recent_date = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT
            nickname,
            symbol,
            max(trade_date) latest_date,
            sum(units) holding
        from activities act
        join accounts acc
        on act.account_id = acc.id
        group by
            nickname,
            symbol
        having latest_date > ?
        order by 
            nickname,
            symbol,
            latest_date,
            holding;
    """,
        (recent_date),
    ).fetchall()
    return [dict(row) for row in rows]


# get the recently active stocks for given nicknames
def get_recently_active_stocks_by_nickname(conn, nickname, days=90):
    recent_date = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT
            symbol,
            max(trade_date) latest_date,
            sum(units) holding
        from activities act
        join accounts acc
        on act.account_id = acc.id
        where nickname = ?
        group by
            symbol
        having latest_date > ?
        order by 
            nickname,
            symbol,
            latest_date,
            holding;
    """,
        (nickname, recent_date),
    ).fetchall()
    return [dict(row) for row in rows]


# get transactions on selected stocks by a single nickname
def get_transactions_by_stocks_by_nickname(conn, nickname, stocks):
    placeholder = ",".join("?" for _ in stocks)
    rows = conn.execute(
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
            WHERE nickname = ?
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
        """,
        (nickname, *stocks, nickname),
    ).fetchall()
    return [dict(r) for r in rows]


def get_last_fetched(conn, api_source, account_id):
    row = conn.execute(
        """
        select
            account_id,
            fetched_at
        from last_fetched 
        where api_source = ?
        and account_id = ?
        """,
        (api_source, account_id),
    ).fetchone()
    return dict(row) if row else None


def get_latest_analysis_all_accounts(conn):
    rows = conn.execute("select * from analysis").fetchall()
    return [dict(r) for r in rows]


def get_snapshot_dates_all_accounts(conn):
    snapshots = conn.execute(
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
    dates = conn.execute(
        """
        select distinct
            last_successful_sync 
        from positions 
        where account_id = ?
            and trigger = 'scheduled'
        """,
        (account_id,),
    ).fetchall()
    return [dict(sn) for sn in dates]


def get_latest_analysis_by_account(conn, account_id):
    rows = conn.execute(
        """
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
            using(symbol);
        """,
        (account_id, account_id, account_id, account_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_analysis_by_account_by_snapshot(conn, account_id, sync_date):
    rows = conn.execute(
        """
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
        """,
        (account_id, sync_date, account_id, sync_date, account_id, account_id),
    ).fetchall()
    return [dict(r) for r in rows]


def compare_analysis_by_account_across_snapshots(conn, account_id, sync_dates=None):
    if not sync_dates:
        rows = get_snapshot_dates_by_account(conn, account_id)
        sync_dates = [row["last_successful_sync"] for row in rows]
    placeholder = ",".join("?" for _ in sync_dates)

    rows = conn.execute(
        f"""
        with account_totals as (
            select
                account_id,
                last_successful_sync,
                sum(holdings * cost_basis) total_bought, 
                sum(holdings * current_price) total_current
            from positions
            where account_id = ? 
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
                d.account_id,
                d.symbol,
                last_successful_sync,
                max(dividend_balance) dividend_balance
            from latest_valid_dates d
            join transactions t
            on t.account_id = d.account_id
                and t.symbol = d.symbol
                and trade_date = latest_valid_date
            where d.account_id = ?
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
        where account_id = ? 
        and last_successful_sync in ({placeholder});
        """,
        (account_id, *sync_dates, account_id, account_id, account_id, *sync_dates),
    ).fetchall()
    return [dict(r) for r in rows]
