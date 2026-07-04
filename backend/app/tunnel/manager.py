# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Reverse-tunnel session + stream manager (control-plane side).

A ``TunnelSession`` wraps one connected agent's transport (a WebSocket, or any
object with async ``send_bytes``/``recv_bytes``) and multiplexes many logical
TCP streams over it using the frame protocol. The control plane *initiates*
streams via ``dial(host, port)``; the agent dials the destination locally and
pipes bytes back.

``TunnelManager`` is the registry of connected agents + the authorization gate:
``dial(agent_id, host, port)`` checks the agent's allowlist before opening a
stream.

Transport is abstracted so the core is unit-testable in-memory (no WebSocket /
network). The FastAPI relay endpoint adapts a Starlette WebSocket to this
interface.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol

from . import protocol as proto
from .authorizer import Rule, is_allowed

_log = logging.getLogger(__name__)


class TransportClosed(Exception):
    pass


class Transport(Protocol):
    async def send_bytes(self, data: bytes) -> None: ...
    async def recv_bytes(self) -> bytes: ...  # raise TransportClosed when closed


class TunnelError(Exception):
    pass


class TunnelUnavailable(TunnelError):
    """The requested agent is not connected."""


class DestinationDenied(TunnelError):
    """The destination is not on the agent's allowlist."""


class DialError(TunnelError):
    """The agent could not open the stream (refused/failed/timeout)."""


class TunnelStream:
    """A single multiplexed byte stream over a session."""

    def __init__(self, session: "TunnelSession", stream_id: int):
        self._session = session
        self.stream_id = stream_id
        self._rq: asyncio.Queue = asyncio.Queue()   # payloads; b"" sentinel = EOF
        self._open: asyncio.Future = asyncio.get_event_loop().create_future()
        self._closed = False

    async def write(self, data: bytes) -> None:
        if self._closed:
            raise TransportClosed("stream closed")
        await self._session._send(proto.encode(self.stream_id, proto.DATA, data))

    async def read(self) -> bytes:
        """Return next chunk, or b"" at end-of-stream."""
        return await self._rq.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._session._send(proto.encode(self.stream_id, proto.CLOSE))
        except Exception:
            pass
        # Unblock any local reader: once we close, no more data is expected on
        # this stream (and the agent's echoed CLOSE would arrive after we've
        # forgotten the stream, so it can't feed EOF for us).
        self._feed_eof()
        self._session._forget(self.stream_id)

    # --- internal (driven by the session read loop) ---
    def _feed(self, data: bytes) -> None:
        self._rq.put_nowait(data)

    def _feed_eof(self) -> None:
        self._rq.put_nowait(b"")

    def _resolve_open_ok(self) -> None:
        if not self._open.done():
            self._open.set_result(True)

    def _resolve_open_err(self, reason: str) -> None:
        if not self._open.done():
            self._open.set_exception(DialError(reason or "dial failed"))


