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

Also exposes POST /agents/{agent_id}/tunnel-token for the agent to exchange its
long-lived HMAC secret for a short-lived, single-use WS connection token.
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.agent import AgentRegistration, TunnelConnectionToken
from app.models.org_settings import OrganizationSettings
from app.models.user import User, UserRole
from app.routers import require_roles
from app.schemas.agent import AgentTunnelStatus, TunnelConfigUpdate, TunnelTokenResponse
from app.services.audit_service import record_event
from app.services.secrets_service import SecretsService
from app.tunnel.authorizer import parse_allowlist
from app.tunnel.manager import get_manager
from app import config as app_config

_TOKEN_TTL_SECONDS = 60

router = APIRouter(prefix="/agents", tags=["Agent Tunnel"])


def _to_status(reg: AgentRegistration) -> AgentTunnelStatus:
    return AgentTunnelStatus(
        agent_id=reg.id,
        asset_id=getattr(reg, "asset_id", None),
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
    reg.tunnel_max_concurrent = max(1, body.max_concurrent)
    await record_event(
        db,
        user.organization_id,
        "agent.tunnel_config_updated",
        {
            "agent_id": str(agent_id),
            "enabled": body.enabled,
            "allowlist": list(body.allowlist),
            "max_concurrent": reg.tunnel_max_concurrent,
        },
        actor_id=user.id,
    )
    await db.commit()
    await db.refresh(reg)
    return _to_status(reg)


async def _resolve_agent_by_bearer(
    authorization: str,
    agent_id: uuid.UUID,
    db: AsyncSession,
) -> AgentRegistration:
    """Validate the org HMAC secret and return the specific agent registration.

    Raises HTTPException 401/403/404 on failure so callers can just await and
    use the result directly.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    raw_token = authorization[7:].strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="Empty token")

    secrets_svc = SecretsService(app_config.settings.SECRET_KEY)
    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.agent_secret_encrypted.is_not(None)
        )
    )
    org_id = None
    for org_settings in result.scalars().all():
        try:
            if secrets_svc.decrypt(org_settings.agent_secret_encrypted) == raw_token:
                org_id = org_settings.organization_id
                break
        except Exception:
            continue
    if org_id is None:
        raise HTTPException(status_code=401, detail="Invalid agent secret")

    reg_result = await db.execute(
        select(AgentRegistration).where(
            and_(
                AgentRegistration.id == agent_id,
                AgentRegistration.organization_id == org_id,
            )
        )
    )
    reg = reg_result.scalar_one_or_none()
    if reg is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not reg.tunnel_enabled:
        raise HTTPException(status_code=403, detail="Tunnel not enabled for this agent")
    return reg


@router.post("/{agent_id}/tunnel-token", response_model=TunnelTokenResponse)
async def issue_tunnel_token(
    agent_id: uuid.UUID,
    authorization: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> TunnelTokenResponse:
    """Issue a short-lived, single-use tunnel connection token.

    Called by the agent immediately before opening the tunnel WebSocket. The
    agent authenticates with its long-lived org HMAC secret and receives back a
    32-byte (hex-encoded) random token valid for _TOKEN_TTL_SECONDS seconds.
    That token is used *once* for the WS handshake; the relay marks it consumed
    on acceptance and rejects reuse or expired tokens.

    The Authorization header carries the same ``Bearer <org-agent-secret>`` the
    agent uses for all other API calls — this endpoint is NOT admin-only because
    the agent process itself (not a human operator) calls it.
    """
    reg = await _resolve_agent_by_bearer(authorization, agent_id, db)

    raw_token = secrets.token_hex(32)  # 64 hex chars = 32 bytes of entropy
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=_TOKEN_TTL_SECONDS)

    record = TunnelConnectionToken(
        agent_id=reg.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(record)
    await db.commit()

    return TunnelTokenResponse(
        token=raw_token,
        expires_at=expires_at,
    )
