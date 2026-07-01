# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_logs_indexes", "indexes": [{"name": "main", "daily_limit": 0}], "count": 1}
    from ._client import get_v1_client
    async with get_v1_client(creds) as client:
        resp = await client.get("/logs/config/indexes")
        resp.raise_for_status()
        indexes = [{"name": i["name"], "daily_limit": i.get("daily_limit", {}).get("num_events")} for i in resp.json().get("indexes", [])]
    return {"action": "discover_logs_indexes", "indexes": indexes, "count": len(indexes)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
