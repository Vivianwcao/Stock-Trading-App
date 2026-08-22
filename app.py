import os
import json
from snaptrade_client import SnapTrade, SnapTradeAuth
from dotenv import load_dotenv
from pprint import pprint
from datetime import date

load_dotenv()

start_date = date(2022, 1, 1)
end_date = date(2026, 8, 22)

snaptrade = SnapTrade(
    # Personal auth means your API key is the user context.
    auth=SnapTradeAuth.personal_api_key(
        client_id=os.environ["SNAPTRADE_CLIENT_ID"],
        consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
    )
)
# # List all accounts
# res = snaptrade.account_information.list_user_accounts()
# with open("accounts.json", "w", encoding="utf-8") as f:
#     json.dump(res.body, f, indent=2, default=str)

# # List account activities (updates every 24 hours + 1 day delay)
# res2 = snaptrade.account_information.get_account_activities(
#     account_id="12740fda-39c7-4199-b569-c3f8ac982a6c",
#     limit=2000,
#     start_date=start_date,
#     end_date=end_date,
#     type="BUY,SELL,INTEREST,DIVIDEND,WITHDRAWAL,REI,STOCK_DIVIDEND,FEE,TAX,TRANSFER,SPLIT",
# )
# with open("activities.json", "w", encoding="utf-8") as f:
#     json.dump(res2.body, f, indent=2, default=str)

# # List all account positions
# res3 = snaptrade.account_information.get_all_account_positions(
#     account_id="12740fda-39c7-4199-b569-c3f8ac982a6c"
# )
# with open("positions.json", "w", encoding="utf-8") as f:
#     json.dump(res3.body, f, indent=2, default=str)

# # List all orders - last 24 hours
# res4 = snaptrade.account_information.get_user_account_recent_orders(
#     account_id="12740fda-39c7-4199-b569-c3f8ac982a6c"
# )
# with open("24hr_orders.json", "w", encoding="utf-8") as f:
#     json.dump(res4.body, f, indent=2, default=str)

# List all orders - past (30) days
res5 = snaptrade.account_information.get_user_account_orders(
    account_id="12740fda-39c7-4199-b569-c3f8ac982a6c", days=30
)
with open("all_orders.json", "w", encoding="utf-8") as f:
    json.dump(res5.body, f, indent=2, default=str)
