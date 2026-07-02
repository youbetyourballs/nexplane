# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    ci_data = parameters["ci_data"]
    name = ci_data.get("name", "unknown")
    if not creds:
        return {"action": "sync_cmdb", "name": name, "synced": True}
    from ._client import get_client
    async with await get_client(connector) as client:
        # Check if CI already exists
        resp = await client.get("/cmdb_ci", params={"sysparm_query": f"name={name}", "sysparm_limit": 1, "sysparm_fields": "sys_id"})
        resp.raise_for_status()
        existing = resp.json().get("result", [])
        if existing:
            sys_id = existing[0]["sys_id"]
            resp2 = await client.patch(f"/cmdb_ci/{sys_id}", json=ci_data)
        else:
            resp2 = await client.post("/cmdb_ci", json=ci_data)
        resp2.raise_for_status()
    return {"action": "sync_cmdb", "name": name, "synced": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "CMDB sync rollback not supported automatically"}
