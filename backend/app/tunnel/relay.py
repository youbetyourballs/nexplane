# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Reverse-tunnel relay endpoint (control-plane side).

Accepts an agent's outbound WebSocket, authenticates it with the same
``Authorization: Bearer <org-agent-secret>`` scheme the rest of the agent API
uses, verifies the agent registration has ``tunnel_enabled``, loads its
allowlist, and hands the socket to a ``TunnelSession`` registered with the
process-wide ``TunnelManager``. Runs until the agent disconnects.

Wire the route in with (in app/routers/agent.py or main):

    from app.tunnel.relay import run_agent_tunnel
    @router.websocket("/tunnel")
    async def agent_tunnel(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
        await run_agent_tunnel(websocket, db)
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from starlette.websockets import WebSocket, WebSocketDisconnect

from app import config as app_config
from app.models.agent import AgentRegistration
from app.models.org_settings import OrganizationSettings
from app.services.secrets_service import SecretsService

from .authorizer import parse_allowlist
from .manager import TunnelSession, TransportClosed, get_manager


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


async def _resolve_org_by_bearer(authorization: str, db) -> OrganizationSettings | None:
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

    org = await _resolve_org_by_bearer(websocket.headers.get("authorization", ""), db)
    if org is None:
        await websocket.close(code=_UNAUTHORIZED)
        return

    agent_id = websocket.query_params.get("agent_id", "")
    reg = await _load_agent(db, org.organization_id, agent_id)
    if reg is None or not reg.tunnel_enabled:
        await websocket.close(code=_FORBIDDEN)
        return

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
