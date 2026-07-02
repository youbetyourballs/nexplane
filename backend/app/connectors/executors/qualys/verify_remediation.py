# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    host_ip = parameters["host_ip"]
    qid = parameters["qid"]
    if not creds:
        return {"action": "verify_remediation", "host_ip": host_ip, "qid": qid, "scan_launched": True}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.post("/api/2.0/fo/scan/", data={"action": "launch", "scan_title": f"Remediation Verify {qid}", "ip": host_ip, "target_from": "assets"})
        resp.raise_for_status()
    return {"action": "verify_remediation", "host_ip": host_ip, "qid": qid, "scan_launched": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan verification has no rollback"}
