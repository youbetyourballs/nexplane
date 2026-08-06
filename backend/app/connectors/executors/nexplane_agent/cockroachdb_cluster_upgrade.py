# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
CockroachDB cluster upgrade executor.
Rolling binary upgrade: drain → replace binary → restart per node.
Version finalization is a separate explicit step (or via auto_finalize flag).
Flow: preflight → backup → rolling node upgrade → (optional) finalize version → verify.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _fn
    return await _fn(command=command, parameters=parameters, asset_ids=asset_ids, timeout_seconds=timeout_seconds)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version")
    target_version = parameters.get("target_version")
    nodes = parameters.get("nodes", [])
    sql_user = parameters.get("sql_user", "root")
    sql_password = parameters.get("sql_password")
    auto_finalize = bool(parameters.get("auto_finalize", False))
    dry_run = bool(parameters.get("dry_run", False))

    if not target_version:
        raise ValueError("target_version required")
    if not nodes:
        raise ValueError("nodes required — list of {host, http_port, sql_port}")

    # Phase 1: Preflight
    preflight = await dispatch_agent_job(
        command="preflight_crdb_upgrade",
        parameters={"source_version": source_version, "target_version": target_version, "nodes": nodes,
                    "sql_user": sql_user, "sql_password": sql_password},
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "preflight_blocked":
        return preflight

    if dry_run:
        return {"status": "dry_run", "source_version": source_version, "target_version": target_version, "preflight": preflight}

    # Phase 2: Backup
    backup = await dispatch_agent_job(
        command="crdb_backup",
        parameters={"nodes": nodes, "sql_user": sql_user, "sql_password": sql_password},
        asset_ids=[asset_id],
        timeout_seconds=3600,
    )
    backup_location = backup.get("backup_location")

    # Phase 3: Rolling node upgrade
    nodes_upgraded = []
    for node in nodes:
        node_host = node.get("host")
        logger.info(f"Upgrading CockroachDB node {node_host}")

        await dispatch_agent_job(
            command="crdb_drain_node",
            parameters={"node": node, "sql_user": sql_user, "sql_password": sql_password},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        await dispatch_agent_job(
            command="crdb_replace_binary",
            parameters={"node": node, "target_version": target_version},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        await dispatch_agent_job(
            command="crdb_wait_node_rejoin",
            parameters={"node": node, "nodes": nodes},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        nodes_upgraded.append(node_host)
        logger.info(f"Node {node_host} upgraded to {target_version}")

    # Phase 4: Finalize (optional)
    version_finalized = False
    if auto_finalize:
        logger.warning(f"Finalizing CockroachDB cluster version to {target_version} — this is irreversible")
        await dispatch_agent_job(
            command="crdb_finalize_version",
            parameters={"nodes": nodes, "sql_user": sql_user, "sql_password": sql_password, "target_version": target_version},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        version_finalized = True

    # Phase 5: Verify
    verify = await dispatch_agent_job(
        command="crdb_verify_cluster",
        parameters={"nodes": nodes, "sql_user": sql_user, "sql_password": sql_password, "target_version": target_version},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    status = "completed" if verify.get("cluster_healthy") else "verify_failed"
    if not auto_finalize and verify.get("cluster_healthy"):
        status = "awaiting_finalize"

    return {
        "status": status,
        "source_version": source_version,
        "target_version": target_version,
        "nodes_upgraded": nodes_upgraded,
        "version_finalized": version_finalized,
        "backup_location": backup_location,
        "verify_result": verify,
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id")
    version_finalized = execution_result.get("version_finalized", False)
    backup_location = execution_result.get("backup_location")

    if version_finalized:
        return {
            "rolled_back": False,
            "reason": "CockroachDB cluster version already finalized — cannot downgrade",
            "manual_steps": [
                "1. Restore from backup",
                f"2. Backup location: {backup_location}",
                "3. Provision new cluster from backup at original version",
            ],
            "backup_location": backup_location,
        }

    p = parameters.get("desired_outcome") or parameters
    nodes = p.get("nodes", [])
    nodes_upgraded = execution_result.get("nodes_upgraded", [])
    nodes_to_rollback = [n for n in nodes if n.get("host") in nodes_upgraded]

    result = await dispatch_agent_job(
        command="crdb_rollback_nodes",
        parameters={
            "nodes": nodes_to_rollback,
            "source_version": execution_result.get("source_version"),
            "sql_user": p.get("sql_user", "root"),
            "sql_password": p.get("sql_password"),
        },
        asset_ids=[asset_id],
        timeout_seconds=1800,
    )

    return {
        "rolled_back": result.get("success", False),
        "strategy": "binary_rollback",
        "nodes_rolled_back": [n.get("host") for n in nodes_to_rollback],
        "agent_result": result,
    }
