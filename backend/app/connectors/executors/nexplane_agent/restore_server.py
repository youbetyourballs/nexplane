# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""restore_server executor — thin dispatcher to restore strategy modules."""
import logging

logger = logging.getLogger(__name__)

# Backward-compat re-exports (consumed by existing code that imported from here)
from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client  # noqa: F401
from app.connectors.executors.nexplane_agent.restore_strategies import IrreversibleOperationError  # noqa: F401


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.restore_strategies import get_strategy

    if not asset_ids:
        raise RuntimeError("restore_server: no asset_ids provided")

    target = parameters.get("target", {})
    if target.get("type") == "same":
        restore_strategy = "in_place"
    else:
        artifact_refs = parameters.get("artifact_refs", {})
        restore_strategy = (
            artifact_refs.get("restore_strategy")
            or parameters.get("restore_strategy")
            or "launch_ami"
        )

    strategy = get_strategy(restore_strategy)
    return await strategy.restore(parameters, asset_ids, connector)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.restore_strategies import get_strategy

    restore_strategy = (
        (execution_result.get("artifact_refs") or {}).get("restore_strategy")
        or parameters.get("restore_strategy")
        or ("in_place" if parameters.get("confirm_same_target") else "launch_ami")
    )
    strategy = get_strategy(restore_strategy)
    return await strategy.rollback(parameters, execution_result, connector)
