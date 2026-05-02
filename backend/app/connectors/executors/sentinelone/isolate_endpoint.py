async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_id = parameters["agent_id"]
    if not creds:
        return {"action": "isolate_endpoint", "agent_id": agent_id, "isolated": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/agents/actions/disconnect", json={"filter": {"ids": [agent_id]}})
        resp.raise_for_status()
    return {"action": "isolate_endpoint", "agent_id": agent_id, "isolated": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.sentinelone.reconnect_endpoint import execute as reconnect
    return await reconnect(parameters, [], connector)
