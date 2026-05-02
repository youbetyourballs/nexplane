async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_id = parameters["agent_id"]
    if not creds:
        return {"action": "reconnect_endpoint", "agent_id": agent_id, "isolated": False}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/agents/actions/connect", json={"filter": {"ids": [agent_id]}})
        resp.raise_for_status()
    return {"action": "reconnect_endpoint", "agent_id": agent_id, "isolated": False}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reconnect rollback would isolate — use isolate_endpoint explicitly"}
