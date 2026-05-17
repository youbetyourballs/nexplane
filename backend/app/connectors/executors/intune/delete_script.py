async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    script_id = parameters["script_id"]
    if not creds:
        return {"action": "delete_script", "script_id": script_id, "status": "deleted"}

    from ._client import get_token, graph_delete

    token = get_token(creds)
    graph_delete(token, f"/deviceManagement/deviceManagementScripts/{script_id}")
    return {"action": "delete_script", "script_id": script_id, "status": "deleted"}
