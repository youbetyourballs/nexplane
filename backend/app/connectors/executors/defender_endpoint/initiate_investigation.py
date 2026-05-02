async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    if not creds:
        return {"action": "initiate_investigation", "machine_id": machine_id, "investigation_id": "mock-inv-1"}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.post(f"/machines/{machine_id}/startInvestigation", json={"Comment": "Investigation triggered by Nexplane"})
        resp.raise_for_status()
        result = resp.json()
    return {"action": "initiate_investigation", "machine_id": machine_id, "investigation_id": result.get("investigationId")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "investigations cannot be cancelled"}
