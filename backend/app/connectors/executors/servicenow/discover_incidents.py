async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_incidents", "incidents": [{"number": "INC0000001", "short_description": "Mock incident", "state": "1", "priority": "2"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/incident", params={"sysparm_query": "active=true^stateIN1,2,3", "sysparm_limit": 100, "sysparm_fields": "number,short_description,state,priority,sys_id,assigned_to"})
        resp.raise_for_status()
        incidents = resp.json().get("result", [])
    return {"action": "discover_incidents", "incidents": incidents, "count": len(incidents)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
