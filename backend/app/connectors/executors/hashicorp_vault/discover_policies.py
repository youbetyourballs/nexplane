import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_policies", "policies": ["default", "root"], "count": 2}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    policies = await loop.run_in_executor(None, lambda: client.sys.list_policies()["data"]["policies"])
    return {"action": "discover_policies", "policies": policies, "count": len(policies)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
