# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    targets = parameters["targets"]
    if not creds:
        return {"action": "trigger_scan", "task_id": "mock-task-id", "targets": targets, "status": "queued"}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.post("/org/tasks/scan", params={"_oid": creds["org_id"]}, json={"targets": targets, "rate": 1000})
        resp.raise_for_status()
        task = resp.json()
    return {"action": "trigger_scan", "task_id": task.get("id"), "targets": targets, "status": task.get("status")}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan tasks cannot be cancelled via API"}
