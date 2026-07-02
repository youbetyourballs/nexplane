# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_conditional_access", "policies": [], "count": 0}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with await get_graph_client(token, connector) as client:
        resp = await client.get("/identity/conditionalAccess/policies")
        resp.raise_for_status()
        policies = [{"id": p["id"], "name": p.get("displayName"), "state": p.get("state")} for p in resp.json().get("value", [])]
    return {"action": "discover_conditional_access", "policies": policies, "count": len(policies)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
