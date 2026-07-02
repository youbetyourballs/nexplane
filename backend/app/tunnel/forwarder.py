# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Local TCP forwarder over the reverse tunnel.

For a fixed destination (agent, host, port) it runs a localhost listener; each
accepted connection is piped through get_manager().dial(agent, host, port). This
lets raw-TCP connectors (databases, SSH, LDAP) reach an agent's network without
any SOCKS support in their driver — they simply connect to the returned
127.0.0.1:<port>. Listeners are cached per (agent, host, port) and persist for
the process.

Idle reaping: listeners that have had no accepted connections for more than
IDLE_TIMEOUT_SECONDS are automatically closed and removed. This prevents
unbounded port accumulation when connectors have one-shot network paths.
"""
from __future__ import annotations

import asyncio
import time
from typing import NamedTuple

from .manager import TunnelManager, get_manager

IDLE_TIMEOUT_SECONDS: float = 300.0   # 5 minutes
_REAP_INTERVAL_SECONDS: float = 60.0  # check every minute


class _ForwarderEntry(NamedTuple):
    server: asyncio.AbstractServer
    host: str
    port: int
    last_used: float  # time.monotonic() of last accepted connection


class TunnelForwarder:
    def __init__(self, manager: TunnelManager | None = None,
                 idle_timeout: float = IDLE_TIMEOUT_SECONDS):
        self._manager = manager or get_manager()
        self._servers: dict[tuple[str, str, int], _ForwarderEntry] = {}
        self._lock = asyncio.Lock()
        self._idle_timeout = idle_timeout
        self._reap_task: asyncio.Task | None = None

    def _start_reaper(self) -> None:
        if self._reap_task is None or self._reap_task.done():
            self._reap_task = asyncio.ensure_future(self._reap_loop())

    async def _reap_loop(self) -> None:
        """Background task that closes idle forwarder listeners."""
        while True:
            await asyncio.sleep(_REAP_INTERVAL_SECONDS)
            await self._reap_idle()

    async def _reap_idle(self) -> None:
        now = time.monotonic()
        async with self._lock:
            stale = [
                key for key, entry in self._servers.items()
                if now - entry.last_used > self._idle_timeout
            ]
            for key in stale:
                entry = self._servers.pop(key)
                entry.server.close()
                try:
                    await entry.server.wait_closed()
                except Exception:
                    pass

    async def endpoint_for(self, agent_id: str, host: str, port: int) -> tuple[str, int]:
        key = (agent_id, host, port)
        async with self._lock:
            existing = self._servers.get(key)
            if existing is not None:
                # Refresh last_used timestamp
                self._servers[key] = existing._replace(last_used=time.monotonic())
                return existing.host, existing.port

            async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                # Refresh idle timer on each new connection
                async with self._lock:
                    if key in self._servers:
                        self._servers[key] = self._servers[key]._replace(
                            last_used=time.monotonic()
                        )
                try:
                    stream = await self._manager.dial(agent_id, host, port)
                except Exception:
                    writer.close()
                    return
                await _pump(reader, writer, stream)

            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            sock = server.sockets[0].getsockname()
            self._servers[key] = _ForwarderEntry(
                server=server,
                host=sock[0],
                port=sock[1],
                last_used=time.monotonic(),
            )
            self._start_reaper()
            return sock[0], sock[1]

    async def stop_all(self) -> None:
        if self._reap_task is not None:
            self._reap_task.cancel()
            try:
                await self._reap_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reap_task = None
        async with self._lock:
            for entry in self._servers.values():
                entry.server.close()
            for entry in list(self._servers.values()):
                try:
                    await entry.server.wait_closed()
                except Exception:
                    pass
            self._servers.clear()


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, stream) -> None:
    async def to_stream():
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await stream.write(data)
        except Exception:
            pass
        finally:
            await stream.close()

    async def to_sock():
        try:
            while True:
                data = await stream.read()
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    await asyncio.gather(to_stream(), to_sock(), return_exceptions=True)


_forwarder: TunnelForwarder | None = None


def get_forwarder() -> TunnelForwarder:
    global _forwarder
    if _forwarder is None:
        _forwarder = TunnelForwarder()
    return _forwarder
