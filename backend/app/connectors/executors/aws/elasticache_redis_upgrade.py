# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS ElastiCache Redis replication group version upgrade executor.

Triggers an engine version upgrade on an ElastiCache Redis replication group.
Rollback is PARTIAL — ElastiCache does not support engine downgrades.
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
_POLL_TIMEOUT = 1800


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    group_id = parameters["replication_group_id"]
    target_version = parameters["target_version"]
    creds = connector.credentials

    # Describe replication group to get member clusters
    def _get_group():
        ec = get_boto3_client(creds, "elasticache")
        resp = ec.describe_replication_groups(ReplicationGroupId=group_id)
        return resp["ReplicationGroups"][0]

    group = await _run(_get_group)
    member = group.get("MemberClusters", [None])[0]

    # Get current engine version from a member cluster
    def _get_cluster_version():
        if not member:
            return "unknown"
        ec = get_boto3_client(creds, "elasticache")
        resp = ec.describe_cache_clusters(CacheClusterId=member, ShowCacheNodeInfo=False)
        return resp["CacheClusters"][0]["EngineVersion"]

    current_version = await _run(_get_cluster_version)

    def _version_match(reported: str, target: str) -> bool:
        return reported == target or reported.startswith(target + ".")

    if _version_match(current_version, target_version):
        return {
            "status": "already_at_version",
            "replication_group_id": group_id,
            "previous_version": current_version,
            "current_version": current_version,
        }

    # Find the latest automated snapshot for rollback reference
    def _get_snapshot():
        try:
            ec = get_boto3_client(creds, "elasticache")
            resp = ec.describe_snapshots(
                ReplicationGroupId=group_id,
                SnapshotSource="automated",
                MaxRecords=1,
            )
            snaps = sorted(
                resp.get("Snapshots", []),
                key=lambda s: s.get("NodeSnapshots", [{}])[0].get("SnapshotCreateTime", ""),
                reverse=True,
            )
            return snaps[0]["SnapshotName"] if snaps else ""
        except Exception:
            return ""

    snapshot_id = await _run(_get_snapshot)

    def _modify():
        ec = get_boto3_client(creds, "elasticache")
        ec.modify_replication_group(
            ReplicationGroupId=group_id,
            EngineVersion=target_version,
            ApplyImmediately=True,
        )

    await _run(_modify)
    logger.info(
        "elasticache_redis_upgrade: upgrade to %s initiated for %s",
        target_version,
        group_id,
    )

    # Poll until the cluster reports the target version and is available
    deadline = time.time() + _POLL_TIMEOUT
    final_version = current_version
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            ec = get_boto3_client(creds, "elasticache")
            grp = ec.describe_replication_groups(ReplicationGroupId=group_id)[
                "ReplicationGroups"
            ][0]
            m = grp.get("MemberClusters", [None])[0]
            if not m:
                return grp.get("Status", ""), current_version
            cl = ec.describe_cache_clusters(CacheClusterId=m)["CacheClusters"][0]
            return cl["CacheClusterStatus"], cl["EngineVersion"]

        status, ver = await _run(_poll)
        logger.info(
            "elasticache_redis_upgrade: polling status=%s version=%s", status, ver
        )
        if status == "available" and _version_match(ver, target_version):
            final_version = ver
            break
    else:
        raise TimeoutError(
            f"ElastiCache {group_id} did not reach {target_version} within {_POLL_TIMEOUT}s"
        )

    return {
        "status": "upgraded",
        "replication_group_id": group_id,
        "previous_version": current_version,
        "current_version": final_version,
        "snapshot_id": snapshot_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_id = execution_result.get("snapshot_id", "unknown")
    group_id = execution_result.get(
        "replication_group_id",
        parameters.get("replication_group_id", "unknown"),
    )
    prev = execution_result.get("previous_version", "unknown")
    return {
        "rolled_back": False,
        "reason": (
            f"ElastiCache does not support engine version downgrades. "
            f"To restore {group_id} to {prev}, restore from automated snapshot: {snapshot_id}"
        ),
        "snapshot_id": snapshot_id,
    }
