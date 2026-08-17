# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if "action" not in parameters:
        parameters = {
            "action": "set_default_action",
            "default_action": {"inbound": "Block", "outbound": "Allow"},
        }
    result = await _dispatch.dispatch_agent_job(
        command="configure_windows_firewall",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=60,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    result["_action"] = parameters.get("action", "")
    result["_rule"] = parameters.get("rule")
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    action = execution_result.get("_action", "")
    if action == "add_rule":
        rule = execution_result.get("_rule") or {}
        rollback_params: dict = {"rollback": True, "action": "remove_rule", "rule": rule}
    else:
        rollback_params = {"rollback": True, "snapshot": execution_result.get("snapshot", "")}
    return await _dispatch.dispatch_agent_job(
        command="configure_windows_firewall",
        parameters=rollback_params,
        asset_ids=asset_ids,
        timeout_seconds=60,
    )
