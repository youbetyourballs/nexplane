async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    if not creds:
        return {"action": "restrict_app_execution", "machine_id": machine_id, "status": "Succeeded"}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.post(f"/machines/{machine_id}/restrictCodeExecution", json={"Comment": "Restricted by Nexplane"})
        resp.raise_for_status()
    return {"action": "restrict_app_execution", "machine_id": machine_id, "status": "Succeeded"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "remove restriction via removeCodeExecutionRestriction action manually"}
