# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""RabbitMQ major version upgrade executor (3.x -> 4.x).

Inherits the preflight/snapshot/upgrade/verify scaffold from AppUpgradeExecutor.
Rollback restores from the local snapshot artifact recorded in execution_result.
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
    """Module-level shim so tests can patch rabbitmq_mod.dispatch_agent_job."""
    from app.connectors.executors.nexplane_agent.app_upgrade_base import dispatch_agent_job as _base
    return await _base(command=command, parameters=parameters, asset_ids=asset_ids, timeout_seconds=timeout_seconds)


class RabbitMQUpgradeExecutor(AppUpgradeExecutor):
    """Upgrade RabbitMQ from 3.x to 4.x with snapshot-based rollback."""

    async def preflight(self, asset_id: str, parameters: dict, connector) -> dict:
        p = parameters
        result = await dispatch_agent_job(
            command="app_preflight_rabbitmq",
            parameters={
                "rmq_host": p.get("rmq_host", "localhost"),
                "rmq_port": int(p.get("rmq_port", 15672)),
                "rmq_user": p.get("rmq_user", "guest"),
                "rmq_password": p.get("rmq_password", "guest"),
                "target_version": p.get("target_version", ""),
            },
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
        if not result.get("preflight_passed", False):
            raise PreflightBlocked(result.get("reason", "Preflight failed"))
        return result

    async def upgrade(self, asset_id: str, parameters: dict, connector) -> dict:
        p = parameters
        return await dispatch_agent_job(
            command="app_upgrade_rabbitmq",
            parameters={
                "rmq_host": p.get("rmq_host", "localhost"),
                "rmq_port": int(p.get("rmq_port", 15672)),
                "rmq_user": p.get("rmq_user", "guest"),
                "rmq_password": p.get("rmq_password", "guest"),
                "source_version": p.get("source_version", ""),
                "target_version": p.get("target_version", ""),
                "rmq_container": p.get("rmq_container", "rabbitmq"),
            },
            asset_ids=[asset_id],
            timeout_seconds=600,
        )

    async def verify(self, asset_id, p, connector, upgrade_result):
        upgraded_version = upgrade_result.get("upgraded_version", "")
        if "4." not in upgraded_version:
            raise RuntimeError("RabbitMQ upgrade did not reach 4.x")
        return {"verified": True, "upgraded_version": upgraded_version}

    async def _snapshot_local(self, asset_id, parameters, connector):
        p = parameters
        result = await dispatch_agent_job(
            command="app_snapshot_local_rabbitmq",
            parameters={
                "rmq_host": p.get("rmq_host", "localhost"),
                "rmq_port": int(p.get("rmq_port", 15672)),
                "rmq_user": p.get("rmq_user", "guest"),
                "rmq_password": p.get("rmq_password", "guest"),
                "asset_id": asset_id,
            },
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        return result

    async def rollback(self, asset_id: str, execution_result: dict, connector, parameters=None) -> dict:
        snapshot = execution_result.get("snapshot_result", {})
        strategy = snapshot.get("strategy")
        snapshot_path = snapshot.get("snapshot_path") or snapshot.get("local_path")

        if strategy == "skipped" or not snapshot_path:
            return {"status": "rollback_failed", "reason": "no snapshot available"}

        params = parameters or {}
        container_name = (
            execution_result.get("upgrade_result", {}).get("container_name")
            or params.get("rmq_container", "rabbitmq")
        )
        logger.info("Rolling back RabbitMQ via local snapshot %s on %s", snapshot_path, asset_id)
        result = await dispatch_agent_job(
            command="app_restore_local_rabbitmq",
            parameters={"snapshot_path": snapshot_path},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        return {
            "rolled_back": result.get("restored", False),
            "strategy": strategy,
            "agent_result": result,
            "warning": "container_swap_not_automated",
            "manual_steps": f"docker stop {container_name}-v4; docker start {container_name}",
        }


_executor = RabbitMQUpgradeExecutor()


async def execute(parameters, asset_ids, connector):
    return await _executor.execute(parameters, asset_ids, connector)


async def rollback(parameters, execution_result, connector):
    asset_ids = execution_result.get("asset_ids") or parameters.get("target_asset_ids") or []
    asset_id = str(asset_ids[0]) if asset_ids else ""
    return await _executor.rollback(asset_id, execution_result, connector, parameters=parameters)
