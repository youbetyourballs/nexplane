async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    if not creds:
        return {"action": "unisolate_machine", "machine_id": machine_id, "status": "Succeeded"}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.post(f"/machines/{machine_id}/unisolate", json={"Comment": "Unisolated by Nexplane"})
        resp.raise_for_status()
        action = resp.json()
    return {"action": "unisolate_machine", "machine_id": machine_id, "status": action.get("status")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unisolate rollback would isolate — use isolate_machine explicitly"}
