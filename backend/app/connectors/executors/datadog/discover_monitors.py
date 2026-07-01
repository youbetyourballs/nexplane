# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_monitors", "monitors": [], "count": 0}
    from ._client import get_v1_client
    async with get_v1_client(creds) as client:
        resp = await client.get("/monitor", params={"page_size": 100})
        resp.raise_for_status()
        monitors = [{"id": m["id"], "name": m["name"], "type": m["type"], "status": m.get("overall_state")} for m in resp.json()]
    return {"action": "discover_monitors", "monitors": monitors, "count": len(monitors)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
