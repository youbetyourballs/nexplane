# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    node_id = parameters["node_id"]
    profile_id = parameters["profile_id"]
    if not creds:
        return {"action": "assign_profile", "node_id": node_id, "profile_id": profile_id, "assigned": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post(f"/nodes/{node_id}", json={"profiles": [profile_id]})
        resp.raise_for_status()
    return {"action": "assign_profile", "node_id": node_id, "profile_id": profile_id, "assigned": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unassign profile manually if needed"}
