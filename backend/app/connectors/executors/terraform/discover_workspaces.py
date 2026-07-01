# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_workspaces", "workspaces": [
            {"id": "ws-mock", "name": "production", "environment": "production", "resource_count": 42, "last_run_status": "applied"}
        ], "count": 1}
    from ._client import get_client
    org = creds["organization"]
    async with get_client(creds) as client:
        resp = await client.get(f"/organizations/{org}/workspaces", params={"page[size]": 100})
        resp.raise_for_status()
        items = resp.json().get("data", [])
        workspaces = [{"id": w["id"], "name": w["attributes"]["name"], "environment": w["attributes"].get("environment"), "resource_count": w["attributes"].get("resource-count", 0)} for w in items]
    return {"action": "discover_workspaces", "workspaces": workspaces, "count": len(workspaces)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
