# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
HashiCorp Vault cluster upgrade executor.
Flow: preflight -> raft snapshot -> upgrade standbys -> step down active -> upgrade old-active -> verify.
Topology rule: standbys upgraded before active. Active stepped down after standbys are upgraded.
Rollback: raft snapshot restore on active node, rolling restart with old binary.
Data between snapshot and upgrade is lost — surfaced in rollback result.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "source_version":     p.get("source_version"),
        "target_version":     p["target_version"],
        "nodes":              p["nodes"],
        "vault_token":        p.get("vault_token"),
        "snapshot_s3_bucket": p.get("snapshot_s3_bucket"),
        "dry_run":            bool(p.get("dry_run", False)),
    }


def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    fn = _get_dispatch()
    return await fn(
        command=command,
        parameters=parameters,
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    if not p.get("nodes"):
        raise ValueError("nodes list is required")

    # --- Phase 1: Preflight — identify active vs standby nodes ---
    preflight_result = await dispatch_agent_job(
        command="vault_cluster_preflight",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    if preflight_result.get("status") == "preflight_blocked":
        return preflight_result

    active_node   = preflight_result.get("active_node")
    standby_nodes = preflight_result.get("standby_nodes", [])
    logger.info(f"Vault active: {active_node}, standbys: {standby_nodes}")

    if p["dry_run"]:
        return {
            **preflight_result,
            "dry_run":      True,
            "active_node":  active_node,
            "standby_nodes": standby_nodes,
        }

    # --- Phase 2: Raft snapshot ---
    timestamp       = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path   = f"/tmp/nexplane-vault-snapshot-{timestamp}.snap"
    snapshot_result = await dispatch_agent_job(
        command="vault_raft_snapshot",
        parameters={
            **p,
            "snapshot_path": snapshot_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    actual_snapshot_path = snapshot_result.get("snapshot_path", snapshot_path)
    logger.info(f"Vault Raft snapshot at: {actual_snapshot_path}")

    # --- Phase 3: Upgrade standby nodes ---
    nodes_upgraded = []
    for node in standby_nodes:
        logger.info(f"Upgrading standby node: {node}")
        await dispatch_agent_job(
            command="vault_upgrade_node",
            parameters={**p, "target_node": node},
            asset_ids=[asset_id],
            timeout_seconds=600,
        )
        nodes_upgraded.append(node)
        logger.info(f"Standby {node} upgraded and rejoined")

    # --- Phase 4: Step down active node ---
    if active_node:
        logger.info(f"Stepping down active node: {active_node}")
        await dispatch_agent_job(
            command="vault_stepdown",
            parameters={**p, "target_node": active_node},
            asset_ids=[asset_id],
            timeout_seconds=60,
        )
        logger.info("Active stepped down — new leader elected from upgraded standbys")

    # --- Phase 5: Upgrade old active (now standby) ---
    if active_node:
        logger.info(f"Upgrading old active (now standby): {active_node}")
        await dispatch_agent_job(
            command="vault_upgrade_node",
            parameters={**p, "target_node": active_node},
            asset_ids=[asset_id],
            timeout_seconds=600,
        )
        nodes_upgraded.append(active_node)
        logger.info(f"Old active {active_node} upgraded")

    # --- Phase 6: Verify all nodes ---
    verify_result = await dispatch_agent_job(
        command="vault_cluster_verify",
        parameters={**p, "expected_version": p["target_version"]},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    all_healthy = verify_result.get("all_healthy", False)
    version_ok  = verify_result.get("version_ok", False)

    return {
        "status":          "completed" if (all_healthy and version_ok) else "verify_failed",
        "source_version":  p["source_version"],
        "target_version":  p["target_version"],
        "nodes_upgraded":  nodes_upgraded,
        "active_node":     active_node,
        "snapshot_path":   actual_snapshot_path,
        "verify_result":   verify_result,
        "asset_id":        asset_id,
        "upgraded_at":     datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """
    Restore Raft snapshot on active node, then rolling restart of all nodes with old binary.
    Data between snapshot and upgrade is lost — surfaced in result.
    """
    snapshot_path = execution_result.get("snapshot_path")
    if not snapshot_path:
        return {"rolled_back": False, "reason": "no snapshot_path in execution_result"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )
    p = _resolve_params(parameters)

    try:
        result = await dispatch_agent_job(
            command="vault_snapshot_restore_and_restart",
            parameters={
                **p,
                "snapshot_path": snapshot_path,
                "rollback_nodes": execution_result.get("nodes_upgraded", []),
            },
            asset_ids=[asset_id],
            timeout_seconds=900,
        )
        return {
            "rolled_back":       True,
            "strategy":          "raft_snapshot_restore",
            "snapshot_path":     snapshot_path,
            "data_loss_warning": (
                "All Vault data written between snapshot time and upgrade is lost. "
                "Verify application state after rollback."
            ),
            "agent_result":      result,
        }
    except Exception as exc:
        logger.error(f"Vault rollback failed: {exc}")
        return {"rolled_back": False, "reason": str(exc)}
