# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Routing unit tests for redis and mongo clients.

Run with --noconftest to avoid unrelated import issues.
"""
import asyncio
import types

from app.connectors.executors.redis import _client as rc
from app.connectors.executors.mongodb import _client as mc


def _make_connector(network_path: str, skip_verify: bool = False,
                    creds: dict | None = None):
    return types.SimpleNamespace(
        network_path=network_path,
        network_tls_skip_verify=skip_verify,
        credentials=creds or {},
    )


# ---------------------------------------------------------------------------
# Redis routing tests
# ---------------------------------------------------------------------------

def test_redis_via_agent_routes_host_port(monkeypatch):
    """When routed, RedisClient.host/port should target the forwarder."""
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 63790)

        monkeypatch.setattr(rc, "tcp_endpoint", fake_ep)

        connector = _make_connector(
            "via_agent:a",
            creds={"host": "cache.internal", "port": 6379, "password": "s3cr3t"},
        )
        client = await rc.get_redis_client(connector)
        assert client is not None
        assert client.host == "127.0.0.1"
        assert client.port == 63790

    asyncio.run(run())


def test_redis_direct_unchanged(monkeypatch):
    """Direct connector should keep original host/port."""
    async def run():
        async def passthrough(connector, h, p):
            return (h, p)

        monkeypatch.setattr(rc, "tcp_endpoint", passthrough)

        connector = _make_connector(
            "direct",
            creds={"host": "cache.internal", "port": 6379},
        )
        client = await rc.get_redis_client(connector)
        assert client is not None
        assert client.host == "cache.internal"
        assert client.port == 6379

    asyncio.run(run())


def test_redis_via_agent_ssl_skip_verify(monkeypatch):
    """When routed with SSL + skip_verify, ssl_check_hostname should be False."""
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 63790)

        monkeypatch.setattr(rc, "tcp_endpoint", fake_ep)

        connector = _make_connector(
            "via_agent:a", skip_verify=True,
            creds={"host": "cache.internal", "port": 6379, "ssl": True},
        )
        client = await rc.get_redis_client(connector)
        assert client is not None
        assert client.ssl is True
        assert client.ssl_check_hostname is False
        assert client.ssl_cert_reqs == "none"

    asyncio.run(run())


def test_redis_via_agent_ssl_no_skip_verify(monkeypatch):
    """When routed with SSL but skip_verify=False, ssl_check_hostname stays True."""
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 63790)

        monkeypatch.setattr(rc, "tcp_endpoint", fake_ep)

        connector = _make_connector(
            "via_agent:a", skip_verify=False,
            creds={"host": "cache.internal", "port": 6379, "ssl": True},
        )
        client = await rc.get_redis_client(connector)
        assert client is not None
        assert client.ssl_check_hostname is True

    asyncio.run(run())


def test_redis_no_host_returns_none(monkeypatch):
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 63790)

        monkeypatch.setattr(rc, "tcp_endpoint", fake_ep)
        connector = _make_connector("via_agent:a", creds={})
        client = await rc.get_redis_client(connector)
        assert client is None

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Mongo routing tests
# ---------------------------------------------------------------------------

def test_mongo_via_agent_routes_uri(monkeypatch):
    """When routed, the MongoClient URI should use the forwarder host:port."""
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 27018)

        monkeypatch.setattr(mc, "tcp_endpoint", fake_ep)

        connector = _make_connector(
            "via_agent:a",
            creds={
                "host": "m.internal", "port": 27017,
                "user": "admin", "password": "pass",
                "auth_db": "admin",
            },
        )
        client = await mc.get_mongo_client(connector)
        assert client is not None
        assert "127.0.0.1:27018" in client.uri
        # Original host should not appear
        assert "m.internal" not in client.uri

    asyncio.run(run())


def test_mongo_direct_unchanged(monkeypatch):
    """Direct connector should keep original host:port in URI."""
    async def run():
        async def passthrough(connector, h, p):
            return (h, p)

        monkeypatch.setattr(mc, "tcp_endpoint", passthrough)

        connector = _make_connector(
            "direct",
            creds={
                "host": "m.internal", "port": 27017,
                "user": "admin", "password": "pass",
            },
        )
        client = await mc.get_mongo_client(connector)
        assert client is not None
        assert "m.internal:27017" in client.uri

    asyncio.run(run())


def test_mongo_via_agent_tls_skip_verify(monkeypatch):
    """When routed with TLS + skip_verify, tlsAllowInvalidHostnames should be set."""
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 27018)

        monkeypatch.setattr(mc, "tcp_endpoint", fake_ep)

        connector = _make_connector(
            "via_agent:a", skip_verify=True,
            creds={
                "host": "m.internal", "port": 27017,
                "user": "admin", "password": "pass",
                "tls": True,
            },
        )
        client = await mc.get_mongo_client(connector)
        assert client is not None
        assert client.tls_allow_invalid_hostnames is True

    asyncio.run(run())


def test_mongo_via_agent_tls_no_skip_verify(monkeypatch):
    """When routed with TLS but skip_verify=False, tlsAllowInvalidHostnames stays False."""
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 27018)

        monkeypatch.setattr(mc, "tcp_endpoint", fake_ep)

        connector = _make_connector(
            "via_agent:a", skip_verify=False,
            creds={
                "host": "m.internal", "port": 27017,
                "user": "admin", "password": "pass",
                "tls": True,
            },
        )
        client = await mc.get_mongo_client(connector)
        assert client is not None
        assert client.tls_allow_invalid_hostnames is False

    asyncio.run(run())


def test_mongo_no_host_returns_none(monkeypatch):
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 27018)

        monkeypatch.setattr(mc, "tcp_endpoint", fake_ep)
        connector = _make_connector("via_agent:a", creds={})
        client = await mc.get_mongo_client(connector)
        assert client is None

    asyncio.run(run())
