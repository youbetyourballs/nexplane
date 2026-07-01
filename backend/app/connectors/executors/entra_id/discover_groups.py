# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_groups", "groups": [{"id": "mock-group", "name": "All Users", "type": "security"}], "count": 1}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get("/groups", params={"$select": "id,displayName,groupTypes,membershipRule", "$top": 999})
        resp.raise_for_status()
        groups = [{"id": g["id"], "name": g.get("displayName"), "types": g.get("groupTypes", [])} for g in resp.json().get("value", [])]
    return {"action": "discover_groups", "groups": groups, "count": len(groups)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
