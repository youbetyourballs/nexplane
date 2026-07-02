# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_org_members", "members": [{"login": "mock-user", "role": "member"}], "count": 1}
    from ._client import get_client
    org = creds["org"]
    async with await get_client(connector) as client:
        resp = await client.get(f"/orgs/{org}/members", params={"per_page": 100})
        resp.raise_for_status()
        members = [{"login": m["login"], "id": m["id"]} for m in resp.json()]
    return {"action": "discover_org_members", "members": members, "count": len(members)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
