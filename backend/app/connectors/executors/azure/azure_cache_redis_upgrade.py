# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Azure Cache for Redis version upgrade executor.

Upgrades an Azure Cache for Redis instance to a new Redis version.
Rollback is PARTIAL — Azure Cache for Redis does not support downgrades.
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


def _get_client(creds: dict):
    from azure.identity import ClientSecretCredential
    from azure.mgmt.redis import RedisManagementClient
    credential = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    )
    return RedisManagementClient(credential, creds["subscription_id"])


async def _wait_ready(creds: dict, resource_group: str, cache_name: str, target_version: str) -> str:
    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            client = _get_client(creds)
            return client.redis.get(resource_group, cache_name)

        cache = await _run(_poll)
        logger.info("azure_cache_redis_upgrade: polling state=%s version=%s",
                    cache.provisioning_state, cache.redis_version)
        if cache.provisioning_state == "Succeeded" and str(cache.redis_version) == str(target_version):
            return str(cache.redis_version)
        if cache.provisioning_state == "Failed":
            raise RuntimeError(f"Azure Cache {cache_name} upgrade failed")
    raise TimeoutError(f"Azure Cache {cache_name} did not reach version {target_version} within {_POLL_TIMEOUT}s")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    resource_group = parameters["resource_group"]
    cache_name = parameters["cache_name"]
    target_version = str(parameters["target_version"])

    def _get():
        client = _get_client(creds)
        return client.redis.get(resource_group, cache_name)

    cache = await _run(_get)
    current_version = str(cache.redis_version)

    if current_version == target_version:
        return {"status": "already_at_version", "cache_name": cache_name,
                "previous_version": current_version, "current_version": current_version}

    def _upgrade():
        from azure.mgmt.redis.models import RedisUpdateParameters
        client = _get_client(creds)
        poller = client.redis.begin_update(
            resource_group, cache_name,
            RedisUpdateParameters(redis_version=target_version),
        )
        poller.wait(timeout=60)

    await _run(_upgrade)
    logger.info("azure_cache_redis_upgrade: upgrade to %s initiated for %s", target_version, cache_name)

    final_version = await _wait_ready(creds, resource_group, cache_name, target_version)

    return {
        "status": "upgraded",
        "cache_name": cache_name,
        "resource_group": resource_group,
        "previous_version": current_version,
        "current_version": final_version,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    cache_name = execution_result.get("cache_name", parameters.get("cache_name", "unknown"))
    prev = execution_result.get("previous_version", "unknown")
    return {
        "rolled_back": False,
        "reason": (
            f"Azure Cache for Redis does not support version downgrades. "
            f"Cache {cache_name} cannot be reverted to version {prev} via API."
        ),
    }
