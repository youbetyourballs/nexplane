# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]
    if not creds:
        return {"action": "enable_secret_scanning", "repo": repo, "enabled": True}
    from ._client import get_client
    org = creds["org"]
    async with await get_client(connector) as client:
        resp = await client.patch(f"/repos/{org}/{repo}", json={"security_and_analysis": {"secret_scanning": {"status": "enabled"}}})
        resp.raise_for_status()
    return {"action": "enable_secret_scanning", "repo": repo, "enabled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "disable secret scanning manually if needed"}
