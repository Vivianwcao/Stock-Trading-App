def update account_nickname(client, account_id: str, nickname: str | None):
    clean_nickname = nickname.strip() or None if nickname else None
    client.execute(
        """
        update accounts
        set nickname = ?
        where id = ?
        """,
        (clean_nickname, account_id)
    )
    logger.info("Updated nickname: %s for account: %s.", clean_nickname, account_id)
    return clean_nickname