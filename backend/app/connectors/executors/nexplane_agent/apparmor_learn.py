# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# backend/app/connectors/executors/nexplane_agent/apparmor_learn.py
from app.connectors.executors.nexplane_agent import _dispatch

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="apparmor_learn",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=int(parameters.get("duration_seconds", 60)) + 60,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "apparmor_learn creates no persistent state"}
