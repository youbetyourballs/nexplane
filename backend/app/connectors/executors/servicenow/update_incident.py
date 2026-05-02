async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    sys_id = parameters["sys_id"]
    fields = parameters["fields"]
    if not creds:
        return {"action": "update_incident", "sys_id": sys_id, "updated": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.patch(f"/incident/{sys_id}", json=fields)
        resp.raise_for_status()
    return {"action": "update_incident", "sys_id": sys_id, "updated": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous field values not captured — restore manually"}
