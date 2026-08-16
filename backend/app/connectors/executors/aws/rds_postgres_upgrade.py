# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS RDS PostgreSQL instance version upgrade executor.

Triggers a major or minor version upgrade on an RDS PostgreSQL instance.
AllowMajorVersionUpgrade=True is always passed to allow cross-major upgrades.

Rollback is PARTIAL — RDS does not support engine downgrades.
Rollback returns the latest automated snapshot identifier for manual restore.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

from app.connectors.executors.aws._client import get_boto3_client

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

_POLL_INTERVAL = 30
_POLL_TIMEOUT = 3600


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    db_id = parameters["db_instance_identifier"]
    target_version = parameters["target_version"]
    apply_immediately = parameters.get("apply_immediately", True)
    creds = connector.credentials

    def _describe():
        rds = get_boto3_client(creds, "rds")
        resp = rds.describe_db_instances(DBInstanceIdentifier=db_id)
        return resp["DBInstances"][0]

    inst = await _run(_describe)
    current_version = inst["EngineVersion"]

    if current_version == target_version:
        return {
            "status": "already_at_version",
            "db_instance_identifier": db_id,
            "previous_version": current_version,
            "current_version": current_version,
        }

    def _get_snapshot():
        try:
            rds = get_boto3_client(creds, "rds")
            resp = rds.describe_db_snapshots(
                DBInstanceIdentifier=db_id,
                SnapshotType="automated",
                MaxRecords=1,
            )
            snaps = sorted(
                resp.get("DBSnapshots", []),
                key=lambda s: str(s.get("SnapshotCreateTime", "")),
                reverse=True,
            )
            return snaps[0]["DBSnapshotIdentifier"] if snaps else ""
        except Exception:
            return ""

    snapshot_id = await _run(_get_snapshot)

    def _modify():
        rds = get_boto3_client(creds, "rds")
        rds.modify_db_instance(
            DBInstanceIdentifier=db_id,
            EngineVersion=target_version,
            AllowMajorVersionUpgrade=True,
            ApplyImmediately=apply_immediately,
        )

    await _run(_modify)
    logger.info("rds_postgres_upgrade: upgrade to %s initiated for %s", target_version, db_id)

    deadline = time.time() + _POLL_TIMEOUT
    final_version = current_version
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            rds = get_boto3_client(creds, "rds")
            i = rds.describe_db_instances(DBInstanceIdentifier=db_id)["DBInstances"][0]
            return i["DBInstanceStatus"], i["EngineVersion"]

        status, ver = await _run(_poll)
        logger.info("rds_postgres_upgrade: polling status=%s version=%s", status, ver)
        if status == "available" and ver == target_version:
            final_version = ver
            break
    else:
        raise TimeoutError(
            f"RDS instance {db_id} did not reach {target_version} within {_POLL_TIMEOUT}s"
        )

    return {
        "status": "upgraded",
        "db_instance_identifier": db_id,
        "previous_version": current_version,
        "current_version": final_version,
        "snapshot_id": snapshot_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_id = execution_result.get("snapshot_id", "unknown")
    db_id = execution_result.get(
        "db_instance_identifier",
        parameters.get("db_instance_identifier", "unknown"),
    )
    prev = execution_result.get("previous_version", "unknown")
    return {
        "rolled_back": False,
        "reason": (
            f"RDS does not support engine version downgrades. "
            f"To restore {db_id} to {prev}, restore from automated snapshot: {snapshot_id}"
        ),
        "snapshot_id": snapshot_id,
    }
