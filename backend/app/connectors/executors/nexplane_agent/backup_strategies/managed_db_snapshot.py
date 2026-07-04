# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: RDS managed DB snapshot. Data tier."""
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _rds_client(creds: dict):
    import boto3
    return boto3.client(
        "rds",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds

    aws_connector_id = params.get("aws_connector_id", "")
    db_instance_identifier = params.get("db_instance_identifier", "")
    if not db_instance_identifier:
        raise RuntimeError("managed_db_snapshot: db_instance_identifier is required")

    creds = await _load_aws_creds(aws_connector_id, connector)
    captured_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    snapshot_id = f"nexplane-snap-{db_instance_identifier}-{captured_at}"[:255]

    def _sync_snapshot():
        rds = _rds_client(creds)
        resp = rds.create_db_snapshot(
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceIdentifier=db_instance_identifier,
            Tags=[{"Key": "nexplane:change_type", "Value": "managed_db_snapshot"}],
        )
        snap = resp["DBSnapshot"]
        deadline = time.time() + 600
        while time.time() < deadline:
            desc = rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)["DBSnapshots"]
            if desc and desc[0]["Status"] == "available":
                return desc[0]
            time.sleep(10)
        raise TimeoutError(f"managed_db_snapshot: {snapshot_id} not available within 600s")

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        snap = await loop.run_in_executor(pool, _sync_snapshot)

    artifact_refs = {
        "capture_strategy": "managed_db_snapshot",
        "restore_strategy": "database_restore",
        "backup_tier": "data",
        "captured_at": captured_at,
        "snapshot_id": snapshot_id,
        "snapshot_arn": snap.get("DBSnapshotArn", ""),
        "engine": snap.get("Engine", ""),
        "allocated_storage_gb": snap.get("AllocatedStorage", 0),
        "db_instance_identifier": db_instance_identifier,
        "aws_connector_id": aws_connector_id,
    }
    return {
        "status": "completed",
        "artifact_refs": artifact_refs,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds

    refs = execution_result.get("artifact_refs", {})
    snapshot_id = refs.get("snapshot_id", "")
    aws_connector_id = params.get("aws_connector_id") or refs.get("aws_connector_id", "")
    if not snapshot_id:
        return {"rolled_back": False, "reason": "no snapshot_id in artifact_refs"}

    creds = await _load_aws_creds(aws_connector_id, connector)

    def _sync_delete():
        rds = _rds_client(creds)
        rds.delete_db_snapshot(DBSnapshotIdentifier=snapshot_id)
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)
            except rds.exceptions.DBSnapshotNotFoundFault:
                return True
            except Exception:
                return True
            time.sleep(10)
        return False

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        gone = await loop.run_in_executor(pool, _sync_delete)
    return {"rolled_back": bool(gone), "deleted_snapshot_id": snapshot_id}
