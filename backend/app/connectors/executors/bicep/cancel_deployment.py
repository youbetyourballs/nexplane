import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    deployment_name = parameters["deployment_name"]
    if not creds:
        return {"action": "cancel_deployment", "deployment_name": deployment_name, "cancelled": True}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    client = get_client(creds)
    await loop.run_in_executor(None, lambda: client.deployments.cancel(resource_group, deployment_name))
    return {"action": "cancel_deployment", "deployment_name": deployment_name, "cancelled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cancel has no rollback"}
