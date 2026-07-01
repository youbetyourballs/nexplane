# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time
from datetime import datetime, timezone


def _get_rds_client(creds: dict):
    from ._client import get_boto3_client
    return get_boto3_client(creds, "rds")


async def _real_execute(creds: dict, parameters: dict) -> dict:
    rds = _get_rds_client(creds)
    loop = asyncio.get_event_loop()
    db_instance_id = parameters.get("db_instance_id") or parameters.get("db_instance_identifier", "")
    name = parameters.get("backup_name", "nexplane-rds-backup")
    retention = int(parameters.get("retention_days", 30))
    snapshot_id = parameters.get("snapshot_identifier") or f"nexplane-{db_instance_id}-{int(time.time())}"

    def _call():
        return rds.create_db_snapshot(
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceIdentifier=db_instance_id,
            Tags=[
                {"Key": "Name",          "Value": name},
                {"Key": "RetentionDays", "Value": str(retention)},
                {"Key": "ManagedBy",     "Value": "nexplane"},
            ],
        )

    resp = await loop.run_in_executor(None, _call)
    return {
        "action":      "create_rds_snapshot",
        "snapshot_id": snapshot_id,
        "status":      resp["DBSnapshot"]["Status"],
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action":      "create_rds_snapshot",
            "snapshot_id": f"nexplane-{parameters.get('db_instance_id', 'db')}-mock",
            "status":      "creating",
            "mock":        True,
        }
    return await _real_execute(creds, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    snapshot_id = execution_result.get("snapshot_id")
    if not snapshot_id or not creds:
        return {"rolled_back": False, "reason": "no snapshot_id or no credentials"}
    rds = _get_rds_client(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: rds.delete_db_snapshot(DBSnapshotIdentifier=snapshot_id))
    return {"rolled_back": True, "deleted_snapshot_id": snapshot_id}
