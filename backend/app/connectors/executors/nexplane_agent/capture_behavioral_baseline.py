# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch

# Default and max observation window for adaptive extension
_DEFAULT_WINDOW_SECONDS = 1200   # 20 minutes
_MAX_WINDOW_SECONDS = 7200       # 2 hours


async def _resolve_agent_asset_id(profile_asset_id: str, org_id) -> str:
    """
    Resolve the server asset ID that has an agent registered,
    given a profile asset ID. Falls back to finding any agent in the org.
    """
    try:
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models.agent import AgentRegistration
        from app.models.asset import Asset

        async with AsyncSessionLocal() as db:
            # First try: is there an agent registered directly for this asset?
            reg = await db.execute(
                select(AgentRegistration).where(
                    AgentRegistration.asset_id == profile_asset_id,
                    AgentRegistration.organization_id == org_id,
                ).limit(1)
            )
            if reg.scalar_one_or_none():
                return profile_asset_id

            # Fallback: find any server asset with a registered agent in this org
            reg2 = await db.execute(
                select(AgentRegistration).where(
                    AgentRegistration.organization_id == org_id,
                ).order_by(AgentRegistration.last_seen.desc()).limit(1)
            )
            r = reg2.scalar_one_or_none()
            if r:
                return str(r.asset_id)
    except Exception:
        pass
    return profile_asset_id


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    import uuid
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset

    observation_window = int(parameters.get("observation_window_seconds", _DEFAULT_WINDOW_SECONDS))
    observation_window = min(observation_window, _MAX_WINDOW_SECONDS)

    profile_asset_id = asset_ids[0] if asset_ids else None

    # Resolve the org_id from the profile asset so we can find the agent
    org_id = None
    try:
        async with AsyncSessionLocal() as db:
            asset = await db.get(Asset, uuid.UUID(str(profile_asset_id)))
            if asset:
                org_id = asset.organization_id
    except Exception:
        pass

    # Find the server asset that has a registered agent
    agent_asset_id = await _resolve_agent_asset_id(profile_asset_id, org_id) if org_id else profile_asset_id

    result = await _dispatch.dispatch_agent_job(
        command="capture_behavioral_baseline",
        parameters={
            "asset_id": agent_asset_id,
            "observation_window_seconds": observation_window,
            "max_window_seconds": _MAX_WINDOW_SECONDS,
        },
        asset_ids=[agent_asset_id],
        timeout_seconds=_MAX_WINDOW_SECONDS + 300,
    )

    baseline = result.get("baseline", {})
    unverified = result.get("unverified_dependencies", [])
    obs_duration = result.get("observation_duration_seconds",
                              baseline.get("observation_duration_seconds", observation_window))

    return {
        "action": "capture_behavioral_baseline",
        "baseline": baseline,
        "observation_duration_seconds": obs_duration,
        "unverified_dependencies": unverified,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture_behavioral_baseline is non-mutating"}
