# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action": "discover_laps_devices",
            "devices": [
                {
                    "id": "mock-laps-device-001",
                    "deviceName": "MOCK-PC-001",
                    "lastBackupDateTime": "2026-05-14T08:00:00Z",
                }
            ],
            "total": 1,
        }

    from ._client import get_token, graph_get, GRAPH_BASE

    token = get_token(creds)
    devices = []
    path = "/deviceLocalCredentials"
    while path:
        data = graph_get(token, path)
        devices.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink", "")
        if next_link:
            path = next_link.replace(GRAPH_BASE, "")
        else:
            path = None

    return {
        "action": "discover_laps_devices",
        "devices": devices,
        "total": len(devices),
    }
