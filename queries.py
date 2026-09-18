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
            account_id text,
            symbol text,
            holdings real not null,
            current_price real not null,
            cost_basis real not null,
            last_successful_sync text,

            primary key (account_id, symbol),

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
            end bought_balance,
            case when type = 'BUY' then price
                else 0
            end avg_bought_price
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
            case when p.type = 'BUY' then t.bought_balance + p.amount
            when p.type = 'SELL' then t.bought_balance - t.avg_bought_price * p.units
            else t.bought_balance
            end as bought_balance,

            case when p.type = 'BUY' then abs(coalesce((t.bought_balance + p.amount)/nullif(p.holdings_per_cycle, 0), p.price))
            else t.avg_bought_price
            end as avg_bought_price
            from partitioned p
            join tree t 
            on p.nickname = t.nickname
            and p.symbol = t.symbol
            and p.cycles = t.cycles
            and p.rn = t.rn + 1
        )
        SELECT
            p.wealth_simple_account_id,
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
            round(bought_balance, 4) AS bought_balance,
            round(avg_bought_price, 4) AS avg_bought_price,
            CASE
            WHEN t.type = 'SELL' THEN round(
                (t.price - avg_bought_price) * 100 / nullif(avg_bought_price, 0),
                2
            )
            END AS return_percentage,
            CASE
            WHEN t.type = 'SELL' THEN round((t.price - avg_bought_price) * abs(t.units), 2)
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
        with latest_date as(
            select 
                nickname,
                symbol,
                max(trade_date) latest_date
            from transactions
            where symbol is not null
            group by 
                nickname,
                symbol
        ),
        dividends as (
            select 
                nickname,
                symbol,
                max(dividend_balance) dividend_balance
            from transactions
            where (nickname, symbol, trade_date) in (
                select 
                    nickname,
                    symbol,
                    latest_date 
                from latest_date
            )
            group by 
                nickname,
                symbol 
        ),
        totals as (
            select 
                account_id,
                sum(holdings * cost_basis) total_bought, 
                sum(holdings * current_price) total_current
            from positions
            group by account_id
        )
        select
            nickname, 
            p.account_id,
            p.symbol,
            holdings,
            cost_basis,
            current_price,
            round((current_price - cost_basis)*100 / cost_basis, 2) growth_percentage,
            round(holdings * cost_basis, 4) bought_balance,
            round(total_bought, 4) total_bought,
            round(holdings * cost_basis*100 / total_bought, 2) bought_ratio,
            round(holdings * current_price, 4) current_balance,
            round(total_current, 4) total_current,
            round(holdings * current_price*100 / total_current, 2) current_ratio,
            dividend_balance,
            rank() over(partition by nickname order by holdings * cost_basis*100 / total_bought desc) bought_ratio_rnk,
            rank() over(partition by nickname order by holdings * current_price*100 / total_current desc) current_ratio_rnk,
            rank() over(partition by nickname order by holdings * cost_basis desc) bought_balance_rnk,
            rank() over(partition by nickname order by holdings * current_price desc) current_balance_rnk,
            rank() over(partition by nickname order by (current_price - cost_basis)*100 / cost_basis desc) growth_percentage_rnk,
        p.last_successful_sync
        from positions p
        join totals t
            using(account_id)
        join accounts
            on p.account_id = id
        join dividends
            using(nickname, symbol);
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
        select *
        from accounts
        where status='open'
        and balance > 10
    """).fetchall()
    return [dict(r) for r in rows]


def get_nicknames_by_ids(conn, account_ids):
    placeholder = ",".join("?" for _ in account_ids)
    nicknames = conn.execute(
        f"""
            select
                nickname
            from accounts
            where id in ({placeholder})
        """,
        (*account_ids,),
    ).fetchall()
    return [n["nickname"] for n in nicknames]


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


def get_active_transactions(
    conn, nicknames_placeholder, nicknames, start_date, end_date
):

    rows = conn.execute(
        f"""
            select
                *
            from transactions
            where nickname in ({nicknames_placeholder})
            and trade_date > ?
            and trade_date < ?
        """,
        (*nicknames, start_date, end_date),
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


def get_analysis(conn):
    rows = conn.execute("select * from analysis").fetchall()
    return [dict(r) for r in rows]
