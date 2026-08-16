# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""OCI Cache (Redis) cluster version upgrade executor.

Upgrades an OCI Redis cluster to a new software version.
Rollback is PARTIAL — OCI Cache does not support version downgrades.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

_POLL_INTERVAL = 30
_POLL_TIMEOUT = 1800


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    cluster_id = parameters["cluster_id"]
    target_version = parameters["target_version"]

    def _get():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.redis.RedisClusterClient(config)
        return client.get_redis_cluster(cluster_id).data

    cluster = await _run(_get)
    current_version = cluster.software_version

    if str(current_version) == str(target_version):
        return {"status": "already_at_version", "cluster_id": cluster_id,
                "previous_version": current_version, "current_version": current_version}

    def _upgrade():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.redis.RedisClusterClient(config)
        details = oci.redis.models.UpdateRedisClusterDetails(software_version=target_version)
        client.update_redis_cluster(cluster_id, details)

    await _run(_upgrade)
    logger.info("oci_cache_upgrade: upgrade to %s initiated for %s", target_version, cluster_id)

    deadline = time.time() + _POLL_TIMEOUT
    final_version = current_version
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            import oci
            from app.connectors.executors.oci._client import get_oci_config
            config = get_oci_config(creds)
            client = oci.redis.RedisClusterClient(config)
            return client.get_redis_cluster(cluster_id).data

        polled = await _run(_poll)
        logger.info("oci_cache_upgrade: polling state=%s version=%s",
                    polled.lifecycle_state, polled.software_version)
        if polled.lifecycle_state == "ACTIVE" and str(polled.software_version) == str(target_version):
            final_version = polled.software_version
            break
    else:
        raise TimeoutError(f"OCI Redis cluster {cluster_id} did not reach {target_version} within {_POLL_TIMEOUT}s")

    return {
        "status": "upgraded",
        "cluster_id": cluster_id,
        "previous_version": current_version,
        "current_version": final_version,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    cluster_id = execution_result.get("cluster_id", parameters.get("cluster_id", "unknown"))
    prev = execution_result.get("previous_version", "unknown")
    return {
        "rolled_back": False,
        "reason": (
            f"OCI Redis cluster does not support version downgrades. "
            f"Cluster {cluster_id} cannot be reverted to version {prev}."
        ),
    }
