import sqlite3
import os
import json
from snaptrade import get_snaptrade_auth
import logging

# ── Logging ─────────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)  # required for lambda
logging.basicConfig(level=logging.INFO)  # required for local


def handler(event, context):
    logger.info(json.dumps(event))

    # generate tables


if __name__ == "__main__":
    snaptrade = get_snaptrade_auth()

    # 1. Connect to local database file (creates portfolio.db automatically)
    with sqlite3.connect("stocks.db") as conn:
        # return query rows as dictionary-like objects
        conn.row_factory = sqlite3.Row
        # in SQLite, foreign_keys is a per-connection setting
        conn.execute("PRAGMA foreign_keys = ON")
        # conn.executescript("drop table activities
        # ")
        # init_db(conn)
