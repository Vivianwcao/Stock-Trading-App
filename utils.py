import json
from pprint import pprint


def create_accounts_mapper():
    with open("./test/accounts.json", "r", encoding="utf-8") as f:
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
        if x["meta"]["status"] == "open" and x["balance"]["total"]["amount"] > 10
    }

    with open("./test/accounts_mapper.json", "w", encoding="utf-8") as f:
        json.dump(accounts_mapper, f, indent=2, default=str)

    print("Accounts mapper updated and saved.")
