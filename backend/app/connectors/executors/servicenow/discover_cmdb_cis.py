# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_cmdb_cis", "cis": [{"name": "mock-server", "class": "cmdb_ci_server", "ip_address": "10.0.0.1"}], "count": 1}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.get("/cmdb_ci", params={"sysparm_limit": 100, "sysparm_fields": "name,sys_class_name,ip_address,os,status,sys_id"})
        resp.raise_for_status()
        cis = resp.json().get("result", [])
    return {"action": "discover_cmdb_cis", "cis": cis, "count": len(cis)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
