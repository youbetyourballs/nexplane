# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""No-deadlock proof: a BLOCKING (threaded) TCP client round-trips through the
in-process forwarder without starving the app event loop.

The forwarder's accept coroutine runs on the app event loop. Raw-TCP connector
clients (psycopg2, redis, ...) are synchronous and would block the loop if run
inline. Executors therefore run those blocking calls via asyncio.to_thread, so
the loop stays free to accept the forwarded connection. This test mirrors that
pattern with a plain synchronous socket and asserts the round-trip completes.

Run with --noconftest."""
import asyncio
import socket

from app.tunnel import protocol as proto
from app.tunnel.authorizer import parse_allowlist
from app.tunnel.manager import TunnelSession, TunnelManager, TransportClosed
from app.tunnel.forwarder import TunnelForwarder


class _Pipe:
    def __init__(self):
        self.q: asyncio.Queue = asyncio.Queue()


class InMemoryTransport:
    def __init__(self, inb, outb):
        self.inb, self.outb = inb, outb

    async def send_bytes(self, d):
        self.outb.q.put_nowait(d)

    async def recv_bytes(self):
        item = await self.inb.q.get()
        if item is None:
            raise TransportClosed()
        return item


def _pair():
    a, b = _Pipe(), _Pipe()
    return InMemoryTransport(a, b), InMemoryTransport(b, a)


async def _echo_agent(agent):
    try:
        while True:
            f = proto.decode(await agent.recv_bytes())
            if f.type == proto.OPEN:
                await agent.send_bytes(proto.encode(f.stream_id, proto.OPEN_OK))
            elif f.type == proto.DATA:
                await agent.send_bytes(proto.encode(f.stream_id, proto.DATA, f.payload))
            elif f.type == proto.CLOSE:
                await agent.send_bytes(proto.encode(f.stream_id, proto.CLOSE))
    except TransportClosed:
        pass


def _blocking_roundtrip(host: str, port: int, payload: bytes) -> bytes:
    """Plain synchronous socket connect/sendall/recv — the kind of blocking call
    an executor must push off the loop via asyncio.to_thread."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10)
    try:
        s.connect((host, port))
        s.sendall(payload)
        return s.recv(len(payload))
    finally:
        s.close()


def test_blocking_client_round_trips_without_starving_loop():
    async def run():
        cp, agent = _pair()
        at = asyncio.ensure_future(_echo_agent(agent))
        session = TunnelSession(cp)
        session.start()
        m = TunnelManager()
        m.register("agent-x", session, parse_allowlist(["10.0.0.0/8:5432"]))
        fwd = TunnelForwarder(manager=m)
        host, port = await fwd.endpoint_for("agent-x", "10.1.2.3", 5432)

        # A blocking client running in a worker thread must NOT deadlock: the
        # event loop stays free (via to_thread) to accept + pump the connection.
        got = await asyncio.to_thread(_blocking_roundtrip, host, port, b"ping")
        assert got == b"ping"

        await fwd.stop_all()
        await session.close()
        at.cancel()

    asyncio.run(run())
