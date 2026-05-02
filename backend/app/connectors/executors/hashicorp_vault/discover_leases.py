import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_leases", "leases": [], "count": 0}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    try:
        result = await loop.run_in_executor(None, lambda: client.sys.list_leases(prefix=""))
        leases = result.get("data", {}).get("keys", [])
    except Exception:
        leases = []
    return {"action": "discover_leases", "leases": leases, "count": len(leases)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
