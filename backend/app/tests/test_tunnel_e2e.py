# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Full-traffic end-to-end test of the reverse tunnel.

Exercises the *real* relay entrypoint (``run_agent_tunnel``), the real
``WebSocketTransport`` adapter, the real ``TunnelSession`` / ``TunnelManager``
singleton, and the real ``SocksServer`` — with a provider that dials a *real*
local TCP echo server, and a real SOCKS5 client consuming through the tunnel.

Only the two DB lookups the relay does (org-by-bearer, agent-by-id) are stubbed;
everything else is the production code path. Everything runs in one event loop so
the process-wide manager singleton is loop-safe. Run with --noconftest.
"""
import asyncio
import struct
import types
import uuid

from starlette.websockets import WebSocketDisconnect

from app.tunnel import protocol as proto
from app.tunnel import relay as relay_mod
from app.tunnel.manager import get_manager
from app.tunnel.socks import SocksServer


class FakeWebSocket:
    """In-memory stand-in for a Starlette WebSocket (the wire, not the logic).

    ``run_agent_tunnel`` / ``WebSocketTransport`` drive the relay-facing side;
    the test's provider coroutine drives the agent-facing side."""

    def __init__(self, headers: dict, query_params: dict):
        self.headers = headers
        self.query_params = query_params
        self._to_relay: asyncio.Queue = asyncio.Queue()    # provider -> relay
        self._from_relay: asyncio.Queue = asyncio.Queue()  # relay -> provider
        self.close_code = None

    # --- relay-facing surface ---
    async def accept(self):
        pass

    async def receive_bytes(self) -> bytes:
        item = await self._to_relay.get()
        if item is None:
            raise WebSocketDisconnect(code=1000)
        return item

    async def send_bytes(self, data: bytes):
        self._from_relay.put_nowait(data)

    async def close(self, code: int = 1000):
        self.close_code = code

    # --- provider-facing helpers ---
    async def provider_recv(self) -> bytes:
        return await self._from_relay.get()

    def provider_send(self, data: bytes):
        self._to_relay.put_nowait(data)

    def provider_disconnect(self):
        self._to_relay.put_nowait(None)


async def _provider(ws: FakeWebSocket):
    """Reimplements the agent tunnel client: dials real local TCP per OPEN."""
    streams: dict[int, asyncio.StreamWriter] = {}
    pumps: list[asyncio.Task] = []

    async def pump(sid: int, reader: asyncio.StreamReader):
        try:
            while True:
                data = await reader.read(32768)
                if not data:
                    break
                ws.provider_send(proto.encode(sid, proto.DATA, data))
        finally:
            ws.provider_send(proto.encode(sid, proto.CLOSE))

    try:
        while True:
            frame = proto.decode(await ws.provider_recv())
            if frame.type == proto.OPEN:
                host, port = proto.parse_open(frame.payload)
                try:
                    r, w = await asyncio.open_connection(host, port)
                except Exception as exc:  # noqa: BLE001
                    ws.provider_send(proto.encode(frame.stream_id, proto.OPEN_ERR, str(exc).encode()))
                    continue
                streams[frame.stream_id] = w
                ws.provider_send(proto.encode(frame.stream_id, proto.OPEN_OK))
                pumps.append(asyncio.ensure_future(pump(frame.stream_id, r)))
            elif frame.type == proto.DATA:
                w = streams.get(frame.stream_id)
                if w:
                    w.write(frame.payload)
                    await w.drain()
            elif frame.type == proto.CLOSE:
                w = streams.pop(frame.stream_id, None)
                if w:
                    w.close()
    except asyncio.CancelledError:
        pass
    finally:
        for p in pumps:
            p.cancel()
        for w in streams.values():
            w.close()


async def _echo_server():
    async def handle(reader, writer):
        try:
            while True:
                data = await reader.read(32768)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:  # noqa: BLE001
            pass
        finally:
            writer.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def _socks_connect(host, port, agent_id, dst_ip, dst_port, password="tok"):
    """Minimal SOCKS5 client: user/pass auth (user=agent_id) -> CONNECT IPv4."""
    r, w = await asyncio.open_connection(host, port)
    w.write(bytes([0x05, 0x01, 0x02])); await w.drain()
    assert await r.readexactly(2) == bytes([0x05, 0x02])
    u = agent_id.encode(); p = password.encode()
    w.write(bytes([0x01, len(u)]) + u + bytes([len(p)]) + p); await w.drain()
    assert await r.readexactly(2) == bytes([0x01, 0x00])
    ip = bytes(int(o) for o in dst_ip.split("."))
    w.write(bytes([0x05, 0x01, 0x00, 0x01]) + ip + struct.pack("!H", dst_port)); await w.drain()
    rep = await r.readexactly(4)
    await r.readexactly(4 + 2)  # bound addr + port
    return r, w, rep[1]


def _patch_relay_auth(monkeypatch, agent_id: uuid.UUID, allowlist: list[str], enabled: bool = True):
    org = types.SimpleNamespace(organization_id=uuid.uuid4())
    reg = types.SimpleNamespace(id=agent_id, tunnel_enabled=enabled, tunnel_allowlist=allowlist, tunnel_max_concurrent=10)

    async def fake_resolve(_authz, _db):
        return org

    async def fake_load(_db, _org_id, _agent_id):
        return reg

    monkeypatch.setattr(relay_mod, "_resolve_org_by_bearer", fake_resolve)
    monkeypatch.setattr(relay_mod, "_load_agent", fake_load)


def test_end_to_end_socks_through_real_relay(monkeypatch):
    async def run():
        srv, echo_port = await _echo_server()
        agent_id = uuid.uuid4()
        _patch_relay_auth(monkeypatch, agent_id, [f"127.0.0.1:{echo_port}"])

        ws = FakeWebSocket({"authorization": "Bearer x"}, {"agent_id": str(agent_id)})
        relay_task = asyncio.ensure_future(relay_mod.run_agent_tunnel(ws, db=None))
        provider_task = asyncio.ensure_future(_provider(ws))

        # Wait until the relay has registered the agent with the manager.
        mgr = get_manager()
        for _ in range(100):
            if mgr.is_online(str(agent_id)):
                break
            await asyncio.sleep(0.02)
        assert mgr.is_online(str(agent_id)), "relay did not register the agent"

        socks = SocksServer(mgr, host="127.0.0.1", port=0, auth_token="tok")
        sh, sp = await socks.start()

        # Consume: dial the echo server through the tunnel and round-trip bytes.
        r, w, rep = await _socks_connect(sh, sp, str(agent_id), "127.0.0.1", echo_port)
        assert rep == 0x00, f"SOCKS CONNECT failed rep={rep}"
        payload = b"hello through the reverse tunnel" * 4
        w.write(payload); await w.drain()
        got = await asyncio.wait_for(r.readexactly(len(payload)), timeout=5)
        assert got == payload

        # A destination not on the allowlist is refused (deny-by-default).
        r2, w2, rep2 = await _socks_connect(sh, sp, str(agent_id), "127.0.0.1", echo_port + 1)
        assert rep2 == 0x02  # not allowed

        w.close(); w2.close()
        await socks.stop()
        ws.provider_disconnect()
        provider_task.cancel()
        try:
            await asyncio.wait_for(relay_task, timeout=5)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        srv.close()
        await srv.wait_closed()
        assert not mgr.is_online(str(agent_id)), "relay did not unregister on disconnect"

    asyncio.run(run())
