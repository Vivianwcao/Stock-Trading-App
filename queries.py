from update_tables import TRANSPORT_TYPES
from datetime import datetime, timedelta, timezone


# one time
def init_db(conn):
    cursor = conn.cursor()
    # To have the fresh database instance automatically inherits WAL mode
    cursor.execute("PRAGMA journal_mode = WAL;")
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
            -- and type IN ('BUY', 'SELL', 'DIVIDEND')
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
            FROM with_pres
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


def get_all_active_transactions(conn, data):
    # a list or tuple
    nicknames = data.get("nicknames")
    start_date = data.get("start_date") or "2018-01-01"
    # use tomorrow's date if no end_date provided
    end_date = data.get("end_date") or (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    cursor = conn.cursor()

    if not nicknames:
        names = cursor.execute("""
            select 
                nickname
            from accounts
            where nickname is not null
            and status='open'
            and balance > 10
        """).fetchall()
        nicknames = [r[0] for r in names]

    placeholder = ",".join(["?" for _ in nicknames])

    rows = cursor.execute(
        f"""
            select
                *
            from transactions
            where nickname in ({placeholder})
            and trade_date > ?
            and trade_date < ?
        """,
        (*nicknames, start_date, end_date),
    ).fetchall()
    return [dict(r) for r in rows]
