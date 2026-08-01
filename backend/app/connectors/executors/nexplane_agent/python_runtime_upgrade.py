# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Python runtime upgrade executor (e.g. 3.8→3.11→3.12).

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
    """Module-level shim so tests can patch python_runtime_mod.dispatch_agent_job."""
    from app.connectors.executors.nexplane_agent.app_upgrade_base import dispatch_agent_job as _base
    return await _base(command=command, parameters=parameters, asset_ids=asset_ids, timeout_seconds=timeout_seconds)


class PythonRuntimeUpgradeExecutor(AppUpgradeExecutor):
    """Upgrade Python runtime in a Docker container with snapshot-based rollback."""

    async def preflight(self, asset_id: str, parameters: dict, connector) -> dict:
        p = parameters
        result = await dispatch_agent_job(
            command="app_preflight_python",
            parameters={
                "python_container": p.get("python_container", "python-app"),
                "target_version": p.get("target_version", ""),
                "requirements_file": p.get("requirements_file", "requirements.txt"),
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
            command="app_upgrade_python",
            parameters={
                "python_container": p.get("python_container", "python-app"),
                "target_version": p.get("target_version", ""),
                "python_image": p.get("python_image", ""),
                "app_dir": p.get("app_dir", "/app"),
                "requirements_file": p.get("requirements_file", "requirements.txt"),
                "start_cmd": p.get("start_cmd", ""),
            },
            asset_ids=[asset_id],
            timeout_seconds=600,
        )

    async def verify(self, asset_id, p, connector, upgrade_result):
        upgraded_version = upgrade_result.get("upgraded_version", "")
        if not upgraded_version:
            raise RuntimeError("Python runtime upgrade did not return an upgraded_version")
        return {"verified": True, "upgraded_version": upgraded_version}

    async def _snapshot_local(self, asset_id, parameters, connector):
        p = parameters
        result = await dispatch_agent_job(
            command="app_snapshot_local_python",
            parameters={
                "python_container": p.get("python_container", "python-app"),
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

        params = parameters or execution_result.get("parameters", {})
        logger.info("Rolling back Python runtime via local snapshot %s on %s", snapshot_path, asset_id)
        result = await dispatch_agent_job(
            command="app_restore_local_python",
            parameters={
                "python_container": params.get("python_container", "python-app"),
                "target_version": params.get("target_version", ""),
                "snapshot_path": snapshot_path,
            },
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        return {"rolled_back": result.get("restored", False), "strategy": strategy, "agent_result": result}


_executor = PythonRuntimeUpgradeExecutor()


async def execute(parameters, asset_ids, connector):
    return await _executor.execute(parameters, asset_ids, connector)


async def rollback(parameters, execution_result, connector):
    asset_ids = execution_result.get("asset_ids") or parameters.get("target_asset_ids") or []
    asset_id = str(asset_ids[0]) if asset_ids else ""
    return await _executor.rollback(asset_id, execution_result, connector, parameters=parameters)
