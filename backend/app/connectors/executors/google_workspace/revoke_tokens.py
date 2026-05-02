import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    if not creds:
        return {"action": "revoke_tokens", "user_email": user_email, "revoked": True}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")
    # List and revoke all tokens
    tokens_result = await loop.run_in_executor(None, lambda: service.tokens().list(userKey=user_email).execute())
    for token in tokens_result.get("items", []):
        client_id = token.get("clientId")
        if client_id:
            await loop.run_in_executor(None, lambda cid=client_id: service.tokens().delete(userKey=user_email, clientId=cid).execute())
    return {"action": "revoke_tokens", "user_email": user_email, "revoked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "revoked tokens cannot be restored"}
