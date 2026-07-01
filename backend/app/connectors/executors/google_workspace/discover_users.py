# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_users", "users": [{"email": "alice@example.com", "status": "active", "is_admin": False}], "count": 1}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")
    domain = creds["domain"]
    result = await loop.run_in_executor(None, lambda: service.users().list(domain=domain, maxResults=500).execute())
    users = [{"email": u.get("primaryEmail"), "status": "suspended" if u.get("suspended") else "active", "is_admin": u.get("isAdmin"), "last_login": u.get("lastLoginTime")} for u in result.get("users", [])]
    return {"action": "discover_users", "users": users, "count": len(users)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
