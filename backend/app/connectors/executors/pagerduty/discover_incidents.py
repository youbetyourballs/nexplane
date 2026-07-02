# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_incidents", "incidents": [], "count": 0}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.get("/incidents", params={"statuses[]": ["triggered", "acknowledged"], "limit": 100})
        resp.raise_for_status()
        incidents = [{"id": i["id"], "title": i["title"], "status": i["status"], "urgency": i.get("urgency"), "service_id": i.get("service", {}).get("id")} for i in resp.json().get("incidents", [])]
    return {"action": "discover_incidents", "incidents": incidents, "count": len(incidents)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
