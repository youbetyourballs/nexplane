# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

# Default and max observation window for adaptive extension
_DEFAULT_WINDOW_SECONDS = 1200   # 20 minutes
_MAX_WINDOW_SECONDS = 7200       # 2 hours


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    observation_window = int(parameters.get("observation_window_seconds", _DEFAULT_WINDOW_SECONDS))
    observation_window = min(observation_window, _MAX_WINDOW_SECONDS)

    asset_id = asset_ids[0] if asset_ids else None

    # Agent handles adaptive window extension internally:
    # - confidence=both dependency with no traffic after window → extend by 600s (up to max)
    # - confidence=config_only dependency with no traffic → extend once by 1200s, mark low_confidence
    # - confidence=runtime_only → already observed, no extension needed
    result = await _dispatch.dispatch_agent_job(
        command="capture_behavioral_baseline",
        parameters={
            "asset_id": asset_id,
            "observation_window_seconds": observation_window,
            "max_window_seconds": _MAX_WINDOW_SECONDS,
        },
        asset_ids=list(asset_ids),
        timeout_seconds=_MAX_WINDOW_SECONDS + 300,
    )

    baseline = result.get("baseline", {})
    unverified = result.get("unverified_dependencies", [])

    return {
        "action": "capture_behavioral_baseline",
        "baseline": baseline,
        "observation_duration_seconds": baseline.get("observation_duration_seconds", observation_window),
        "unverified_dependencies": unverified,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture_behavioral_baseline is non-mutating"}
