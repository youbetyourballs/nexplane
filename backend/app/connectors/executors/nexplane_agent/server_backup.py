# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""server_backup executor — thin dispatcher to capture strategy modules."""
import logging
from app.connectors.executors.nexplane_agent.backup_strategies import (
    get_strategy,
    _load_backup_target,
    _load_storage_config,
)

logger = logging.getLogger(__name__)

# Re-export for modules that still import these from server_backup (e.g. restore_server)
from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client  # noqa: F401


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise RuntimeError("server_backup: no asset_ids provided")

    capture_strategy = parameters.get("capture_strategy")
    if not capture_strategy:
        target = await _load_backup_target(str(asset_ids[0]))
        capture_strategy = (
            getattr(target, "capture_strategy", None) if target else None
        ) or "ebs_snapshot"

    strategy = get_strategy(capture_strategy)
    return await strategy.backup(parameters, asset_ids, connector)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    capture_strategy = (
        execution_result.get("artifact_refs", {}).get("capture_strategy")
        or parameters.get("capture_strategy")
        or "ebs_snapshot"
    )
    strategy = get_strategy(capture_strategy)
    return await strategy.rollback(parameters, execution_result, connector)
