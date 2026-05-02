async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    if not creds:
        return {"action": "isolate_machine", "machine_id": machine_id, "status": "Succeeded"}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.post(f"/machines/{machine_id}/isolate", json={"Comment": parameters.get("comment", "Isolated by Nexplane"), "IsolationType": "Full"})
        resp.raise_for_status()
        action = resp.json()
    return {"action": "isolate_machine", "machine_id": machine_id, "action_id": action.get("id"), "status": action.get("status")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.defender_endpoint.unisolate_machine import execute as unisolate
    return await unisolate(parameters, [], connector)
