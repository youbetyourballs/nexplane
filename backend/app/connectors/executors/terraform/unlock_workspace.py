async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "unlock_workspace", "workspace_id": workspace_id, "locked": False}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post(f"/workspaces/{workspace_id}/actions/unlock")
        resp.raise_for_status()
    return {"action": "unlock_workspace", "workspace_id": workspace_id, "locked": False}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unlock rollback would lock — use lock_workspace explicitly"}
