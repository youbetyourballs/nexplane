# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Internal SOCKS5 proxy for routing HTTP connectors through the reverse tunnel.

Lazily starts one localhost SocksServer with an in-process random token
(independent of the operator-facing TUNNEL_SOCKS_ENABLED). HTTP connectors point
httpx at proxy_url(agent_id); the SOCKS username selects the agent, the password
is the internal token, and the agent resolves + dials the destination (remote
DNS).
"""
from __future__ import annotations

import asyncio
import secrets

from .manager import get_manager
from .socks import SocksServer


class ConnectorSocksProxy:
    def __init__(self):
        self._token = secrets.token_urlsafe(24)
        self._server: SocksServer | None = None
        self._addr: tuple[str, int] | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> tuple[str, int]:
        async with self._lock:
            if self._addr is None:
                self._server = SocksServer(get_manager(), host="127.0.0.1", port=0, auth_token=self._token)
                self._addr = await self._server.start()
            return self._addr

    async def proxy_url(self, agent_id: str) -> str:
        host, port = await self._ensure()
        return f"socks5://{agent_id}:{self._token}@{host}:{port}"

    async def stop(self) -> None:
        async with self._lock:
            if self._server is not None:
                await self._server.stop()
                self._server = None
                self._addr = None


_proxy: ConnectorSocksProxy | None = None


def get_connector_proxy() -> ConnectorSocksProxy:
    global _proxy
    if _proxy is None:
        _proxy = ConnectorSocksProxy()
    return _proxy
