async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_stacks", "stacks": [{"name": "production", "project": "myapp", "last_update": "2026-01-01"}], "count": 1}
    from ._client import get_client
    org = creds["organization"]
    async with get_client(creds) as client:
        resp = await client.get(f"/user/stacks", params={"organization": org})
        resp.raise_for_status()
        stacks = [{"name": s["stackName"], "project": s.get("projectName"), "org": s.get("orgName")} for s in resp.json().get("stacks", [])]
    return {"action": "discover_stacks", "stacks": stacks, "count": len(stacks)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
