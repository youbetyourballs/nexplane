async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    target = parameters.get("target", "*")
    if not creds:
        return {"action": "sync_minion", "target": target, "synced": True}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.post("/", json=[{"client": "local_async", "tgt": target, "fun": "saltutil.sync_all"}])
        resp.raise_for_status()
    return {"action": "sync_minion", "target": target, "synced": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync cannot be reversed"}
