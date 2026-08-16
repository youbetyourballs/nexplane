# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS DocumentDB cluster version upgrade executor.

Upgrades a DocumentDB cluster to a new engine version.
Rollback is PARTIAL — DocumentDB does not support engine downgrades.
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
    cluster_id = parameters["db_cluster_identifier"]
    target_version = parameters["target_version"]
    apply_immediately = parameters.get("apply_immediately", True)
    creds = connector.credentials

    def _describe():
        docdb = get_boto3_client(creds, "docdb")
        return docdb.describe_db_clusters(DBClusterIdentifier=cluster_id)

    resp = await _run(_describe)
    cluster = resp["DBClusters"][0]
    current_version = cluster["EngineVersion"]

    if current_version == target_version:
        return {
            "status": "already_at_version",
            "db_cluster_identifier": cluster_id,
            "previous_version": current_version,
            "current_version": current_version,
        }

    def _get_snapshot():
        docdb = get_boto3_client(creds, "docdb")
        return docdb.describe_db_cluster_snapshots(
            DBClusterIdentifier=cluster_id,
            SnapshotType="automated",
            MaxRecords=1,
        )

    try:
        snap_resp = await _run(_get_snapshot)
        snaps = sorted(
            snap_resp.get("DBClusterSnapshots", []),
            key=lambda s: str(s.get("SnapshotCreateTime", "")),
            reverse=True,
        )
        snapshot_id = snaps[0]["DBClusterSnapshotIdentifier"] if snaps else ""
    except Exception:
        snapshot_id = ""

    def _modify():
        docdb = get_boto3_client(creds, "docdb")
        docdb.modify_db_cluster(
            DBClusterIdentifier=cluster_id,
            EngineVersion=target_version,
            ApplyImmediately=apply_immediately,
        )

    await _run(_modify)
    logger.info(
        "documentdb_upgrade: upgrade to %s initiated for %s",
        target_version,
        cluster_id,
    )

    deadline = time.time() + _POLL_TIMEOUT
    final_version = current_version
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            docdb = get_boto3_client(creds, "docdb")
            return docdb.describe_db_clusters(DBClusterIdentifier=cluster_id)

        poll_resp = await _run(_poll)
        c = poll_resp["DBClusters"][0]
        status, ver = c["Status"], c["EngineVersion"]
        logger.info(
            "documentdb_upgrade: polling status=%s version=%s", status, ver
        )
        if status == "available" and ver == target_version:
            final_version = ver
            break
    else:
        raise TimeoutError(
            f"DocumentDB {cluster_id} did not reach {target_version} within {_POLL_TIMEOUT}s"
        )

    return {
        "status": "upgraded",
        "db_cluster_identifier": cluster_id,
        "previous_version": current_version,
        "current_version": final_version,
        "snapshot_id": snapshot_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_id = execution_result.get("snapshot_id", "unknown")
    cluster_id = execution_result.get(
        "db_cluster_identifier",
        parameters.get("db_cluster_identifier", "unknown"),
    )
    prev = execution_result.get("previous_version", "unknown")
    return {
        "rolled_back": False,
        "reason": (
            f"DocumentDB does not support engine version downgrades. "
            f"To restore {cluster_id} to {prev}, restore from cluster snapshot: {snapshot_id}"
        ),
        "snapshot_id": snapshot_id,
    }
