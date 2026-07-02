# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Reverse-tunnel relay endpoint (control-plane side).

Accepts an agent's outbound WebSocket and authenticates it using one of two
mechanisms (tried in order):

1. **Short-lived per-connection token** (preferred): the agent fetches a token
   from POST /agents/{agent_id}/tunnel-token immediately before dialling, then
   presents it as ``Authorization: Bearer <token>`` on the WS handshake.  The
   token is valid for 60 s and is single-use — the relay marks ``used_at`` on
   acceptance so a stolen token cannot be replayed.

2. **Long-lived org HMAC secret** (legacy / fallback): the same
   ``Authorization: Bearer <org-agent-secret>`` the agent uses for its
   register/poll calls.  Accepted to keep agents that have not yet upgraded
   working.  A deprecation warning is logged for every connection that falls
   through to this path so operators can track rollout progress.

In both cases the relay also verifies that the per-agent ``tunnel_enabled``
flag is set, that the agent belongs to the authenticated org, and that it loads
the allowlist from the database.  A valid credential alone is not sufficient.

Wire the route in with:

    from app.tunnel.relay import run_agent_tunnel
    @router.websocket("/tunnel")
    async def agent_tunnel(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
        await run_agent_tunnel(websocket, db)
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from starlette.websockets import WebSocket, WebSocketDisconnect

from app import config as app_config
from app.models.agent import AgentRegistration, TunnelConnectionToken
from app.models.org_settings import OrganizationSettings
from app.services.secrets_service import SecretsService

from .authorizer import parse_allowlist
from .manager import TunnelSession, TransportClosed, get_manager

log = logging.getLogger(__name__)


class WebSocketTransport:
    """Adapts a Starlette WebSocket to the tunnel Transport interface."""

    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def recv_bytes(self) -> bytes:
        try:
            return await self._ws.receive_bytes()
        except (WebSocketDisconnect, RuntimeError, KeyError) as exc:
            raise TransportClosed() from exc


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

async def _resolve_org_by_bearer(authorization: str, db) -> OrganizationSettings | None:
    """Resolve an org by its long-lived HMAC secret (legacy path)."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    secrets = SecretsService(app_config.settings.SECRET_KEY)
    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.agent_secret_encrypted.is_not(None)
        )
    )
    for org_settings in result.scalars().all():
        try:
            if secrets.decrypt(org_settings.agent_secret_encrypted) == token:
                return org_settings
        except Exception:
            continue
    return None


async def _consume_tunnel_token(
    raw_token: str,
    agent_id: str,
    db,
) -> AgentRegistration | None:
    """Validate and consume a short-lived tunnel connection token.

    Returns the AgentRegistration on success, None on any failure (not found,
    expired, already used, wrong agent).  Marks ``used_at`` atomically on
    acceptance so the token cannot be reused.
    """
    try:
        aid = uuid.UUID(str(agent_id))
    except (ValueError, TypeError):
        return None

    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(TunnelConnectionToken).where(
            TunnelConnectionToken.token_hash == token_hash
        )
    )
    record = result.scalar_one_or_none()

    if record is None:
        return None
    if record.used_at is not None:
        log.warning(
            "tunnel: rejected already-used connection token for agent %s (used_at=%s)",
            agent_id,
            record.used_at.isoformat(),
        )
        return None
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        log.warning(
            "tunnel: rejected expired connection token for agent %s (expired=%s)",
            agent_id,
            expires_at.isoformat(),
        )
        return None
    if record.agent_id != aid:
        log.warning(
            "tunnel: token agent_id mismatch: token=%s handshake=%s",
            record.agent_id,
            aid,
        )
        return None

    # Mark consumed before we proceed — single-use enforcement.
    record.used_at = now
    await db.commit()

    reg_result = await db.execute(
        select(AgentRegistration).where(AgentRegistration.id == aid)
    )
    return reg_result.scalar_one_or_none()


async def _load_agent(db, org_id, agent_id: str) -> AgentRegistration | None:
    try:
        aid = uuid.UUID(str(agent_id))
    except (ValueError, TypeError):
        return None
    result = await db.execute(
        select(AgentRegistration).where(
            AgentRegistration.id == aid,
            AgentRegistration.organization_id == org_id,
        )
    )
    return result.scalar_one_or_none()


# WebSocket close codes (application range).
_UNAUTHORIZED = 4401
_FORBIDDEN = 4403


async def run_agent_tunnel(websocket: WebSocket, db) -> None:
    await websocket.accept()

    authorization = websocket.headers.get("authorization", "")
    agent_id = websocket.query_params.get("agent_id", "")

    reg: AgentRegistration | None = None

    # --- Path 1: short-lived per-connection token ----------------------------
    # The agent presents a token it fetched from POST /agents/{id}/tunnel-token
    # seconds ago.  Token format: 64-char hex (sha256 stored; raw in header).
    # We detect this path by checking whether the bearer value is exactly
    # 64 hex chars (the output of secrets.token_hex(32)).  Org HMAC secrets
    # are never exactly this shape (they are Fernet-derived or arbitrary).
    raw_token = ""
    if authorization.startswith("Bearer "):
        candidate = authorization[7:].strip()
        if len(candidate) == 64 and all(c in "0123456789abcdefABCDEF" for c in candidate):
            raw_token = candidate

    if raw_token:
        reg = await _consume_tunnel_token(raw_token, agent_id, db)
        if reg is None:
            log.warning("tunnel: short-lived token auth failed for agent_id=%s", agent_id)
            await websocket.close(code=_UNAUTHORIZED)
            return
        if not reg.tunnel_enabled:
            await websocket.close(code=_FORBIDDEN)
            return
    else:
        # --- Path 2: long-lived org HMAC secret (legacy / fallback) ----------
        org = await _resolve_org_by_bearer(authorization, db)
        if org is None:
            await websocket.close(code=_UNAUTHORIZED)
            return
        reg = await _load_agent(db, org.organization_id, agent_id)
        if reg is None or not reg.tunnel_enabled:
            await websocket.close(code=_FORBIDDEN)
            return
        log.warning(
            "tunnel: agent %s connected using deprecated long-lived HMAC secret; "
            "upgrade agent to fetch short-lived token before dialling",
            agent_id,
        )

    allowlist = parse_allowlist(list(reg.tunnel_allowlist or []))
    session = TunnelSession(WebSocketTransport(websocket))
    session.start()
    manager = get_manager()
    manager.register(str(reg.id), session, allowlist)
    try:
        await session.wait()  # until the agent disconnects
    finally:
        manager.unregister(str(reg.id))
        await session.close()
        try:
            await websocket.close()
        except Exception:
            pass
