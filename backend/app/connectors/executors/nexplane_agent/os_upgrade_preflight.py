# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Lightweight preflight executor for OS major version upgrades.
Dispatches the preflight_os_upgrade agent command and returns the result.
"""
from __future__ import annotations

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    if not asset_ids:
        raise ValueError("asset_ids required")
    return await dispatch_agent_job(
        command="preflight_os_upgrade",
        parameters=parameters,
        asset_ids=[str(asset_ids[0])],
        timeout_seconds=120,
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "preflight is read-only"}
