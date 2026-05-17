async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_id = parameters.get("device_id")

    if not creds:
        return {
            "action": "check_device_compliance",
            "device_id": device_id,
            "devices": [
                {
                    "deviceName": "MOCK-PC-001",
                    "osVersion": "10.0.22621.0",
                    "complianceState": "compliant",
                    "lastSyncDateTime": "2026-05-15T12:00:00Z",
                }
            ],
            "total": 1,
        }

    from ._client import get_token, graph_get

    token = get_token(creds)
    if device_id:
        data = graph_get(
            token,
            f"/deviceManagement/managedDevices/{device_id}"
            "?$select=deviceName,osVersion,complianceState,lastSyncDateTime",
        )
        devices = [data]
    else:
        data = graph_get(
            token,
            "/deviceManagement/managedDevices"
            "?$select=deviceName,osVersion,complianceState,lastSyncDateTime",
        )
        devices = data.get("value", [])

    return {
        "action": "check_device_compliance",
        "device_id": device_id,
        "devices": devices,
        "total": len(devices),
    }
