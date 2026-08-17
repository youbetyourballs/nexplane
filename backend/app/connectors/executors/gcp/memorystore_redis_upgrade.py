# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""GCP Memorystore Redis instance version upgrade executor.

Upgrades a Memorystore Redis instance to a new Redis version.
Rollback is PARTIAL — Memorystore does not support engine downgrades.
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
    instance_short = parameters["instance_name"]
    location = parameters["location"]
    target_version = parameters["target_version"]

    def _get_project():
        from app.connectors.executors.gcp._client import get_project_id
        return get_project_id(creds)

    project_id = await _run(_get_project)
    full_name = f"projects/{project_id}/locations/{location}/instances/{instance_short}"

    def _get():
        from google.cloud import redis_v1
        from app.connectors.executors.gcp._client import get_credentials
        credentials = get_credentials(creds)
        client = redis_v1.CloudRedisClient(credentials=credentials)
        return client.get_instance(name=full_name)

    instance = await _run(_get)
    current_version = instance.redis_version

    if current_version == target_version:
        return {"status": "already_at_version", "instance_name": full_name,
                "previous_version": current_version, "current_version": current_version}

    def _upgrade():
        from google.cloud import redis_v1
        from app.connectors.executors.gcp._client import get_credentials
        credentials = get_credentials(creds)
        client = redis_v1.CloudRedisClient(credentials=credentials)
        op = client.upgrade_instance(name=full_name, redis_version=target_version)
        op.result(timeout=60)

    await _run(_upgrade)
    logger.info("memorystore_redis_upgrade: upgrade to %s initiated for %s", target_version, full_name)

    deadline = time.time() + _POLL_TIMEOUT
    final_version = current_version
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            from google.cloud import redis_v1
            from app.connectors.executors.gcp._client import get_credentials
            credentials = get_credentials(creds)
            client = redis_v1.CloudRedisClient(credentials=credentials)
            return client.get_instance(name=full_name)

        inst = await _run(_poll)
        logger.info("memorystore_redis_upgrade: polling state=%s version=%s",
                    inst.state.name if inst.state else "?", inst.redis_version)
        if inst.state and inst.state.name == "READY" and inst.redis_version == target_version:
            final_version = inst.redis_version
            break
    else:
        raise TimeoutError(f"Memorystore {full_name} did not reach {target_version} within {_POLL_TIMEOUT}s")

    return {
        "status": "upgraded",
        "instance_name": full_name,
        "previous_version": current_version,
        "current_version": final_version,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    instance_name = execution_result.get("instance_name", parameters.get("instance_name", "unknown"))
    prev = execution_result.get("previous_version", "unknown")
    return {
        "rolled_back": False,
        "reason": (
            f"Memorystore Redis does not support engine version downgrades. "
            f"Instance {instance_name} cannot be reverted from upgraded version to {prev} via API."
        ),
    }