class TunnelSession:
    """Control-plane end of one agent's tunnel."""

    def __init__(self, transport: Transport):
        self._t = transport
        self._streams: dict[int, TunnelStream] = {}
        self._next_id = 1
        self._read_task: asyncio.Task | None = None
        self._closed = False

    def start(self) -> None:
        self._read_task = asyncio.ensure_future(self._read_loop())

    async def _send(self, data: bytes) -> None:
        await self._t.send_bytes(data)

    def _alloc_id(self) -> int:
        sid = self._next_id
        self._next_id = (self._next_id + 1) & 0xFFFFFFFF or 1
        return sid

    def _forget(self, stream_id: int) -> None:
        self._streams.pop(stream_id, None)

    async def dial(self, host: str, port: int, *, timeout: float = 10.0) -> TunnelStream:
        if self._closed:
            raise TunnelUnavailable("session closed")
        sid = self._alloc_id()
        stream = TunnelStream(self, sid)
        self._streams[sid] = stream
        await self._send(proto.open_frame(sid, host, port))
        try:
            await asyncio.wait_for(stream._open, timeout)
        except asyncio.TimeoutError:
            self._forget(sid)
            raise DialError(f"dial to {host}:{port} timed out")
        except DialError:
            self._forget(sid)
            raise
        return stream

    async def _read_loop(self) -> None:
        try:
            while True:
                data = await self._t.recv_bytes()
                frame = proto.decode(data)
                stream = self._streams.get(frame.stream_id)
                if frame.type == proto.DATA:
                    if stream:
                        stream._feed(frame.payload)
                elif frame.type == proto.OPEN_OK:
                    if stream:
                        stream._resolve_open_ok()
                elif frame.type == proto.OPEN_ERR:
                    if stream:
                        stream._resolve_open_err(frame.payload.decode("utf-8", "replace"))
                elif frame.type == proto.CLOSE:
                    if stream:
                        stream._feed_eof()
                # OPEN frames are agent-inbound only; the control plane ignores them.
        except (TransportClosed, asyncio.CancelledError):
            pass
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        self._closed = True
        for stream in list(self._streams.values()):
            stream._feed_eof()
            if not stream._open.done():
                stream._resolve_open_err("session closed")
        self._streams.clear()

    async def wait(self) -> None:
        """Block until the read loop finishes (transport closed)."""
        if self._read_task:
            try:
                await self._read_task
            except (asyncio.CancelledError, Exception):
                pass

    async def close(self) -> None:
        self._closed = True
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except (asyncio.CancelledError, Exception):
                pass


_DEFAULT_MAX_CONCURRENT: int = 10


class ConcurrencyLimitExceeded(TunnelError):
    """The agent has reached its maximum concurrent stream limit."""


def _fire(coro) -> None:
    """Schedule a coroutine as a background task (fire-and-forget)."""
    asyncio.ensure_future(coro)


async def _insert_audit(agent_id: str, host: str, port: int) -> uuid.UUID | None:
    """Insert a tunnel_dial_audit row and return its id, or None on failure."""
    try:
        from app.database import AsyncSessionLocal
        from app.models.tunnel_audit import TunnelDialAudit
        audit_id = uuid.uuid4()
        async with AsyncSessionLocal() as db:
            row = TunnelDialAudit(
                id=audit_id,
                agent_id=uuid.UUID(agent_id),
                destination_host=host,
                destination_port=port,
                opened_at=datetime.now(timezone.utc),
            )
            db.add(row)
            await db.commit()
        return audit_id
    except Exception as exc:
        _log.debug("tunnel audit insert failed: %s", exc)
        return None


async def _close_audit(
    audit_id: uuid.UUID,
    *,
    bytes_sent: int = 0,
    bytes_recv: int = 0,
    close_reason: str = "normal",
) -> None:
    """Update a tunnel_dial_audit row with close stats."""
    try:
        from sqlalchemy import update
        from app.database import AsyncSessionLocal
        from app.models.tunnel_audit import TunnelDialAudit
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(TunnelDialAudit)
                .where(TunnelDialAudit.id == audit_id)
                .values(
                    closed_at=datetime.now(timezone.utc),
                    bytes_sent=bytes_sent,
                    bytes_recv=bytes_recv,
                    close_reason=close_reason,
                )
            )
            await db.commit()
    except Exception as exc:
        _log.debug("tunnel audit close failed: %s", exc)


class _CountedStream:
    """Wraps a TunnelStream and decrements the active-count on close."""

    def __init__(
        self,
        stream: "TunnelStream",
        manager: "TunnelManager",
        agent_id: str,
        *,
        audit_id: uuid.UUID | None = None,
    ):
        self._stream = stream
        self._manager = manager
        self._agent_id = agent_id
        self._counted = True
        self._audit_id = audit_id
        self._bytes_sent: int = 0
        self._bytes_recv: int = 0

    @property
    def stream_id(self) -> int:
        return self._stream.stream_id

    async def write(self, data: bytes) -> None:
        self._bytes_sent += len(data)
        await self._stream.write(data)

    async def read(self) -> bytes:
        chunk = await self._stream.read()
        self._bytes_recv += len(chunk)
        return chunk

    async def close(self, *, close_reason: str = "normal") -> None:
        if self._counted:
            self._counted = False
            self._manager._active_count[self._agent_id] = max(
                0, self._manager._active_count.get(self._agent_id, 1) - 1
            )
        if self._audit_id is not None:
            _fire(_close_audit(
                self._audit_id,
                bytes_sent=self._bytes_sent,
                bytes_recv=self._bytes_recv,
                close_reason=close_reason,
            ))
            self._audit_id = None
        await self._stream.close()

    def _feed(self, data: bytes) -> None:
        self._stream._feed(data)

    def _feed_eof(self) -> None:
        self._stream._feed_eof()

    def _resolve_open_ok(self) -> None:
        self._stream._resolve_open_ok()

    def _resolve_open_err(self, reason: str) -> None:
        self._stream._resolve_open_err(reason)


