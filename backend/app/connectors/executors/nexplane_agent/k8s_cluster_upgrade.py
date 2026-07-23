# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kubernetes cluster upgrade executor.

Supports EKS (AWS managed), GKE (GCP managed), AKS (Azure managed),
and self-hosted clusters via kubeadm.

Control plane upgrade: ROLLBACK_CAPABILITY for that step is irreversible —
Kubernetes does not support control plane downgrade. Node pool upgrades
are fully reversible (restore previous node image via replacement).

Flow:
  preflight (version path +1 minor, removed API scan, resource headroom)
  → control plane upgrade (EKS/GKE/AKS API call or kubeadm)
  → node pool rolling upgrade (drain → upgrade → verify per node)
  → post-upgrade health check

Rollback: node pools can be rolled back (replace with previous image).
Control plane cannot be rolled back — this is surfaced in the intent summary.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"  # node pools: full; control plane: irreversible


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    target_version = parameters.get("target_version", "")  # e.g. "1.29"
    cluster_type = parameters.get("cluster_type", "auto")  # eks | gke | aks | kubeadm | auto
    node_pool_strategy = parameters.get("node_pool_strategy", "rolling")
    drain_timeout_seconds = int(parameters.get("drain_timeout_seconds", 300))
    max_unavailable = int(parameters.get("max_unavailable", 1))
    dry_run = bool(parameters.get("dry_run", False))

    if not target_version:
        raise ValueError("target_version required (e.g. '1.29')")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Step 1: Preflight — version path, removed APIs, resource headroom
    logger.info(f"K8s upgrade preflight for {asset_id}, target={target_version}")
    preflight = await dispatch_agent_job(
        command="preflight_k8s_upgrade",
        parameters={
            "target_version": target_version,
            "cluster_type": cluster_type,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    if preflight.get("status") == "blocked":
        return {
            "status": "blocked",
            "reason": preflight.get("reason", "Preflight checks failed"),
            "preflight": preflight,
        }

    # Detect actual cluster type from preflight if auto
    detected_cluster_type = preflight.get("cluster_type", cluster_type)
    current_version = preflight.get("current_version", "unknown")

    if dry_run:
        return {
            "status": "dry_run",
            "cluster_type": detected_cluster_type,
            "current_version": current_version,
            "target_version": target_version,
            "removed_api_violations": preflight.get("removed_api_violations", []),
            "node_pools": preflight.get("node_pools", []),
            "warnings": preflight.get("warnings", []),
        }

    # Step 2: Control plane upgrade — IRREVERSIBLE step
    logger.info(f"Upgrading K8s control plane on {asset_id} to {target_version}")
    cp_result = await dispatch_agent_job(
        command="upgrade_k8s_control_plane",
        parameters={
            "target_version": target_version,
            "cluster_type": detected_cluster_type,
        },
        asset_ids=[asset_id],
        timeout_seconds=1800,  # managed control plane upgrades can take 30 min
    )

    if not cp_result.get("success", True):
        return {
            "status": "failed",
            "phase": "control_plane",
            "error": cp_result.get("error", "Control plane upgrade failed"),
            "cp_result": cp_result,
            "note": "Control plane upgrade is irreversible — manual intervention required",
        }

    control_plane_upgraded_at = datetime.now(timezone.utc).isoformat()
    node_pools = preflight.get("node_pools", [])
    upgraded_pools = []

    # Step 3: Rolling node pool upgrade
    for pool in node_pools:
        pool_name = pool.get("name", "default")
        logger.info(f"Upgrading node pool {pool_name} on {asset_id}")
        try:
            pool_result = await dispatch_agent_job(
                command="upgrade_k8s_node_pool",
                parameters={
                    "target_version": target_version,
                    "cluster_type": detected_cluster_type,
                    "pool_name": pool_name,
                    "node_pool_strategy": node_pool_strategy,
                    "drain_timeout_seconds": drain_timeout_seconds,
                    "max_unavailable": max_unavailable,
                    "previous_image": pool.get("image_version", ""),
                },
                asset_ids=[asset_id],
                timeout_seconds=3600,
            )
            upgraded_pools.append({
                "pool_name": pool_name,
                "previous_image": pool.get("image_version"),
                "result": pool_result,
            })
        except Exception as exc:
            logger.error(f"Node pool {pool_name} upgrade failed: {exc}")
            upgraded_pools.append({
                "pool_name": pool_name,
                "previous_image": pool.get("image_version"),
                "error": str(exc),
                "status": "failed",
            })

    # Step 4: Post-upgrade health check
    try:
        health = await dispatch_agent_job(
            command="verify_k8s_cluster_health",
            parameters={"expected_version": target_version},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
    except Exception as exc:
        health = {"healthy": False, "error": str(exc)}

    return {
        "status": "completed",
        "cluster_type": detected_cluster_type,
        "previous_version": current_version,
        "new_version": target_version,
        "control_plane_upgraded_at": control_plane_upgraded_at,
        "upgraded_node_pools": upgraded_pools,
        "health_check": health,
        "rollback_note": "Control plane cannot be rolled back. Node pools can be restored to previous image.",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    upgraded_pools = execution_result.get("upgraded_node_pools", [])
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    cluster_type = execution_result.get("cluster_type", "auto")
    previous_version = execution_result.get("previous_version", "")

    if execution_result.get("status") == "failed" and execution_result.get("phase") == "control_plane":
        return {
            "rolled_back": False,
            "reason": "Control plane upgrade is irreversible — manual intervention required",
        }

    if not upgraded_pools:
        return {"rolled_back": False, "reason": "no_node_pools_to_rollback"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    rolled_back_pools = []
    # Restore node pools in reverse order (FILO)
    for pool_info in reversed(upgraded_pools):
        pool_name = pool_info.get("pool_name")
        previous_image = pool_info.get("previous_image")
        if not previous_image:
            rolled_back_pools.append({"pool_name": pool_name, "skipped": True, "reason": "no_previous_image"})
            continue
        try:
            result = await dispatch_agent_job(
                command="rollback_k8s_node_pool",
                parameters={
                    "cluster_type": cluster_type,
                    "pool_name": pool_name,
                    "target_image": previous_image,
                    "previous_version": previous_version,
                },
                asset_ids=[asset_id],
                timeout_seconds=3600,
            )
            rolled_back_pools.append({"pool_name": pool_name, "result": result})
        except Exception as exc:
            rolled_back_pools.append({"pool_name": pool_name, "error": str(exc)})

    return {
        "rolled_back": True,
        "note": "Node pools restored to previous image. Control plane remains at upgraded version.",
        "rolled_back_pools": rolled_back_pools,
    }
