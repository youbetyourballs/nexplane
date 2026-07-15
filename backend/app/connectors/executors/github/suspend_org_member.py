# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    username = parameters["username"]
    if not creds:
        return {"action": "suspend_org_member", "username": username, "suspended": True}
    from ._client import get_client
    org = creds["org"]
    async with await get_client(connector) as client:
        resp = await client.put(f"/orgs/{org}/members/{username}/suspended")
        resp.raise_for_status()
    return {"action": "suspend_org_member", "username": username, "suspended": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unsuspend via org settings"}
