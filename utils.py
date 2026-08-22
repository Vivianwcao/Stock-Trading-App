import json
from pprint import pprint

with open("accounts.json", "r", encoding="utf-8") as f:
    data_list = json.load(f)

accounts_mapper = {
    x["number"]: {
        "account_id": x["id"],
        "type": x["name"],
        "balance": x["balance"]["total"]["amount"],
        "first_transaction_date": x["sync_status"]["transactions"][
            "first_transaction_date"
        ],
    }
    for x in data_list
    if x["meta"]["status"] == "open"
}

pprint(accounts_mapper)
