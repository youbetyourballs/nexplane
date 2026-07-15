# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]
    if not creds:
        return {"action": "enable_dependabot", "repo": repo, "enabled": True}
    from ._client import get_client
    org = creds["org"]
    async with await get_client(connector) as client:
        resp = await client.put(f"/repos/{org}/{repo}/vulnerability-alerts")
        resp.raise_for_status()
    return {"action": "enable_dependabot", "repo": repo, "enabled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "disable Dependabot manually if needed"}
