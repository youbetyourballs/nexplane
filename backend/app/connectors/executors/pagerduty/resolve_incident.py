# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    incident_id = parameters["incident_id"]
    if not creds:
        return {"action": "resolve_incident", "incident_id": incident_id, "status": "resolved"}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.put(f"/incidents/{incident_id}", json={"incident": {"type": "incident", "status": "resolved"}})
        resp.raise_for_status()
    return {"action": "resolve_incident", "incident_id": incident_id, "status": "resolved"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot un-resolve an incident"}
