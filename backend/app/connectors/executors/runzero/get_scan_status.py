# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    task_id = parameters["task_id"]
    if not creds:
        return {"action": "get_scan_status", "task_id": task_id, "status": "completed", "progress": 100}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.get(f"/org/tasks/{task_id}", params={"_oid": creds["org_id"]})
        resp.raise_for_status()
        task = resp.json()
    return {"action": "get_scan_status", "task_id": task_id, "status": task.get("status"), "progress": task.get("progress")}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "status check has no rollback"}
