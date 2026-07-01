# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="santa_rule_remove",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    rollback_params = {
        "identifier_type": execution_result.get("identifier_type"),
        "identifier": execution_result.get("identifier"),
        "previous_state": execution_result.get("previous_state"),
        "_rollback": True,
    }
    return await _dispatch.dispatch_agent_job(
        command="santa_rule_remove",
        parameters=rollback_params,
        asset_ids=asset_ids,
        timeout_seconds=30,
    )
