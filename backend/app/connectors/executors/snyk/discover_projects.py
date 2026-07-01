# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_projects", "projects": [{"id": "mock-proj-1", "name": "my-app", "type": "npm", "issue_counts": {"critical": 0, "high": 2}}], "count": 1}
    from ._client import get_client
    org_id = creds["org_id"]
    async with get_client(creds) as client:
        resp = await client.get(f"/orgs/{org_id}/projects", params={"version": "2023-05-29", "limit": 100})
        resp.raise_for_status()
        projects = [{"id": p["id"], "name": p["attributes"]["name"], "type": p["attributes"].get("type")} for p in resp.json().get("data", [])]
    return {"action": "discover_projects", "projects": projects, "count": len(projects)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
