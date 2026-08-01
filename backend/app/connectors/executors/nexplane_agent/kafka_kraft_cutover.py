# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kafka KRaft cutover executor (Phase 2).

Permanently removes ZooKeeper dependency. IRREVERSIBLE — once committed,
rollback is not possible. Requires Phase 1 (kafka_zk_to_kraft_bridge) first.

Does NOT inherit from AppUpgradeExecutor — the base class snapshot logic is
incompatible with an irreversible operation.
"""

import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Kafka ZK→KRaft cutover permanently removes ZooKeeper metadata. Restore from a full system backup if needed."


class IrreversibleOperationError(Exception):
    """Raised when rollback is attempted on an irreversible operation."""


async def execute(parameters, asset_ids, connector):
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    asset_id = str(asset_ids[0]) if asset_ids else ""
    p = parameters or {}

    # 1. Preflight
    preflight = await dispatch_agent_job(
        command="app_preflight_kafka_cutover",
        parameters={
            "kafka_container": p.get("kafka_container", "kafka"),
            "kafka_port": int(p.get("kafka_port", 9092)),
        },
        asset_ids=asset_ids,
    )
    if not preflight.get("preflight_passed"):
        return {"status": "preflight_blocked", "reason": preflight.get("reason", "Cutover preflight failed")}

    # 2. Execute cutover
    result = await dispatch_agent_job(
        command="app_upgrade_kafka_cutover",
        parameters={
            "kafka_container": p.get("kafka_container", "kafka"),
            "kafka_port": int(p.get("kafka_port", 9092)),
            "kafka_zk_container": p.get("kafka_zk_container", "zookeeper"),
        },
        asset_ids=asset_ids,
    )
    return {"status": "completed", "cutover_result": result, "rollback_possible": False}


async def rollback(parameters, execution_result, connector):
    raise IrreversibleOperationError(
        "Kafka ZK→KRaft cutover is irreversible. Once committed, ZooKeeper metadata cannot be recovered from KRaft state. "
        "Restore from a full system backup if needed."
    )
