# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="defaults_delete",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # Deletion is not reversible without the original value — no-op rollback
    return {"rolled_back": False, "reason": "defaults_delete rollback requires the original value; use defaults_write to restore manually"}
