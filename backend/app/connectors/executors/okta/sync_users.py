# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """List all Okta users for discovery. Returns count + user list."""
    creds = getattr(connector, "credentials", {})
    filter_str = parameters.get("filter", "")
    if not creds:
        return {
            "action": "sync_users",
            "count": 2,
            "users": [
                {"id": "00u1mock000001", "login": "alice@example.com", "status": "ACTIVE"},
                {"id": "00u1mock000002", "login": "bob@example.com", "status": "ACTIVE"},
            ],
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }
    import httpx
    from ._client import okta_headers, okta_base
    base = okta_base(creds)
    headers = okta_headers(creds)
    users = []
    url = f"{base}/users?limit=200"
    if filter_str:
        url += f"&filter={filter_str}"
    async with httpx.AsyncClient() as client:
        while url:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            for u in resp.json():
                profile = u.get("profile", {})
                users.append({
                    "id": u["id"],
                    "login": profile.get("login"),
                    "email": profile.get("email"),
                    "first_name": profile.get("firstName"),
                    "last_name": profile.get("lastName"),
                    "status": u.get("status"),
                    "created": u.get("created"),
                    "last_login": u.get("lastLogin"),
                })
            link = resp.headers.get("Link", "")
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")
            url = next_url
    return {
        "action": "sync_users",
        "count": len(users),
        "users": users,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync_users is read-only, no rollback needed"}
