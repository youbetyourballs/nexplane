# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    incident_id = parameters["incident_id"]
    if not creds:
        return {"action": "get_incident_notes", "incident_id": incident_id, "notes": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/incidents/{incident_id}/notes")
        resp.raise_for_status()
        notes = [{"id": n["id"], "content": n["content"], "created_at": n["created_at"], "user": n.get("user", {}).get("email")} for n in resp.json().get("notes", [])]
    return {"action": "get_incident_notes", "incident_id": incident_id, "notes": notes, "count": len(notes)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "status check has no rollback"}
