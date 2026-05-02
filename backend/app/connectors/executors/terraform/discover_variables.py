async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "discover_variables", "variables": [
            {"id": "var-mock", "key": "environment", "category": "terraform", "sensitive": False}
        ], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/workspaces/{workspace_id}/vars")
        resp.raise_for_status()
        vars_data = [{"id": v["id"], "key": v["attributes"]["key"], "category": v["attributes"]["category"], "sensitive": v["attributes"]["sensitive"]} for v in resp.json().get("data", [])]
    return {"action": "discover_variables", "variables": vars_data, "count": len(vars_data)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
