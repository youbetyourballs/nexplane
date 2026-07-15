# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Executor: disable the reverse tunnel on an agent registration.

Platform-tier operation (execution_tier=1). Updates AgentRegistration
directly and stores previous state so rollback can re-enable.
"""
import uuid
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise RuntimeError("No asset_ids provided")

    asset_id = uuid.UUID(str(asset_ids[0]))

    # Support being called from enable_reverse_tunnel rollback with explicit
    # restore values, or as a first-class disable (restore to disabled).
    restore_enabled: bool = parameters.get("restore_enabled", False)
    restore_allowlist: list = parameters.get("restore_allowlist", [])

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

        # Capture previous state for rollback.
        previous_enabled = bool(reg.tunnel_enabled)
        previous_allowlist = list(reg.tunnel_allowlist or [])

        reg.tunnel_enabled = restore_enabled
        reg.tunnel_allowlist = list(restore_allowlist)
        await db.commit()
        await db.refresh(reg)

    disabled_at = datetime.now(timezone.utc).isoformat()
    return {
        "agent_id": str(reg.id),
        "enabled": restore_enabled,
        "disabled_at": disabled_at,
        "allowlist": list(restore_allowlist),
        "previous_state": {
            "enabled": previous_enabled,
            "allowlist": previous_allowlist,
        },
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore previous tunnel state by re-enabling with the prior config."""
    from app.connectors.executors.agent_tunnel import enable_reverse_tunnel

    asset_ids = execution_result.get("_asset_ids") or []
    previous = execution_result.get("previous_state", {})

    restore_params = {
        "allowlist": previous.get("allowlist", []),
        "allowed_connector_types": [],
    }
    # If the tunnel was previously disabled, just ensure it stays disabled —
    # re-running execute() with restore_enabled=False achieves that idempotently.
    if not previous.get("enabled", True):
        restore_params["restore_enabled"] = False
        restore_params["restore_allowlist"] = previous.get("allowlist", [])
        return await execute(restore_params, asset_ids, connector)

    return await enable_reverse_tunnel.execute(restore_params, asset_ids, connector)
