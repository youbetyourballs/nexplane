# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Cassandra rolling upgrade executor.
Upgrades each node one at a time: drain → stop → upgrade → start → verify UN.
Flow: preflight → snapshot (nodetool snapshot) → rolling per-node upgrade → upgradesstables → verify.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job as _fn
    return await _fn(
        command=command,
        parameters=parameters,
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version")
    target_version = parameters.get("target_version")
    nodes = parameters.get("nodes", [])
    upgrade_delay_seconds = int(parameters.get("upgrade_delay_seconds", 30))
    dry_run = bool(parameters.get("dry_run", False))

    if not target_version:
        raise ValueError("target_version required")
    if not nodes:
        raise ValueError("nodes required — list of {host, port, dc}")

    # Phase 1: Preflight
    logger.info(f"Cassandra upgrade preflight for asset {asset_id}, {source_version}→{target_version}")
    preflight = await dispatch_agent_job(
        command="preflight_cassandra_upgrade",
        parameters={"source_version": source_version, "target_version": target_version, "nodes": nodes},
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "preflight_blocked":
        return preflight

    if dry_run:
        return {"status": "dry_run", "target_version": target_version, "preflight": preflight}

    # Phase 2: Snapshot (nodetool snapshot on each node)
    logger.info(f"Taking Cassandra snapshots on all {len(nodes)} nodes")
    snapshot = await dispatch_agent_job(
        command="cassandra_snapshot",
        parameters={"nodes": nodes, "target_version": target_version},
        asset_ids=[asset_id],
        timeout_seconds=600,
    )
    snapshot_tag = snapshot.get("snapshot_tag")

    # Phase 3: Rolling per-node upgrade
    upgraded_nodes = []
    failed_node = None

    for node in nodes:
        node_host = node.get("host")
        logger.info(f"Upgrading Cassandra node {node_host}")
        try:
            await dispatch_agent_job(
                command="cassandra_drain_node",
                parameters={"node": node},
                asset_ids=[asset_id],
                timeout_seconds=300,
            )
            await dispatch_agent_job(
                command="cassandra_upgrade_node",
                parameters={"node": node, "target_version": target_version},
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
            upgraded_nodes.append(node_host)
            logger.info(f"Node {node_host} upgraded successfully")
        except Exception as exc:
            failed_node = node_host
            logger.error(f"Node {node_host} upgrade failed: {exc}")
            break

        if upgrade_delay_seconds > 0 and node != nodes[-1]:
            await asyncio.sleep(upgrade_delay_seconds)

    # Phase 4: Post-upgrade sstables (only on upgraded nodes)
    if upgraded_nodes:
        await dispatch_agent_job(
            command="cassandra_upgradesstables",
            parameters={"nodes": [n for n in nodes if n.get("host") in upgraded_nodes]},
            asset_ids=[asset_id],
            timeout_seconds=1800,
        )

    # Phase 5: Verify
    verify = await dispatch_agent_job(
        command="verify_cassandra_cluster",
        parameters={"nodes": nodes, "target_version": target_version},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    status = "completed" if not failed_node and verify.get("all_nodes_ok") else (
        "partial_upgrade" if upgraded_nodes else "upgrade_failed"
    )

    return {
        "status": status,
        "source_version": source_version,
        "target_version": target_version,
        "upgraded_nodes": upgraded_nodes,
        "failed_node": failed_node,
        "snapshot_tag": snapshot_tag,
        "verify_result": verify,
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id")
    upgraded_nodes = execution_result.get("upgraded_nodes", [])
    snapshot_tag = execution_result.get("snapshot_tag")
    source_version = execution_result.get("source_version")

    if not snapshot_tag:
        return {
            "rolled_back": False,
            "reason": "no snapshot_tag in execution_result — manual restoration required",
            "upgraded_nodes": upgraded_nodes,
        }

    if not upgraded_nodes:
        return {"rolled_back": True, "reason": "no nodes were upgraded — nothing to roll back"}

    logger.info(f"Rolling back Cassandra upgrade for {len(upgraded_nodes)} upgraded nodes using snapshot {snapshot_tag}")

    p = parameters.get("desired_outcome") or parameters
    nodes = p.get("nodes", [])

    # Only attempt rollback on nodes that were actually upgraded
    nodes_to_rollback = [n for n in nodes if n.get("host") in upgraded_nodes]

    result = await dispatch_agent_job(
        command="cassandra_rollback_nodes",
        parameters={
            "nodes_to_rollback": nodes_to_rollback,
            "snapshot_tag": snapshot_tag,
            "source_version": source_version,
        },
        asset_ids=[asset_id],
        timeout_seconds=1800,
    )

    return {
        "rolled_back": result.get("success", False),
        "strategy": "snapshot_restore",
        "snapshot_tag": snapshot_tag,
        "nodes_rolled_back": nodes_to_rollback,
        "warning": "Cassandra SSTable format may not be fully reversible on upgraded nodes. Verify data integrity after rollback.",
        "agent_result": result,
    }
