# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    scan_type = parameters.get("scan_type", "Full")
    if not creds:
        return {"action": "run_antivirus_scan", "machine_id": machine_id, "scan_type": scan_type, "started": True}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.post(f"/machines/{machine_id}/runAntiVirusScan", json={"Comment": "Scan triggered by Nexplane", "ScanType": scan_type})
        resp.raise_for_status()
    return {"action": "run_antivirus_scan", "machine_id": machine_id, "scan_type": scan_type, "started": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot cancel an initiated AV scan"}
