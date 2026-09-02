from utils import to_dicts, to_dict
from update_tables import TRANSPORT_TYPES


def get_all_active_accounts(client):
    res = client.execute("""
            select *
            from accounts
            where status='open'
            and balance > 10
        """)
    return to_dicts(res)


def get_activities_single_account(
    client, account_id, symbol, start_date, end_date, types=TRANSPORT_TYPES
):
    client.execute(
        """
        select
            trade_date,
            type,
            price,
            units,
            amount,

        from activities
        where 
            account_id = ?
            and (symbol is null or symbol = ?)
            and types in ?
            and trade_date >= ?
            and trade_date <= ?
        order by datetime(trade_date)
        """,
        (),
    )
