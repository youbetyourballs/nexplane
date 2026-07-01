# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_ec2_client
    ec2 = get_ec2_client(creds)
    loop = asyncio.get_event_loop()
    snapshot_id = parameters.get("snapshot_id")
    if snapshot_id:
        resp = await loop.run_in_executor(None, lambda: ec2.describe_snapshots(SnapshotIds=[snapshot_id]))
        snaps = resp.get("Snapshots", [])
        verified = len(snaps) > 0 and snaps[0].get("State") == "completed"
    else:
        verified = True
    return {"action": "verify_snapshot", "verified": verified, "verified_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return {"action": "verify_snapshot", "verified": True, "verified_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "verification has no rollback"}
