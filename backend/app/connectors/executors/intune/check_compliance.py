# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_id = parameters["device_id"]
    if not creds:
        return {
            "action": "check_compliance",
            "device_id": device_id,
            "deviceName": "MOCK-PC-001",
            "complianceState": "compliant",
            "deviceEnrollmentType": "windowsCoManagement",
            "lastSyncDateTime": "2026-05-15T12:00:00Z",
            "osVersion": "10.0.22621.0",
        }

    from ._client import get_token, graph_get

    token = get_token(creds)
    data = graph_get(
        token,
        f"/deviceManagement/managedDevices/{device_id}"
        "?$select=complianceState,deviceName,deviceEnrollmentType,lastSyncDateTime,osVersion",
    )
    return {
        "action": "check_compliance",
        "device_id": device_id,
        "deviceName": data.get("deviceName"),
        "complianceState": data.get("complianceState"),
        "deviceEnrollmentType": data.get("deviceEnrollmentType"),
        "lastSyncDateTime": data.get("lastSyncDateTime"),
        "osVersion": data.get("osVersion"),
    }
