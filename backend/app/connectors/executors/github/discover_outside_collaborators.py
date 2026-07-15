# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_outside_collaborators", "collaborators": [], "count": 0}
    from ._client import get_client
    org = creds["org"]
    async with await get_client(connector) as client:
        resp = await client.get(f"/orgs/{org}/outside_collaborators", params={"per_page": 100})
        resp.raise_for_status()
        collaborators = [{"login": u["login"], "id": u["id"]} for u in resp.json()]
    return {"action": "discover_outside_collaborators", "collaborators": collaborators, "count": len(collaborators)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
