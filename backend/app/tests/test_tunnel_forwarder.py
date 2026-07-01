# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Forwarder e2e: plain TCP client -> local forwarder -> tunnel manager -> fake echo agent.
Run with --noconftest."""
import asyncio
from app.tunnel import protocol as proto
from app.tunnel.authorizer import parse_allowlist
from app.tunnel.manager import TunnelSession, TunnelManager, TransportClosed
from app.tunnel.forwarder import TunnelForwarder


class _Pipe:
    def __init__(self): self.q: asyncio.Queue = asyncio.Queue()

class InMemoryTransport:
    def __init__(self, inb, outb): self.inb, self.outb = inb, outb
    async def send_bytes(self, d): self.outb.q.put_nowait(d)
    async def recv_bytes(self):
        item = await self.inb.q.get()
        if item is None: raise TransportClosed()
        return item

def _pair():
    a, b = _Pipe(), _Pipe()
    return InMemoryTransport(a, b), InMemoryTransport(b, a)

async def _echo_agent(agent):
    try:
        while True:
            f = proto.decode(await agent.recv_bytes())
            if f.type == proto.OPEN: await agent.send_bytes(proto.encode(f.stream_id, proto.OPEN_OK))
            elif f.type == proto.DATA: await agent.send_bytes(proto.encode(f.stream_id, proto.DATA, f.payload))
            elif f.type == proto.CLOSE: await agent.send_bytes(proto.encode(f.stream_id, proto.CLOSE))
    except TransportClosed: pass

def test_forwarder_round_trip():
    async def run():
        cp, agent = _pair()
        at = asyncio.ensure_future(_echo_agent(agent))
        session = TunnelSession(cp); session.start()
        m = TunnelManager(); m.register("agent-x", session, parse_allowlist(["10.0.0.0/8:5432"]))
        fwd = TunnelForwarder(manager=m)
        host, port = await fwd.endpoint_for("agent-x", "10.1.2.3", 5432)
        r, w = await asyncio.open_connection(host, port)
        w.write(b"hello"); await w.drain()
        assert await r.readexactly(5) == b"hello"
        w.close()
        # reuse: same dest returns same local port
        host2, port2 = await fwd.endpoint_for("agent-x", "10.1.2.3", 5432)
        assert (host2, port2) == (host, port)
        await fwd.stop_all(); await session.close(); at.cancel()
    asyncio.run(run())
