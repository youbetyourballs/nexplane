# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_policies", "policies": [], "count": 0}
    from ._client import get_session
    async with await get_session(creds, connector) as client:
        resp = await client.get("/urlFilteringRules")
        resp.raise_for_status()
        policies = [{"id": p.get("id"), "name": p.get("name"), "action": p.get("action"), "state": p.get("state")} for p in resp.json()]
    return {"action": "discover_policies", "policies": policies, "count": len(policies)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
