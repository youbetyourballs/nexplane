# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kafka ZooKeeper to KRaft bridge executor (Phase 1).

Starts KRaft controllers alongside ZooKeeper in dual-write mode.
Fully reversible — rollback stops KRaft controllers and returns to ZK-only mode.
"""

import logging

from app.connectors.executors.nexplane_agent.app_upgrade_base import (
    AppUpgradeExecutor,
    PreflightBlocked,
    SNAPSHOT_STRATEGY_LOCAL,
)

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    """Module-level shim so tests can patch kafka_bridge_mod.dispatch_agent_job."""
    from app.connectors.executors.nexplane_agent.app_upgrade_base import dispatch_agent_job as _base
    return await _base(command=command, parameters=parameters, asset_ids=asset_ids, timeout_seconds=timeout_seconds)


class KafkaBridgeExecutor(AppUpgradeExecutor):
    """Phase 1: Add KRaft controllers in dual-write mode alongside ZooKeeper."""

    async def preflight(self, asset_id: str, parameters: dict, connector) -> dict:
        p = parameters
        result = await dispatch_agent_job(
            command="app_preflight_kafka_bridge",
            parameters={
                "kafka_container": p.get("kafka_container", "kafka"),
                "kafka_zk_host": p.get("kafka_zk_host", "localhost"),
                "kafka_zk_port": int(p.get("kafka_zk_port", 2181)),
                "kafka_port": int(p.get("kafka_port", 9092)),
                "source_version": p.get("source_version", ""),
                "target_version": p.get("target_version", ""),
            },
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
        if not result.get("preflight_passed", False):
            raise PreflightBlocked(result.get("reason", "Kafka bridge preflight failed"))
        return result

    async def upgrade(self, asset_id: str, parameters: dict, connector) -> dict:
        p = parameters
        return await dispatch_agent_job(
            command="app_upgrade_kafka_bridge",
            parameters={
                "kafka_container": p.get("kafka_container", "kafka"),
                "kafka_zk_host": p.get("kafka_zk_host", "localhost"),
                "kafka_zk_port": int(p.get("kafka_zk_port", 2181)),
                "kafka_port": int(p.get("kafka_port", 9092)),
                "source_version": p.get("source_version", ""),
                "target_version": p.get("target_version", ""),
                "kafka_zk_container": p.get("kafka_zk_container", "zookeeper"),
            },
            asset_ids=[asset_id],
            timeout_seconds=600,
        )

    async def verify(self, asset_id, p, connector, upgrade_result):
        if upgrade_result.get("bridge_mode") != "dual_write":
            raise RuntimeError("Kafka bridge did not reach dual_write mode")
        return {"verified": True, "bridge_mode": "dual_write"}

    async def _snapshot_local(self, asset_id: str, parameters: dict, connector) -> dict:
        p = parameters
        result = await dispatch_agent_job(
            command="app_snapshot_local_kafka_bridge",
            parameters={
                "kafka_container": p.get("kafka_container", "kafka"),
                "kafka_zk_host": p.get("kafka_zk_host", "localhost"),
                "kafka_zk_port": int(p.get("kafka_zk_port", 2181)),
                "asset_id": asset_id,
            },
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        return result

    async def rollback(self, asset_id: str, execution_result: dict, connector) -> dict:
        snapshot = execution_result.get("snapshot_result", {})
        strategy = snapshot.get("strategy")
        snapshot_path = snapshot.get("snapshot_path") or snapshot.get("local_path")

        if strategy == "skipped" or not snapshot_path:
            return {"status": "rollback_failed", "reason": "no snapshot available"}

        logger.info("Rolling back Kafka bridge via local snapshot %s on %s", snapshot_path, asset_id)
        result = await dispatch_agent_job(
            command="app_restore_local_kafka_bridge",
            parameters={
                "kafka_container": execution_result.get("parameters", {}).get("kafka_container", "kafka"),
                "snapshot_path": snapshot_path,
            },
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        return {"rolled_back": result.get("restored", False), "strategy": strategy, "agent_result": result}


_executor = KafkaBridgeExecutor()


async def execute(parameters, asset_ids, connector):
    return await _executor.execute(parameters, asset_ids, connector)


async def rollback(parameters, asset_ids, connector, execution_result):
    asset_id = str(asset_ids[0]) if asset_ids else ""
    return await _executor.rollback(asset_id, execution_result, connector)
