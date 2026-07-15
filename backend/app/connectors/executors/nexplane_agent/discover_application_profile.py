# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    asset_id = parameters.get("asset_id") or (asset_ids[0] if asset_ids else None)

    result = await _dispatch.dispatch_agent_job(
        command="discover_application_profile",
        parameters={"asset_id": asset_id},
        asset_ids=list(asset_ids),
        timeout_seconds=180,
    )

    hostname = result.get("hostname", asset_id)
    profile = result.get("profile", {})

    return {
        "action": "discover_application_profile",
        "_auto_asset": {
            "name": f"Application Profile — {hostname}",
            "asset_type": "application_profile",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": profile,
            "tags": ["discovered", "migration"],
        },
        "profile": profile,
        "hostname": hostname,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover_application_profile is non-mutating"}
