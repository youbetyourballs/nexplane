import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_auth_methods", "methods": [{"path": "token/", "type": "token"}], "count": 1}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    methods = await loop.run_in_executor(None, lambda: client.sys.list_auth_methods())
    result = [{"path": path, "type": info.get("type")} for path, info in methods.items()]
    return {"action": "discover_auth_methods", "methods": result, "count": len(result)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
