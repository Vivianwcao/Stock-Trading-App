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
# # get all account_ids
# res = snaptrade.account_information.list_user_accounts()
# with open("accounts.json", "w", encoding="utf-8") as f:
#     json.dump(res.body, f, indent=2, default=str)

# response = snaptrade.account_information.get_account_activities(
#     account_id="12740fda-39c7-4199-b569-c3f8ac982a6c",
#     limit=2000,
#     start_date=start_date,
#     end_date=end_date,
#     type="BUY,SELL,INTEREST,DIVIDEND,WITHDRAWAL,REI,STOCK_DIVIDEND,FEE,TAX,TRANSFER,SPLIT",
# )

# with open("output.json", "w", encoding="utf-8") as f:
#     json.dump(response.body, f, indent=2, default=str)
