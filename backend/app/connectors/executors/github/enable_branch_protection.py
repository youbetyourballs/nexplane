# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]
    branch = parameters["branch"]
    if not creds:
        return {"action": "enable_branch_protection", "repo": repo, "branch": branch, "enabled": True}
    from ._client import get_client
    org = creds["org"]
    protection = {"required_status_checks": None, "enforce_admins": True, "required_pull_request_reviews": {"required_approving_review_count": 1}, "restrictions": None}
    async with await get_client(connector) as client:
        resp = await client.put(f"/repos/{org}/{repo}/branches/{branch}/protection", json=protection)
        resp.raise_for_status()
    return {"action": "enable_branch_protection", "repo": repo, "branch": branch, "enabled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "remove branch protection manually if needed"}
