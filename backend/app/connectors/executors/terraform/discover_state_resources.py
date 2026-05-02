async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "discover_state_resources", "resources": [
            {"name": "aws_instance.web", "type": "aws_instance", "provider": "aws"}
        ], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/workspaces/{workspace_id}/current-state-version-outputs")
        resp.raise_for_status()
        resources = resp.json().get("data", [])
    return {"action": "discover_state_resources", "resources": resources, "count": len(resources)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
