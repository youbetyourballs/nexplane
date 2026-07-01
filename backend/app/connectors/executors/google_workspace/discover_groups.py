# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_groups", "groups": [{"email": "security@example.com", "name": "Security Team"}], "count": 1}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")
    domain = creds["domain"]
    result = await loop.run_in_executor(None, lambda: service.groups().list(domain=domain, maxResults=200).execute())
    groups = [{"email": g.get("email"), "name": g.get("name"), "direct_members_count": g.get("directMembersCount")} for g in result.get("groups", [])]
    return {"action": "discover_groups", "groups": groups, "count": len(groups)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
