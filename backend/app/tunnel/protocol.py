# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Agent reverse-tunnel wire protocol.

A single WebSocket carries many logical TCP streams (mux) between the control
plane and a connected agent. Each WebSocket **binary message** is exactly one
frame:

    [stream_id: 4 bytes big-endian uint32][type: 1 byte][payload: bytes]

We deliberately do NOT use yamux (Go-native, awkward to speak from Python) — the
WebSocket already frames messages, so a tiny stream-id header is all we need,
and both the Go agent and the Python relay implement it in a few lines.

Frame types:
  OPEN     control-plane -> agent : payload = b"host:port" (utf-8). Open a stream
                                    and dial that destination locally.
  OPEN_OK  agent -> control-plane : dial succeeded.
  OPEN_ERR agent -> control-plane : payload = reason (utf-8). Dial refused/failed.
  DATA     both directions        : payload = raw bytes for the stream.
  CLOSE    both directions        : half/close the stream.

See docs/superpowers/specs/2026-07-01-agent-reverse-tunnel-design.md (nexplane-deploy).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

OPEN = 1
OPEN_OK = 2
OPEN_ERR = 3
DATA = 4
CLOSE = 5

_TYPES = {OPEN, OPEN_OK, OPEN_ERR, DATA, CLOSE}
_HEADER = struct.Struct(">IB")  # uint32 stream_id, uint8 type
HEADER_LEN = _HEADER.size  # 5


class ProtocolError(ValueError):
    """Raised when a frame cannot be decoded."""


@dataclass(frozen=True)
class Frame:
    stream_id: int
    type: int
    payload: bytes = b""


def encode(stream_id: int, type: int, payload: bytes = b"") -> bytes:
    if type not in _TYPES:
        raise ProtocolError(f"unknown frame type {type!r}")
    if not (0 <= stream_id <= 0xFFFFFFFF):
        raise ProtocolError(f"stream_id out of range: {stream_id}")
    return _HEADER.pack(stream_id, type) + payload


def decode(data: bytes) -> Frame:
    if len(data) < HEADER_LEN:
        raise ProtocolError(f"frame too short: {len(data)} bytes")
    stream_id, type = _HEADER.unpack(data[:HEADER_LEN])
    if type not in _TYPES:
        raise ProtocolError(f"unknown frame type {type!r}")
    return Frame(stream_id=stream_id, type=type, payload=data[HEADER_LEN:])


def open_frame(stream_id: int, host: str, port: int) -> bytes:
    return encode(stream_id, OPEN, f"{host}:{port}".encode("utf-8"))


def parse_open(payload: bytes) -> tuple[str, int]:
    """Parse an OPEN payload 'host:port' -> (host, port). Raises ProtocolError."""
    try:
        text = payload.decode("utf-8")
        host, _, port_s = text.rpartition(":")
        if not host or not port_s:
            raise ValueError("expected host:port")
        port = int(port_s)
        if not (1 <= port <= 65535):
            raise ValueError(f"port out of range: {port}")
        return host, port
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError(f"bad OPEN payload: {exc}") from exc
