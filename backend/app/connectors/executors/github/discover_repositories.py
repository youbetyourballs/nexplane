# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_repositories", "repositories": [
            {"name": "mock-repo", "visibility": "private", "language": "Python", "default_branch": "main"}
        ], "count": 1}
    from ._client import get_client
    org = creds["org"]
    async with await get_client(connector) as client:
        resp = await client.get(f"/orgs/{org}/repos", params={"per_page": 100, "type": "all"})
        resp.raise_for_status()
        repos = [{"name": r["name"], "visibility": r.get("visibility"), "language": r.get("language"), "default_branch": r.get("default_branch")} for r in resp.json()]
    return {"action": "discover_repositories", "repositories": repos, "count": len(repos)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
