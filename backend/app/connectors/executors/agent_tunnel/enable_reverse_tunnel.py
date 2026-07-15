# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Executor: enable the reverse tunnel on an agent registration.

This is a platform-tier operation (execution_tier=1) — no agent job is
dispatched. We update the AgentRegistration row directly, exactly as the
admin API does, and store enough previous state for rollback.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


# Default allowlist used when the operator does not supply one.
_RFC1918_ALLOWLIST = ["10.0.0.0/8:*", "172.16.0.0/12:*", "192.168.0.0/16:*"]


async def _find_registration(asset_id: uuid.UUID):
    """Return (registration, org_id) for the most-recently-seen agent on the asset."""
    from sqlalchemy import select, and_
    from app.database import AsyncSessionLocal
    from app.models.agent import AgentRegistration
    from app.models.asset import Asset

    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, asset_id)
        if not asset:
            raise RuntimeError(f"Asset {asset_id} not found")

        org_id = asset.organization_id
        result = await db.execute(
            select(AgentRegistration)
            .where(
                and_(
                    AgentRegistration.asset_id == asset_id,
                    AgentRegistration.organization_id == org_id,
                )
            )
            .order_by(AgentRegistration.last_seen.desc())
            .limit(1)
        )
        reg = result.scalar_one_or_none()
        if reg is None:
            raise RuntimeError(
                f"No agent registered for asset {asset_id}. Deploy the Nexplane agent first."
            )
        return reg, org_id


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise RuntimeError("No asset_ids provided")

    asset_id = uuid.UUID(str(asset_ids[0]))
    allowed_connector_types = parameters.get("allowed_connector_types") or []
    allowlist = parameters.get("allowlist") or _RFC1918_ALLOWLIST

    from sqlalchemy import select, and_
    from app.database import AsyncSessionLocal
    from app.models.agent import AgentRegistration
    from app.models.asset import Asset

    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, asset_id)
        if not asset:
            raise RuntimeError(f"Asset {asset_id} not found")
        org_id = asset.organization_id

        from sqlalchemy import and_
        result = await db.execute(
            select(AgentRegistration)
            .where(
                and_(
                    AgentRegistration.asset_id == asset_id,
                    AgentRegistration.organization_id == org_id,
                )
            )
            .order_by(AgentRegistration.last_seen.desc())
            .limit(1)
        )
        reg = result.scalar_one_or_none()
        if reg is None:
            raise RuntimeError(
                f"No agent registered for asset {asset_id}. Deploy the Nexplane agent first."
            )

        # Capture previous state for rollback.
        previous_enabled = bool(reg.tunnel_enabled)
        previous_allowlist = list(reg.tunnel_allowlist or [])

        reg.tunnel_enabled = True
        reg.tunnel_allowlist = list(allowlist)
        await db.commit()
        await db.refresh(reg)

    enabled_at = datetime.now(timezone.utc).isoformat()
    return {
        "agent_id": str(reg.id),
        "enabled": True,
        "enabled_at": enabled_at,
        "allowlist": list(allowlist),
        "allowed_connector_types": allowed_connector_types,
        "previous_state": {
            "enabled": previous_enabled,
            "allowlist": previous_allowlist,
        },
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore previous tunnel state by delegating to disable_reverse_tunnel."""
    from app.connectors.executors.agent_tunnel import disable_reverse_tunnel

    asset_ids = execution_result.get("_asset_ids") or []
    previous = execution_result.get("previous_state", {})

    # Use the previous allowlist so rollback restores the exact prior config.
    rollback_params = {
        "restore_allowlist": previous.get("allowlist", []),
        "restore_enabled": previous.get("enabled", False),
    }
    return await disable_reverse_tunnel.execute(rollback_params, asset_ids, connector)
