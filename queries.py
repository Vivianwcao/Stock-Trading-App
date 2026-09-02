from utils import to_dicts, to_dict


def get_all_active_accounts(client):
    res = client.execute("""
            select *
            from accounts
            where status='open'
            and balance > 10
        """)
    return to_dicts(res)
