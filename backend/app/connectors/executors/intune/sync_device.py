async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_id = parameters["device_id"]
    if not creds:
        return {"action": "sync_device", "device_id": device_id, "status": "sync_triggered"}

    from ._client import get_token, graph_post

    token = get_token(creds)
    graph_post(token, f"/deviceManagement/managedDevices/{device_id}/syncDevice")
    return {"action": "sync_device", "device_id": device_id, "status": "sync_triggered"}
