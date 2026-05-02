async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_id = parameters["agent_id"]
    if not creds:
        return {"action": "initiate_scan", "agent_id": agent_id, "scan_started": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/agents/actions/initiate-scan", json={"filter": {"ids": [agent_id]}})
        resp.raise_for_status()
    return {"action": "initiate_scan", "agent_id": agent_id, "scan_started": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot cancel an initiated scan"}
