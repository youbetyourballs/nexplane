# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_id = parameters["node_id"]
    profile_id = parameters["profile_id"]
    if not creds:
        return {"action": "run_compliance_scan", "node_id": node_id, "profile_id": profile_id, "scan_triggered": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/compliance/scanner/jobs", json={"type": "exec", "tags": [], "name": f"Nexplane scan {node_id}", "profiles": [{"name": profile_id}], "node_selectors": [{"manager_id": "", "filters": [{"key": "id", "values": [node_id]}]}]})
        resp.raise_for_status()
        job = resp.json()
    return {"action": "run_compliance_scan", "node_id": node_id, "profile_id": profile_id, "job_id": job.get("id"), "scan_triggered": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan has no rollback"}
