# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_saved_searches", "searches": [], "count": 0}
    from ._client import get_rest_client
    async with await get_rest_client(connector) as client:
        resp = await client.get("/services/saved/searches", params={"output_mode": "json", "count": 100})
        resp.raise_for_status()
        searches = [{"name": s["name"], "search": s["content"].get("search")} for s in resp.json().get("entry", [])]
    return {"action": "discover_saved_searches", "searches": searches, "count": len(searches)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
