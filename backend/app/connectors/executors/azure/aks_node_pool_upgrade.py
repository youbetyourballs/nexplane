# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Azure AKS node pool upgrade executor.

Upgrades an AKS node pool (agent pool) to a new Kubernetes version.
Node pool rollback IS supported — Azure allows downgrading a node pool
to the previous version while the control plane stays at target version.

ROLLBACK_CAPABILITY = "full"
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_POLL_INTERVAL = 30
_POLL_TIMEOUT = 1800


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _get_aks_client(creds: dict):
    from azure.identity import ClientSecretCredential
    from azure.mgmt.containerservice import ContainerServiceClient
    credential = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    )
    return ContainerServiceClient(credential, creds["subscription_id"])


async def _wait_for_pool_upgrade(creds: dict, resource_group: str, cluster_name: str, pool_name: str, target_version: str) -> str:
    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            client = _get_aks_client(creds)
            return client.agent_pools.get(resource_group, cluster_name, pool_name)

        pool = await _run(_poll)
        logger.info(
            "aks_node_pool_upgrade: polling pool=%s state=%s version=%s",
            pool_name, pool.provisioning_state, pool.orchestrator_version,
        )
        if pool.provisioning_state == "Succeeded" and pool.orchestrator_version == target_version:
            return pool.orchestrator_version
        if pool.provisioning_state == "Failed":
            raise RuntimeError(f"AKS node pool upgrade failed (provisioning_state=Failed)")
    raise TimeoutError(
        f"AKS node pool {pool_name} did not reach {target_version} within {_POLL_TIMEOUT}s"
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    resource_group = parameters["resource_group"]
    cluster_name = parameters["cluster_name"]
    pool_name = parameters["node_pool_name"]
    target_version = parameters["target_version"]

    def _get():
        client = _get_aks_client(creds)
        return client.agent_pools.get(resource_group, cluster_name, pool_name)

    pool = await _run(_get)
    previous_version = pool.orchestrator_version

    if previous_version == target_version:
        return {
            "status": "already_at_version",
            "node_pool_name": pool_name,
            "previous_version": previous_version,
            "current_version": previous_version,
        }

    def _upgrade():
        from azure.mgmt.containerservice.models import AgentPool
        client = _get_aks_client(creds)
        existing = client.agent_pools.get(resource_group, cluster_name, pool_name)
        existing.orchestrator_version = target_version
        poller = client.agent_pools.begin_create_or_update(
            resource_group, cluster_name, pool_name, existing
        )
        poller.wait(timeout=60)

    await _run(_upgrade)
    logger.info("aks_node_pool_upgrade: upgrade to %s initiated for pool %s", target_version, pool_name)

    final_version = await _wait_for_pool_upgrade(creds, resource_group, cluster_name, pool_name, target_version)

    return {
        "status": "upgraded",
        "cluster_name": cluster_name,
        "node_pool_name": pool_name,
        "resource_group": resource_group,
        "previous_version": previous_version,
        "current_version": final_version,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    if execution_result.get("status") == "already_at_version":
        return {"rolled_back": False, "reason": "Pool was already at target version — nothing to undo"}

    creds = connector.credentials
    resource_group = execution_result.get("resource_group", parameters["resource_group"])
    cluster_name = execution_result.get("cluster_name", parameters["cluster_name"])
    pool_name = execution_result.get("node_pool_name", parameters["node_pool_name"])
    previous_version = execution_result["previous_version"]

    def _downgrade():
        client = _get_aks_client(creds)
        existing = client.agent_pools.get(resource_group, cluster_name, pool_name)
        existing.orchestrator_version = previous_version
        poller = client.agent_pools.begin_create_or_update(
            resource_group, cluster_name, pool_name, existing
        )
        poller.wait(timeout=60)

    await _run(_downgrade)
    logger.info("aks_node_pool_upgrade rollback: reverting pool %s to %s", pool_name, previous_version)

    final_version = await _wait_for_pool_upgrade(creds, resource_group, cluster_name, pool_name, previous_version)

    return {
        "rolled_back": True,
        "node_pool_name": pool_name,
        "restored_version": final_version,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
