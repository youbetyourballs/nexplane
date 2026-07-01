# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action": "discover_managed_devices",
            "devices": [
                {
                    "id": "mock-device-1",
                    "deviceName": "MOCK-PC-001",
                    "complianceState": "compliant",
                    "osVersion": "10.0.22621.0",
                    "lastSyncDateTime": "2026-05-15T12:00:00Z",
                    "operatingSystem": "Windows",
                }
            ],
            "total": 1,
        }

    from ._client import get_token, graph_get

    token = get_token(creds)
    devices = []
    path = "/deviceManagement/managedDevices"
    while path:
        data = graph_get(token, path)
        devices.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink", "")
        if next_link:
            # nextLink is a full URL; strip the base so graph_get can prepend it
            from ._client import GRAPH_BASE
            path = next_link.replace(GRAPH_BASE, "")
        else:
            path = None

    return {
        "action": "discover_managed_devices",
        "devices": devices,
        "total": len(devices),
    }
