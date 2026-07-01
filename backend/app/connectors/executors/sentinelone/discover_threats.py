# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_threats", "threats": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/threats", params={"limit": 1000, "resolved": "false"})
        resp.raise_for_status()
        threats = [{"id": t["id"], "name": t.get("threatInfo", {}).get("threatName"), "severity": t.get("threatInfo", {}).get("confidenceLevel"), "agent_id": t.get("agentDetectionInfo", {}).get("agentId")} for t in resp.json().get("data", [])]
    return {"action": "discover_threats", "threats": threats, "count": len(threats)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
