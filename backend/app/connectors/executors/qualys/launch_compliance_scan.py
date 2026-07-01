# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import xml.etree.ElementTree as ET

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    policy_id = parameters["policy_id"]
    if not creds:
        return {"action": "launch_compliance_scan", "policy_id": policy_id, "launched": True}
    from ._client import get_client
    params = {"action": "launch", "policy_id": policy_id}
    if parameters.get("target_ips"):
        params["ip"] = parameters["target_ips"]
    async with get_client(creds) as client:
        resp = await client.post("/api/2.0/fo/scan/compliance/", data=params)
        resp.raise_for_status()
    return {"action": "launch_compliance_scan", "policy_id": policy_id, "launched": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot cancel an initiated scan"}
