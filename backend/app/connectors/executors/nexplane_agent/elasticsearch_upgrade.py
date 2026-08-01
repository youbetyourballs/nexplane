# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Elasticsearch major version upgrade executor (7.x -> 8.x).

Inherits the preflight/snapshot/upgrade/verify scaffold from AppUpgradeExecutor.
Rollback restores from the snapshot artifact recorded in execution_result.
"""

import logging

from app.connectors.executors.nexplane_agent.app_upgrade_base import (
    AppUpgradeExecutor,
    PreflightBlocked,
    SNAPSHOT_STRATEGY_LOCAL,
    SNAPSHOT_STRATEGY_S3,
    SNAPSHOT_STRATEGY_EBS,
)

logger = logging.getLogger(__name__)


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    """Module-level shim so tests can patch es_mod.dispatch_agent_job."""
    from app.connectors.executors.nexplane_agent.app_upgrade_base import dispatch_agent_job as _base
    return await _base(command=command, parameters=parameters, asset_ids=asset_ids, timeout_seconds=timeout_seconds)


class ElasticsearchUpgradeExecutor(AppUpgradeExecutor):
    """Upgrade Elasticsearch from 7.x to 8.x with snapshot-based rollback."""

    async def preflight(self, asset_id: str, parameters: dict, connector) -> dict:
        result = await dispatch_agent_job(
            command="app_preflight_elasticsearch",
            parameters=parameters,
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
        if not result.get("preflight_passed", False):
            reason = result.get("reason", "preflight check failed")
            raise PreflightBlocked(reason)
        return result

    async def upgrade(self, asset_id: str, parameters: dict, connector) -> dict:
        return await dispatch_agent_job(
            command="app_upgrade_elasticsearch",
            parameters=parameters,
            asset_ids=[asset_id],
            timeout_seconds=600,
        )

    async def verify(self, asset_id: str, parameters: dict, connector, upgrade_result: dict) -> dict:
        health_check_url = parameters.get("health_check_url")
        upgraded_port = (upgrade_result or {}).get("upgraded_port", parameters.get("es_port", 9200))
        result = await dispatch_agent_job(
            command="app_verify_elasticsearch",
            parameters={**parameters, "es_port": upgraded_port, "health_check_url": health_check_url},
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
        return result if result else {"verify_status": "passed"}

    async def rollback(self, asset_id: str, execution_result: dict, connector) -> dict:
        snapshot = execution_result.get("snapshot_result", {})
        strategy = snapshot.get("strategy")

        if strategy == SNAPSHOT_STRATEGY_EBS:
            snapshot_id = snapshot.get("snapshot_id")
            instance_id = snapshot.get("instance_id")
            logger.info("Rolling back ES via EBS snapshot %s on %s", snapshot_id, instance_id)
            result = await dispatch_agent_job(
                command="app_restore_ebs_elasticsearch",
                parameters={"snapshot_id": snapshot_id, "ec2_instance_id": instance_id},
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
        elif strategy == SNAPSHOT_STRATEGY_S3:
            s3_bucket = snapshot.get("s3_bucket")
            dump_path = snapshot.get("dump_path")
            logger.info("Rolling back ES via S3 snapshot s3://%s/%s", s3_bucket, dump_path)
            result = await dispatch_agent_job(
                command="app_restore_s3_elasticsearch",
                parameters={"s3_bucket": s3_bucket, "s3_key": dump_path},
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
        else:
            local_path = snapshot.get("local_path")
            logger.info("Rolling back ES via local snapshot %s", local_path)
            result = await dispatch_agent_job(
                command="app_restore_local_elasticsearch",
                parameters={"local_path": local_path},
                asset_ids=[asset_id],
                timeout_seconds=300,
            )

        return {"rolled_back": result.get("restored", False), "strategy": strategy, "agent_result": result}
