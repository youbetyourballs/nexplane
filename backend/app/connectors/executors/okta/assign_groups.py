# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone
import httpx
from ._client import okta_headers, okta_base


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    group_ids: list[str] = parameters.get("group_ids", [])

    if not creds:
        return {
            "action": "assign_okta_groups",
            "user_id": user_id,
            "assigned_groups": group_ids,
            "simulated": True,
            "assigned_at": datetime.now(timezone.utc).isoformat(),
        }

    base = okta_base(creds)
    headers = okta_headers(creds)
    assigned = []
    async with httpx.AsyncClient() as client:
        for gid in group_ids:
            resp = await client.put(f"{base}/groups/{gid}/users/{user_id}", headers=headers)
            resp.raise_for_status()
            assigned.append(gid)

    return {
        "action": "assign_okta_groups",
        "user_id": user_id,
        "assigned_groups": assigned,
        "assigned_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = execution_result.get("user_id") or parameters.get("user_id")
    assigned_groups: list[str] = execution_result.get("assigned_groups", [])

    if not creds:
        return {"rolled_back": True, "removed_groups": assigned_groups, "simulated": True}

    base = okta_base(creds)
    headers = okta_headers(creds)
    removed = []
    async with httpx.AsyncClient() as client:
        for gid in assigned_groups:
            resp = await client.delete(f"{base}/groups/{gid}/users/{user_id}", headers=headers)
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()
            removed.append(gid)

    return {
        "rolled_back": True,
        "user_id": user_id,
        "removed_groups": removed,
    }
