async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_groups", "groups": [{"id": "mock-group", "name": "Default", "type": "static"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/groups", params={"limit": 200})
        resp.raise_for_status()
        groups = [{"id": g["id"], "name": g.get("name"), "type": g.get("type")} for g in resp.json().get("data", [])]
    return {"action": "discover_groups", "groups": groups, "count": len(groups)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
