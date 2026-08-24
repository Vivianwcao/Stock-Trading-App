import json
import os
from pprint import pprint

from dotenv import load_dotenv
from snaptrade_client import SnapTrade, SnapTradeAuth

load_dotenv()

snaptrade = SnapTrade(
    # Personal auth means your API key is the user context.
    auth=SnapTradeAuth.personal_api_key(
        client_id=os.environ["SNAPTRADE_CLIENT_ID"],
        consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
    )
)


def get_accounts():
    # List all accounts
    res2 = snaptrade.account_information.list_user_accounts()
    with open("accounts.json", "w", encoding="utf-8") as f:
        json.dump(res2.body, f, indent=2, default=str)
    print("Retrieved latest accounts data from API.")


def get_activities(account_id, transaction_types, start_date=None) -> list[dict]:
    # bulk
    arguments = {
        "account_id": account_id,
        "limit": 1000,  # 1000 is default also the max
        "type": transaction_types,
        # start_date is defalted to the first transaction based on trade_date.
        # end_date is defalted to the last transaction based on trade_date.
    }
    if start_date:
        #  from last certain (eg. 2) days (updates every 24 hours + 1 day delay)
        arguments["start_date"] = start_date
    res = snaptrade.account_information.get_account_activities(**arguments)
    return res.body["data"]


def get_orders_last_24hrs(account_id):
    # List all executed orders (only buy and sell) - last 24 hours
    res = snaptrade.account_information.get_user_account_recent_orders(
        account_id=account_id, only_executed=True
    )
    return res.body["orders"]


# arguments = {
#     "account_id": "12740fda-39c7-4199-b569-c3f8ac982a6c",
#     "limit": 1000,
#     "type": "BUY,SELL,INTEREST,DIVIDEND,WITHDRAWAL,REI,STOCK_DIVIDEND,FEE,TAX,TRANSFER,SPLIT,SUBSTITUTE_DIVIDEND,CONTRIBUTION",
#     "start_date": "2026-08-20",
# }
# res = snaptrade.account_information.get_account_activities(**arguments)
# print(res.body["data"])

# # List all account positions
# res3 = snaptrade.account_information.get_all_account_positions(
#     account_id="*******"
# )
# with open("./test/positions.json", "w", encoding="utf-8") as f:
#     json.dump(res3.body, f, indent=2, default=str)

# # List all orders - past (30) days
# res5 = snaptrade.account_information.get_user_account_orders(
#     account_id="*******", days=30, state="executed"
# )
# with open("./test/all_orders.json", "w", encoding="utf-8") as f:
#     # returns a list
#     json.dump(res5.body, f, indent=2, default=str)
