# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""SOCKS5 front-end tests: a real TCP SOCKS5 client -> SocksServer -> tunnel
manager -> in-memory fake echo agent. Run with --noconftest."""
import asyncio
import struct

from app.tunnel import protocol as proto
from app.tunnel.authorizer import parse_allowlist
from app.tunnel.manager import TunnelSession, TunnelManager, TransportClosed
from app.tunnel.socks import SocksServer


class _Pipe:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()


class InMemoryTransport:
    def __init__(self, inbound: _Pipe, outbound: _Pipe):
        self.inb, self.outb = inbound, outbound

    async def send_bytes(self, data: bytes) -> None:
        self.outb.q.put_nowait(data)

    async def recv_bytes(self) -> bytes:
        item = await self.inb.q.get()
        if item is None:
            raise TransportClosed()
        return item


def _pair():
    a, b = _Pipe(), _Pipe()
    return InMemoryTransport(a, b), InMemoryTransport(b, a)


async def _fake_echo_agent(agent: InMemoryTransport):
    try:
        while True:
            frame = proto.decode(await agent.recv_bytes())
            if frame.type == proto.OPEN:
                await agent.send_bytes(proto.encode(frame.stream_id, proto.OPEN_OK))
            elif frame.type == proto.DATA:
                await agent.send_bytes(proto.encode(frame.stream_id, proto.DATA, frame.payload))
            elif frame.type == proto.CLOSE:
                await agent.send_bytes(proto.encode(frame.stream_id, proto.CLOSE))
    except TransportClosed:
        pass


async def _socks_connect(host, port, agent_id, dst_host, dst_port, password="x"):
    """Minimal SOCKS5 client: greet -> user/pass auth (user=agent_id) -> CONNECT domain."""
    r, w = await asyncio.open_connection(host, port)
    w.write(bytes([0x05, 0x01, 0x02])); await w.drain()          # 1 method: user/pass
    assert await r.readexactly(2) == bytes([0x05, 0x02])
    u = agent_id.encode(); p = password.encode()
    w.write(bytes([0x01, len(u)]) + u + bytes([len(p)]) + p); await w.drain()
    assert await r.readexactly(2) == bytes([0x01, 0x00])         # auth ok
    d = dst_host.encode()
    w.write(bytes([0x05, 0x01, 0x00, 0x03, len(d)]) + d + struct.pack("!H", dst_port)); await w.drain()
    rep = await r.readexactly(4)
    # consume bound addr (IPv4) + port
    await r.readexactly(4 + 2)
    return r, w, rep[1]  # rep[1] = SOCKS reply code


def _setup_manager():
    cp, agent = _pair()
    at = asyncio.ensure_future(_fake_echo_agent(agent))
    session = TunnelSession(cp); session.start()
    m = TunnelManager()
    m.register("agent-x", session, parse_allowlist(["10.0.0.0/8:5432", "echo.local:5432"]))
    return m, session, at


def test_socks_connect_and_echo():
    async def run():
        m, session, at = _setup_manager()
        server = SocksServer(m, host="127.0.0.1", port=0)
        h, port = await server.start()
        r, w, rep = await _socks_connect(h, port, "agent-x", "echo.local", 5432)
        assert rep == 0x00  # success
        w.write(b"ping over socks"); await w.drain()
        assert await r.readexactly(len(b"ping over socks")) == b"ping over socks"
        w.close()
        await server.stop(); await session.close(); at.cancel()
    asyncio.run(run())


def test_socks_denied_destination():
    async def run():
        m, session, at = _setup_manager()
        server = SocksServer(m, host="127.0.0.1", port=0)
        h, port = await server.start()
        # 8.8.8.8:5432 is not on the allowlist -> SOCKS reply 0x02 (not allowed)
        r, w, rep = await _socks_connect(h, port, "agent-x", "8.8.8.8", 5432)
        assert rep == 0x02
        w.close()
        await server.stop(); await session.close(); at.cancel()
    asyncio.run(run())


def test_socks_unknown_agent():
    async def run():
        m = TunnelManager()
        server = SocksServer(m, host="127.0.0.1", port=0)
        h, port = await server.start()
        r, w, rep = await _socks_connect(h, port, "ghost", "10.0.0.1", 5432)
        assert rep == 0x03  # network unreachable (agent not connected)
        w.close()
        await server.stop()
    asyncio.run(run())
