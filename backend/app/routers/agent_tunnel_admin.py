# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Admin API for managing agents' reverse-tunnel config.

Operators enable/disable an agent's outbound tunnel and set its deny-by-default
allowlist here (instead of editing the database directly). Allowlist entries are
validated on write with the same parser the relay + agent enforce, so a bad rule
is rejected up front rather than silently denying everything at dial time.

Admin-only, org-scoped. Online status reflects whether the agent is currently
connected to *this* control-plane process's relay.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import AgentRegistration
from app.models.user import User, UserRole
from app.routers import require_roles
from app.schemas.agent import AgentTunnelStatus, TunnelConfigUpdate
from app.services.audit_service import record_event
from app.tunnel.authorizer import parse_allowlist
from app.tunnel.manager import get_manager

router = APIRouter(prefix="/agents", tags=["Agent Tunnel"])


def _to_status(reg: AgentRegistration) -> AgentTunnelStatus:
    return AgentTunnelStatus(
        agent_id=reg.id,
        hostname=reg.hostname,
        tunnel_enabled=bool(reg.tunnel_enabled),
        tunnel_allowlist=list(reg.tunnel_allowlist or []),
        online=get_manager().is_online(str(reg.id)),
    )


@router.get("/tunnel", response_model=list[AgentTunnelStatus])
async def list_agent_tunnels(
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentRegistration)
        .where(AgentRegistration.organization_id == user.organization_id)
        .order_by(AgentRegistration.hostname.asc())
    )
    return [_to_status(reg) for reg in result.scalars().all()]


@router.put("/{agent_id}/tunnel", response_model=AgentTunnelStatus)
async def set_agent_tunnel(
    agent_id: uuid.UUID,
    body: TunnelConfigUpdate,
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    # Validate allowlist entries up front (rejects malformed rules).
    try:
        parse_allowlist(body.allowlist)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid allowlist entry: {exc}")

    # An enabled tunnel with an empty allowlist bridges nothing (deny-by-default)
    # — almost always an operator mistake, so reject it explicitly.
    if body.enabled and not body.allowlist:
        raise HTTPException(
            status_code=400,
            detail="Cannot enable the tunnel with an empty allowlist (it would deny every destination).",
        )

    result = await db.execute(
        select(AgentRegistration).where(
            AgentRegistration.id == agent_id,
            AgentRegistration.organization_id == user.organization_id,
        )
    )
    reg = result.scalar_one_or_none()
    if reg is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    reg.tunnel_enabled = body.enabled
    reg.tunnel_allowlist = list(body.allowlist)
    await record_event(
        db,
        user.organization_id,
        "agent.tunnel_config_updated",
        {
            "agent_id": str(agent_id),
            "enabled": body.enabled,
            "allowlist": list(body.allowlist),
        },
        actor_id=user.id,
    )
    await db.commit()
    await db.refresh(reg)
    return _to_status(reg)
