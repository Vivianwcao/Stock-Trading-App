import os

from dotenv import load_dotenv
from snaptrade_client import SnapTrade, SnapTradeAuth

load_dotenv()


def get_snaptrade_auth():
    return SnapTrade(
        # Personal auth means your API key is the user context.
        auth=SnapTradeAuth.personal_api_key(
            client_id=os.environ["SNAPTRADE_CLIENT_ID"],
            consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
        )
    )
