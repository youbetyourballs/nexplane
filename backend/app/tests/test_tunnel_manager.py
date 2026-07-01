# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Tests for the reverse-tunnel session/stream manager, using an in-memory
transport and a fake echo-agent (no WebSocket / network). Run with --noconftest.
"""
import asyncio

import pytest

from app.tunnel import protocol as proto
from app.tunnel.authorizer import parse_allowlist
from app.tunnel.manager import (
    TunnelSession, TunnelManager, TransportClosed,
    DialError, DestinationDenied, TunnelUnavailable,
)


class _Pipe:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()


class InMemoryTransport:
    """Reads from `inbound`, writes to `outbound`. None enqueued = closed."""
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
    cp = InMemoryTransport(inbound=a, outbound=b)     # control-plane end
    agent = InMemoryTransport(inbound=b, outbound=a)  # agent end
    return cp, agent


async def _fake_agent(agent: InMemoryTransport):
    """Minimal agent: OPEN 'deny*' -> OPEN_ERR; else OPEN_OK; DATA -> echo; CLOSE -> stop."""
    try:
        while True:
            frame = proto.decode(await agent.recv_bytes())
            if frame.type == proto.OPEN:
                host, _ = proto.parse_open(frame.payload)
                if host.startswith("deny"):
                    await agent.send_bytes(proto.encode(frame.stream_id, proto.OPEN_ERR, b"refused"))
                else:
                    await agent.send_bytes(proto.encode(frame.stream_id, proto.OPEN_OK))
            elif frame.type == proto.DATA:
                await agent.send_bytes(proto.encode(frame.stream_id, proto.DATA, frame.payload))
            elif frame.type == proto.CLOSE:
                await agent.send_bytes(proto.encode(frame.stream_id, proto.CLOSE))
    except TransportClosed:
        pass


def test_dial_and_echo():
    async def run():
        cp, agent = _pair()
        at = asyncio.ensure_future(_fake_agent(agent))
        s = TunnelSession(cp); s.start()
        stream = await s.dial("echo.local", 5432)
        await stream.write(b"hello")
        assert await stream.read() == b"hello"
        await stream.write(b"world")
        assert await stream.read() == b"world"
        await stream.close()
        await s.close(); at.cancel()
    asyncio.run(run())


def test_dial_refused_raises():
    async def run():
        cp, agent = _pair()
        at = asyncio.ensure_future(_fake_agent(agent))
        s = TunnelSession(cp); s.start()
        with pytest.raises(DialError):
            await s.dial("deny.local", 22)
        await s.close(); at.cancel()
    asyncio.run(run())


def test_manager_allowlist_gate():
    async def run():
        cp, agent = _pair()
        at = asyncio.ensure_future(_fake_agent(agent))
        s = TunnelSession(cp); s.start()
        m = TunnelManager()
        m.register("agent-1", s, parse_allowlist(["10.0.0.0/8:5432"]))
        assert m.is_online("agent-1")
        # allowed
        stream = await m.dial("agent-1", "10.1.2.3", 5432)
        await stream.write(b"ping"); assert await stream.read() == b"ping"
        # denied by allowlist (never reaches the agent)
        with pytest.raises(DestinationDenied):
            await m.dial("agent-1", "10.1.2.3", 5433)
        with pytest.raises(DestinationDenied):
            await m.dial("agent-1", "8.8.8.8", 5432)
        await s.close(); at.cancel()
    asyncio.run(run())


def test_manager_unknown_agent():
    async def run():
        m = TunnelManager()
        with pytest.raises(TunnelUnavailable):
            await m.dial("nope", "10.0.0.1", 443)
    asyncio.run(run())


def test_dial_timeout_when_agent_silent():
    async def run():
        cp, _agent = _pair()  # no fake agent running -> no OPEN_OK
        s = TunnelSession(cp); s.start()
        with pytest.raises(DialError):
            await s.dial("quiet.local", 80, timeout=0.2)
        await s.close()
    asyncio.run(run())


def test_stream_close_unblocks_pending_reader():
    # Regression: TunnelStream.close() must feed EOF locally. The agent's echoed
    # CLOSE arrives after we've forgotten the stream, so without the local EOF a
    # reader blocked in stream.read() would hang forever (the SOCKS relay bug).
    async def run():
        cp, agent = _pair()
        at = asyncio.ensure_future(_fake_agent(agent))
        s = TunnelSession(cp); s.start()
        stream = await s.dial("echo.local", 5432)

        async def reader():
            return await stream.read()

        read_task = asyncio.ensure_future(reader())
        await asyncio.sleep(0.05)          # let the reader block on an empty stream
        await stream.close()               # must unblock it with EOF
        got = await asyncio.wait_for(read_task, timeout=1.0)
        assert got == b""
        await s.close(); at.cancel()
    asyncio.run(run())


def test_session_close_feeds_eof():
    async def run():
        cp, agent = _pair()
        at = asyncio.ensure_future(_fake_agent(agent))
        s = TunnelSession(cp); s.start()
        stream = await s.dial("echo.local", 5432)
        await s.close()          # closes read loop -> EOF to open streams
        assert await stream.read() == b""
        at.cancel()
    asyncio.run(run())
