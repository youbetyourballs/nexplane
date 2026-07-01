# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_audit_logs", "activities": [], "count": 0}
    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "reports_v1")
    app_name = parameters.get("application_name", "admin")
    result = await loop.run_in_executor(None, lambda: service.activities().list(userKey="all", applicationName=app_name, maxResults=100).execute())
    activities = [{"id": a.get("id", {}).get("uniqueQualifier"), "actor": a.get("actor", {}).get("email"), "events": [e.get("name") for e in a.get("events", [])], "time": a.get("id", {}).get("time")} for a in result.get("items", [])]
    return {"action": "discover_audit_logs", "activities": activities, "count": len(activities)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