class TunnelManager:
    """Registry of connected agents + the authorization gate for dials."""

    def __init__(self):
        self._sessions: dict[str, tuple[TunnelSession, list[Rule]]] = {}
        self._max_concurrent: dict[str, int] = {}       # agent_id → limit
        self._active_count: dict[str, int] = {}         # agent_id → open streams

    def register(self, agent_id: str, session: TunnelSession, allowlist: list[Rule],
                 max_concurrent: int = _DEFAULT_MAX_CONCURRENT) -> None:
        self._sessions[agent_id] = (session, allowlist)
        self._max_concurrent[agent_id] = max_concurrent
        self._active_count[agent_id] = 0

    def unregister(self, agent_id: str) -> None:
        self._sessions.pop(agent_id, None)
        self._max_concurrent.pop(agent_id, None)
        self._active_count.pop(agent_id, None)

    def is_online(self, agent_id: str) -> bool:
        return agent_id in self._sessions

    def online_agents(self) -> list[str]:
        return list(self._sessions)

    def active_stream_count(self, agent_id: str) -> int:
        return self._active_count.get(agent_id, 0)

    def agent_stats(self) -> dict[str, dict[str, object]]:
        """Return per-agent stats: ``{agent_id: {online: bool, active_streams: int}}``."""
        return {
            agent_id: {
                "online": True,
                "active_streams": self._active_count.get(agent_id, 0),
            }
            for agent_id in self._sessions
        }

    async def dial(self, agent_id: str, host: str, port: int, *, timeout: float = 10.0) -> TunnelStream:
        entry = self._sessions.get(agent_id)
        if entry is None:
            raise TunnelUnavailable(f"agent {agent_id!r} is not connected")
        session, allowlist = entry

        # Enforce concurrency limit before opening a new stream
        limit = self._max_concurrent.get(agent_id, _DEFAULT_MAX_CONCURRENT)
        current = self._active_count.get(agent_id, 0)
        if current >= limit:
            raise ConcurrencyLimitExceeded(
                f"agent {agent_id!r} has reached its concurrency limit of {limit} streams"
            )

        if not is_allowed(allowlist, host, port):
            # Record denied dial (fire-and-forget, non-blocking)
            async def _denied_audit():
                aid = await _insert_audit(agent_id, host, port)
                if aid is not None:
                    await _close_audit(aid, close_reason="denied")
            _fire(_denied_audit())
            raise DestinationDenied(f"{host}:{port} is not allowed for agent {agent_id!r}")

        self._active_count[agent_id] = current + 1
        try:
            stream = await session.dial(host, port, timeout=timeout)
        except Exception:
            self._active_count[agent_id] = max(0, self._active_count.get(agent_id, 1) - 1)
            raise

        # Fire-and-forget audit insert (non-blocking)
        audit_id: uuid.UUID | None = None
        try:
            audit_id = await asyncio.wait_for(_insert_audit(agent_id, host, port), timeout=2.0)
        except Exception as exc:
            _log.debug("tunnel audit insert skipped: %s", exc)

        # Wrap the stream so we decrement the counter when it closes
        return _CountedStream(stream, self, agent_id, audit_id=audit_id)


# Process-wide manager instance (the relay endpoint registers agents here).
_manager = TunnelManager()


def get_manager() -> TunnelManager:
    return _manager
