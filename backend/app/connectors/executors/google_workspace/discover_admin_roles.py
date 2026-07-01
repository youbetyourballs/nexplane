# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_admin_roles", "assignments": [], "count": 0}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")
    result = await loop.run_in_executor(None, lambda: service.roleAssignments().list(customer="my_customer", maxResults=100).execute())
    assignments = [{"role_id": a.get("roleId"), "assigned_to": a.get("assignedTo"), "scope_type": a.get("scopeType")} for a in result.get("items", [])]
    return {"action": "discover_admin_roles", "assignments": assignments, "count": len(assignments)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
