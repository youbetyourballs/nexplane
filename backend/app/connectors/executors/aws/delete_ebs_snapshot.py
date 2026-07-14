# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "EBS snapshot data is permanently deleted — no recovery path without the original volume"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    snapshot_id = parameters.get('snapshot_id', '')

    if not creds:
        return {"action": "delete_ebs_snapshot", "snapshot_id": snapshot_id, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    ec2 = get_boto3_client(creds, 'ec2')
    loop = asyncio.get_event_loop()

    def _call():
        ec2.delete_snapshot(SnapshotId=snapshot_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_ebs_snapshot",
        "snapshot_id": snapshot_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_ebs_snapshot is terminal — snapshot data cannot be recovered"}
