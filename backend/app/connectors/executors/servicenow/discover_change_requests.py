async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_change_requests", "changes": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/change_request", params={"sysparm_query": "active=true", "sysparm_limit": 100, "sysparm_fields": "number,short_description,state,type,risk,sys_id"})
        resp.raise_for_status()
        changes = resp.json().get("result", [])
    return {"action": "discover_change_requests", "changes": changes, "count": len(changes)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
