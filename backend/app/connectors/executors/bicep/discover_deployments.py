import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_deployments", "deployments": [{"name": "mock-deployment", "status": "Succeeded"}], "count": 1}

    from ._client import arm_get
    sub = creds["subscription_id"]
    resource_group = parameters.get("resource_group")
    if resource_group:
        path = f"/subscriptions/{sub}/resourceGroups/{resource_group}/providers/Microsoft.Resources/deployments"
    else:
        path = f"/subscriptions/{sub}/providers/Microsoft.Resources/deployments"
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, lambda: arm_get(creds, path))
    items = data.get("value", [])
    deployments = [
        {
            "name": d.get("name"),
            "status": d.get("properties", {}).get("provisioningState"),
            "mode": d.get("properties", {}).get("mode"),
        }
        for d in items
    ]
    return {"action": "discover_deployments", "deployments": deployments, "count": len(deployments)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
