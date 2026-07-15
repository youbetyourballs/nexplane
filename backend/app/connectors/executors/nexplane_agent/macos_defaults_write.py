# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="defaults_write",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    # Use the agent's built-in RollbackDefaultsWrite handler via rollback=True in parameters.
    # The agent merges previous_result into params; if previous_value is absent/null it deletes,
    # otherwise it restores the previous value.
    rollback_params = dict(parameters)
    rollback_params["rollback"] = True
    rollback_params["previous_result"] = execution_result
    return await _dispatch.dispatch_agent_job(
        command="defaults_write",
        parameters=rollback_params,
        asset_ids=asset_ids,
        timeout_seconds=30,
    )
