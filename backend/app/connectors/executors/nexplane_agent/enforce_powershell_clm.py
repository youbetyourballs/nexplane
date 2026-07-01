# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="enforce_powershell_clm",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=60,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="enforce_powershell_clm",
        parameters={"action": "restore", "snapshot_id": execution_result.get("snapshot_id", "")},
        asset_ids=asset_ids,
        timeout_seconds=60,
    )
