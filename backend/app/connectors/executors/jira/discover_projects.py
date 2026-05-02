async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_projects", "projects": [
            {"key": "SEC", "name": "Security", "type": "software", "lead": "alice"}
        ], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/project/search", params={"maxResults": 200})
        resp.raise_for_status()
        projects = [{"key": p["key"], "name": p["name"], "type": p.get("projectTypeKey"), "lead": p.get("lead", {}).get("displayName")} for p in resp.json().get("values", [])]
    return {"action": "discover_projects", "projects": projects, "count": len(projects)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
