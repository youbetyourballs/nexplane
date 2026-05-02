async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    sys_id = parameters["sys_id"]
    if not creds:
        return {"action": "resolve_incident", "sys_id": sys_id, "state": "6"}
    from ._client import get_client
    body = {"state": "6", "close_code": "Solved (Permanently)", "close_notes": parameters.get("resolution_notes", "Resolved by Nexplane")}
    async with get_client(creds) as client:
        resp = await client.patch(f"/incident/{sys_id}", json=body)
        resp.raise_for_status()
    return {"action": "resolve_incident", "sys_id": sys_id, "state": "6"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot un-resolve — reopen manually"}
