async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    project = parameters["project"]
    stack = parameters["stack"]
    if not creds:
        return {"action": "discover_stack_history", "updates": [], "count": 0}
    from ._client import get_client
    org = creds["organization"]
    async with get_client(creds) as client:
        resp = await client.get(f"/stacks/{org}/{project}/{stack}/updates", params={"pageSize": 20})
        resp.raise_for_status()
        updates = [{"version": u.get("version"), "result": u.get("result"), "started": u.get("startTime")} for u in resp.json().get("updates", [])]
    return {"action": "discover_stack_history", "updates": updates, "count": len(updates)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
