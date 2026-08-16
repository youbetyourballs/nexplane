# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Azure AKS cluster upgrade executor.

Upgrades the AKS managed control plane to a new Kubernetes version.
AKS only supports +1 minor version per upgrade (e.g. 1.27 → 1.28).

Control plane upgrade is IRREVERSIBLE. Rollback returns partial status
with guidance for manual intervention.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

_POLL_INTERVAL = 30
_POLL_TIMEOUT = 1800  # 30 minutes


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _get_aks_client(creds: dict):
    from azure.identity import ClientSecretCredential
    from azure.mgmt.containerservice import ContainerServiceClient
    credential = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    )
    return ContainerServiceClient(credential, creds["subscription_id"])


def _parse_minor(version: str) -> int:
    parts = version.split(".")
    return int(parts[1]) if len(parts) >= 2 else 0


async def _wait_for_upgrade(creds: dict, resource_group: str, cluster_name: str, target_version: str) -> str:
    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            client = _get_aks_client(creds)
            return client.managed_clusters.get(resource_group, cluster_name)

        cluster = await _run(_poll)
        logger.info(
            "aks_cluster_upgrade: polling state=%s version=%s",
            cluster.provisioning_state,
            cluster.kubernetes_version,
        )
        if cluster.provisioning_state == "Succeeded" and cluster.kubernetes_version == target_version:
            return cluster.kubernetes_version
        if cluster.provisioning_state == "Failed":
            raise RuntimeError(f"AKS cluster upgrade failed (provisioning_state=Failed)")
    raise TimeoutError(
        f"AKS cluster {cluster_name} did not reach {target_version} within {_POLL_TIMEOUT}s"
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    resource_group = parameters["resource_group"]
    cluster_name = parameters["cluster_name"]
    target_version = parameters["target_version"]

    def _get():
        client = _get_aks_client(creds)
        return client.managed_clusters.get(resource_group, cluster_name)

    cluster = await _run(_get)
    current_version = cluster.kubernetes_version

    if current_version == target_version:
        logger.info("aks_cluster_upgrade: cluster %s already at %s", cluster_name, target_version)
        return {
            "status": "already_at_version",
            "cluster_name": cluster_name,
            "previous_version": current_version,
            "current_version": current_version,
        }

    # Validate +1 minor version constraint
    cur_minor = _parse_minor(current_version)
    tgt_minor = _parse_minor(target_version)
    if tgt_minor - cur_minor != 1:
        raise ValueError(
            f"AKS only supports upgrading one minor version at a time. "
            f"Current: {current_version}, Target: {target_version} "
            f"(minor delta: {tgt_minor - cur_minor})"
        )

    def _upgrade():
        from azure.mgmt.containerservice.models import ManagedCluster
        client = _get_aks_client(creds)
        existing = client.managed_clusters.get(resource_group, cluster_name)
        existing.kubernetes_version = target_version
        poller = client.managed_clusters.begin_create_or_update(
            resource_group, cluster_name, existing
        )
        poller.wait(timeout=60)  # Wait for operation to be accepted, not for completion

    await _run(_upgrade)
    logger.info("aks_cluster_upgrade: upgrade to %s initiated for %s", target_version, cluster_name)

    final_version = await _wait_for_upgrade(creds, resource_group, cluster_name, target_version)

    return {
        "status": "upgraded",
        "cluster_name": cluster_name,
        "resource_group": resource_group,
        "previous_version": current_version,
        "current_version": final_version,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    previous_version = execution_result.get("previous_version", "unknown")
    cluster_name = execution_result.get("cluster_name", parameters.get("cluster_name", "unknown"))
    return {
        "rolled_back": False,
        "reason": (
            f"AKS control plane upgrade is irreversible. Cluster {cluster_name} remains at "
            f"{execution_result.get('current_version', 'upgraded version')}. "
            f"Previous version was {previous_version}. Manual intervention required to restore."
        ),
    }
