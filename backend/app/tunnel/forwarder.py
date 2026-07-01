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
"""
from __future__ import annotations

import asyncio

from .manager import TunnelManager, get_manager


class TunnelForwarder:
    def __init__(self, manager: TunnelManager | None = None):
        self._manager = manager or get_manager()
        self._servers: dict[tuple[str, str, int], tuple[asyncio.AbstractServer, str, int]] = {}
        self._lock = asyncio.Lock()

    async def endpoint_for(self, agent_id: str, host: str, port: int) -> tuple[str, int]:
        key = (agent_id, host, port)
        async with self._lock:
            existing = self._servers.get(key)
            if existing is not None:
                _srv, lhost, lport = existing
                return lhost, lport

            async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
                try:
                    stream = await self._manager.dial(agent_id, host, port)
                except Exception:
                    writer.close()
                    return
                await _pump(reader, writer, stream)

            server = await asyncio.start_server(handle, "127.0.0.1", 0)
            sock = server.sockets[0].getsockname()
            self._servers[key] = (server, sock[0], sock[1])
            return sock[0], sock[1]

    async def stop_all(self) -> None:
        async with self._lock:
            for server, _h, _p in self._servers.values():
                server.close()
            for server, _h, _p in list(self._servers.values()):
                await server.wait_closed()
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
