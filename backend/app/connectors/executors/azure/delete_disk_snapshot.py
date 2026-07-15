# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    snapshot_name = parameters["snapshot_name"]

    if not creds:
        return {"action": "delete_disk_snapshot", "resource_group": resource_group, "snapshot_name": snapshot_name, "deleted": True, "mock": True}

    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, lambda: client.snapshots.begin_delete(resource_group, snapshot_name).result()
    )
    return {
        "action": "delete_disk_snapshot",
        "resource_group": resource_group,
        "snapshot_name": snapshot_name,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_disk_snapshot is terminal"}
