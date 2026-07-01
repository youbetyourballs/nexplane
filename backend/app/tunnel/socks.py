# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
SOCKS5 front-end for the reverse tunnel (Phase 2 consumption interface).

Any TCP client that speaks SOCKS5 (most HTTP/DB clients, connectors) can reach
an agent's network by pointing at this proxy — no per-connector code. The
**agent is selected by the SOCKS5 username** (= the agent registration id), and
each CONNECT is dialed through that agent's tunnel via the ``TunnelManager``
(which enforces the agent's allowlist). Bind this listener to localhost /
internal only; it's for trusted backend components.

SOCKS5: RFC 1928; username/password auth: RFC 1929.
"""
from __future__ import annotations

import asyncio
import ipaddress
import struct

from .manager import TunnelManager, TunnelStream, DestinationDenied, TunnelUnavailable, DialError

_VER = 0x05
_AUTH_USERPASS = 0x02
_CMD_CONNECT = 0x01
_ATYP_IPV4 = 0x01
_ATYP_DOMAIN = 0x03
_ATYP_IPV6 = 0x04

# CONNECT reply codes
_REP_OK = 0x00
_REP_GENERAL = 0x01
_REP_NOT_ALLOWED = 0x02
_REP_UNREACHABLE = 0x03
_REP_REFUSED = 0x05
_REP_CMD_UNSUP = 0x07
_REP_ATYP_UNSUP = 0x08


async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    data = await reader.readexactly(n)
    return data


async def _reply(writer: asyncio.StreamWriter, rep: int) -> None:
    # VER, REP, RSV, ATYP=IPv4, BND.ADDR=0.0.0.0, BND.PORT=0
    writer.write(bytes([_VER, rep, 0x00, _ATYP_IPV4, 0, 0, 0, 0, 0, 0]))
    await writer.drain()


async def handle_socks_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    manager: TunnelManager,
    *,
    auth_token: str | None = None,
) -> None:
    """Handle one SOCKS5 client. Username = agent_id; CONNECT dials via the tunnel."""
    try:
        # --- greeting ---
        ver, nmethods = struct.unpack("!BB", await _read_exact(reader, 2))
        if ver != _VER:
            writer.close(); return
        methods = await _read_exact(reader, nmethods)
        if _AUTH_USERPASS not in methods:
            writer.write(bytes([_VER, 0xFF])); await writer.drain(); writer.close(); return
        writer.write(bytes([_VER, _AUTH_USERPASS])); await writer.drain()

        # --- username/password auth (RFC 1929): username = agent_id ---
        (auth_ver,) = struct.unpack("!B", await _read_exact(reader, 1))
        (ulen,) = struct.unpack("!B", await _read_exact(reader, 1))
        username = (await _read_exact(reader, ulen)).decode("utf-8", "replace")
        (plen,) = struct.unpack("!B", await _read_exact(reader, 1))
        password = (await _read_exact(reader, plen)).decode("utf-8", "replace")
        if auth_token is not None and password != auth_token:
            writer.write(bytes([0x01, 0x01])); await writer.drain(); writer.close(); return
        writer.write(bytes([0x01, 0x00])); await writer.drain()  # auth success

        # --- CONNECT request ---
        ver, cmd, _rsv, atyp = struct.unpack("!BBBB", await _read_exact(reader, 4))
        if ver != _VER:
            writer.close(); return
        if cmd != _CMD_CONNECT:
            await _reply(writer, _REP_CMD_UNSUP); writer.close(); return
        if atyp == _ATYP_IPV4:
            host = str(ipaddress.IPv4Address(await _read_exact(reader, 4)))
        elif atyp == _ATYP_IPV6:
            host = str(ipaddress.IPv6Address(await _read_exact(reader, 16)))
        elif atyp == _ATYP_DOMAIN:
            (dlen,) = struct.unpack("!B", await _read_exact(reader, 1))
            host = (await _read_exact(reader, dlen)).decode("utf-8", "replace")
        else:
            await _reply(writer, _REP_ATYP_UNSUP); writer.close(); return
        (port,) = struct.unpack("!H", await _read_exact(reader, 2))

        # --- dial through the agent's tunnel ---
        try:
            stream = await manager.dial(username, host, port)
        except DestinationDenied:
            await _reply(writer, _REP_NOT_ALLOWED); writer.close(); return
        except TunnelUnavailable:
            await _reply(writer, _REP_UNREACHABLE); writer.close(); return
        except DialError:
            await _reply(writer, _REP_REFUSED); writer.close(); return
        except Exception:
            await _reply(writer, _REP_GENERAL); writer.close(); return

        await _reply(writer, _REP_OK)
        await _relay(reader, writer, stream)
    except (asyncio.IncompleteReadError, ConnectionError):
        try:
            writer.close()
        except Exception:
            pass


async def _relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, stream: TunnelStream) -> None:
    async def sock_to_stream():
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

    async def stream_to_sock():
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

    await asyncio.gather(sock_to_stream(), stream_to_sock(), return_exceptions=True)


class SocksServer:
    """asyncio SOCKS5 server routing CONNECTs through the tunnel manager."""

    def __init__(self, manager: TunnelManager, *, host: str = "127.0.0.1", port: int = 0,
                 auth_token: str | None = None):
        self._manager = manager
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> tuple[str, int]:
        self._server = await asyncio.start_server(
            lambda r, w: handle_socks_connection(r, w, self._manager, auth_token=self._auth_token),
            self._host, self._port,
        )
        sock = self._server.sockets[0]
        return sock.getsockname()[0], sock.getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
