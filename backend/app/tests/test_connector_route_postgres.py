# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Postgres connector routing: host/hostaddr split via the agent tunnel.
Run with --noconftest."""
import asyncio
import types

from app.connectors.executors.postgres import _client


def _connector(network_path, *, skip=False, sslmode="verify-full"):
    creds = {
        "host": "db.internal",
        "port": 5432,
        "dbname": "postgres",
        "user": "admin",
        "password": "secret",
        "sslmode": sslmode,
    }
    return types.SimpleNamespace(
        network_path=network_path,
        connector_type=types.SimpleNamespace(value="postgres"),
        network_tls_skip_verify=skip,
        credentials=creds,
    )


def test_via_agent_uses_hostaddr_split(monkeypatch):
    """A via_agent connector connects to the forwarder (hostaddr) while keeping
    the real hostname (host) so cert/SNI verification still validates."""
    async def run():
        async def fake_ep(_c, host, port):
            return ("127.0.0.1", 55432)

        monkeypatch.setattr(_client, "tcp_endpoint", fake_ep)
        client = await _client.get_postgres_client(_connector("via_agent:agent1"))
        assert client is not None
        assert client.host == "db.internal"      # SNI / cert verification target preserved
        assert client.hostaddr == "127.0.0.1"    # actual connect target = forwarder
        assert client.port == 55432
        assert client.sslmode == "verify-full"   # not skipping verify: keep verify-full

    asyncio.run(run())


def test_via_agent_skip_verify_downgrades_sslmode(monkeypatch):
    """With network_tls_skip_verify, a verify-* sslmode is downgraded to require
    (the real cert can't be validated when tunneled to a localhost forwarder)."""
    async def run():
        async def fake_ep(_c, host, port):
            return ("127.0.0.1", 55432)

        monkeypatch.setattr(_client, "tcp_endpoint", fake_ep)
        client = await _client.get_postgres_client(
            _connector("via_agent:agent1", skip=True, sslmode="verify-full")
        )
        assert client is not None
        assert client.host == "db.internal"
        assert client.hostaddr == "127.0.0.1"
        assert client.port == 55432
        assert client.sslmode == "require"       # downgraded from verify-full

    asyncio.run(run())


def test_direct_unchanged(monkeypatch):
    """A direct connector keeps its real host/port and never sets hostaddr."""
    async def run():
        async def fake_ep(_c, host, port):
            return (host, port)

        monkeypatch.setattr(_client, "tcp_endpoint", fake_ep)
        client = await _client.get_postgres_client(_connector("direct", sslmode="require"))
        assert client is not None
        assert client.host == "db.internal"
        assert client.port == 5432
        assert client.hostaddr is None
        assert client.sslmode == "require"

    asyncio.run(run())
