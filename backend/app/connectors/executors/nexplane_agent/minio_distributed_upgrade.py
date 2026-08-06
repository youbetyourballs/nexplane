# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
MinIO distributed upgrade executor.
Uses mc admin update for coordinated rolling upgrade across all nodes.
MinIO handles its own binary replacement and restart coordination.
Flow: preflight → config export → mc admin update → poll until all nodes on target → verify.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _fn
    return await _fn(command=command, parameters=parameters, asset_ids=asset_ids, timeout_seconds=timeout_seconds)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    target_version = parameters.get("target_version")
    nodes = parameters.get("nodes", [])
    access_key = parameters.get("access_key")
    secret_key = parameters.get("secret_key")
    dry_run = bool(parameters.get("dry_run", False))

    if not target_version:
        raise ValueError("target_version required (MinIO release tag, e.g. RELEASE.2024-01-01T00-00-00Z)")
    if not nodes:
        raise ValueError("nodes required — list of {host, api_port, console_port}")

    minio_params = {
        "nodes": nodes,
        "access_key": access_key,
        "secret_key": secret_key,
        "target_version": target_version,
    }

    # Phase 1: Preflight
    preflight = await dispatch_agent_job(
        command="preflight_minio_upgrade",
        parameters=minio_params,
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "preflight_blocked":
        return preflight

    source_version = preflight.get("current_version", "unknown")

    if dry_run:
        return {"status": "dry_run", "source_version": source_version, "target_version": target_version, "preflight": preflight}

    # Phase 2: Config export (backup)
    config_export = await dispatch_agent_job(
        command="minio_config_export",
        parameters=minio_params,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    # Phase 3: mc admin update (MinIO handles rolling upgrade internally)
    logger.info(f"Triggering MinIO distributed upgrade to {target_version}")
    await dispatch_agent_job(
        command="minio_update",
        parameters={**minio_params, "source_version": source_version},
        asset_ids=[asset_id],
        timeout_seconds=600,
    )

    # Phase 4: Poll until all nodes report target version (up to 10 min)
    poll_result = await dispatch_agent_job(
        command="minio_poll_version",
        parameters={**minio_params, "expected_version": target_version, "poll_timeout_seconds": 600},
        asset_ids=[asset_id],
        timeout_seconds=660,
    )

    # Phase 5: Heal if needed
    heal_triggered = poll_result.get("heal_needed", False)
    if heal_triggered:
        logger.info("MinIO erasure heal triggered post-upgrade")
        await dispatch_agent_job(
            command="minio_heal",
            parameters=minio_params,
            asset_ids=[asset_id],
            timeout_seconds=600,
        )

    # Phase 6: Verify
    verify = await dispatch_agent_job(
        command="minio_verify_cluster",
        parameters=minio_params,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    return {
        "status": "completed" if verify.get("all_nodes_online") else "verify_failed",
        "source_version": source_version,
        "target_version": target_version,
        "nodes_upgraded": [n.get("host") for n in nodes],
        "heal_triggered": heal_triggered,
        "verify_result": verify,
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id")
    source_version = execution_result.get("source_version")

    if not source_version or source_version == "unknown":
        return {"rolled_back": False, "reason": "source_version unknown — cannot pin rollback version"}

    p = parameters.get("desired_outcome") or parameters
    minio_params = {
        "nodes": p.get("nodes", []),
        "access_key": p.get("access_key"),
        "secret_key": p.get("secret_key"),
        "target_version": source_version,
    }

    logger.info(f"Rolling back MinIO to {source_version}")
    result = await dispatch_agent_job(
        command="minio_update",
        parameters={**minio_params, "rollback": True},
        asset_ids=[asset_id],
        timeout_seconds=600,
    )

    poll_result = await dispatch_agent_job(
        command="minio_poll_version",
        parameters={**minio_params, "expected_version": source_version, "poll_timeout_seconds": 300},
        asset_ids=[asset_id],
        timeout_seconds=360,
    )

    return {
        "rolled_back": poll_result.get("all_nodes_on_version", False),
        "strategy": "minio_pinned_version_rollback",
        "rolled_back_to": source_version,
        "agent_result": result,
    }
