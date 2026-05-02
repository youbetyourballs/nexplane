import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_deployments", "deployments": [{"name": "mock-deployment", "status": "Succeeded"}], "count": 1}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    client = get_client(creds)
    deployments_list = await loop.run_in_executor(None, lambda: list(client.deployments.list_at_subscription_scope()))
    deployments = [{"name": d.name, "status": d.properties.provisioning_state, "mode": str(d.properties.mode)} for d in deployments_list]
    return {"action": "discover_deployments", "deployments": deployments, "count": len(deployments)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
