# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Routing unit tests for ldap and active_directory (ldap3) clients.

Run with --noconftest to avoid unrelated import issues.
"""
import asyncio
import ssl
import types

from app.connectors.executors.ldap import _client as lc
from app.connectors.executors.active_directory import _client as ac


def _make_connector(network_path: str, skip_verify: bool = False,
                    creds: dict | None = None):
    return types.SimpleNamespace(
        network_path=network_path,
        network_tls_skip_verify=skip_verify,
        credentials=creds or {},
    )


# ---------------------------------------------------------------------------
# LDAP routing tests
# ---------------------------------------------------------------------------

def test_ldap_via_agent_routes_host_port(monkeypatch):
    """When routed, LDAPClient.host/port should target the forwarder."""
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 3389)

        monkeypatch.setattr(lc, "tcp_endpoint", fake_ep)

        connector = _make_connector(
            "via_agent:a",
            creds={"host": "dc.internal", "port": 389, "bind_dn": "cn=admin",
                   "bind_password": "pw", "base_dn": "dc=example,dc=com"},
        )
        client = await lc.get_ldap_client(connector)
        assert client is not None
        assert client.host == "127.0.0.1"
        assert client.port == 3389

    asyncio.run(run())


def test_ldap_direct_unchanged(monkeypatch):
    """Direct connector should keep original host/port."""
    async def run():
        async def passthrough(connector, h, p):
            return (h, p)

        monkeypatch.setattr(lc, "tcp_endpoint", passthrough)

        connector = _make_connector(
            "direct",
            creds={"host": "dc.internal", "port": 389},
        )
        client = await lc.get_ldap_client(connector)
        assert client is not None
        assert client.host == "dc.internal"
        assert client.port == 389
        assert client.tls is None

    asyncio.run(run())


def test_ldap_via_agent_ssl_skip_verify_sets_cert_none(monkeypatch):
    """Routed LDAPS + skip_verify should build a Tls(validate=CERT_NONE)."""
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 6636)

        monkeypatch.setattr(lc, "tcp_endpoint", fake_ep)

        connector = _make_connector(
            "via_agent:a", skip_verify=True,
            creds={"host": "dc.internal", "port": 636, "use_ssl": True},
        )
        client = await lc.get_ldap_client(connector)
        assert client is not None
        assert client.use_ssl is True
        assert client.tls is not None
        assert client.tls.validate == ssl.CERT_NONE

    asyncio.run(run())


def test_ldap_via_agent_ssl_no_skip_verify(monkeypatch):
    """Routed LDAPS without skip_verify should not force CERT_NONE."""
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 6636)

        monkeypatch.setattr(lc, "tcp_endpoint", fake_ep)

        connector = _make_connector(
            "via_agent:a", skip_verify=False,
            creds={"host": "dc.internal", "port": 636, "use_ssl": True},
        )
        client = await lc.get_ldap_client(connector)
        assert client is not None
        assert client.tls is None

    asyncio.run(run())


def test_ldap_no_host_returns_none(monkeypatch):
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 3389)

        monkeypatch.setattr(lc, "tcp_endpoint", fake_ep)
        connector = _make_connector("via_agent:a", creds={})
        client = await lc.get_ldap_client(connector)
        assert client is None

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Active Directory prepare_ad_target routing tests
# ---------------------------------------------------------------------------

def test_ad_prepare_target_via_agent_stamps_forward(monkeypatch):
    """Routed AD creds should get _forward_host/_forward_port stamped."""
    async def run():
        async def fake_ep(connector, h, p):
            return ("127.0.0.1", 3389)

        monkeypatch.setattr(ac, "tcp_endpoint", fake_ep)

        connector = _make_connector(
            "via_agent:a",
            creds={"server": "dc.internal", "port": 389, "bind_dn": "cn=admin",
                   "bind_password": "pw", "base_dn": "DC=corp,DC=local"},
        )
        creds = await ac.prepare_ad_target(connector, connector.credentials)
        assert creds["_forward_host"] == "127.0.0.1"
        assert creds["_forward_port"] == 3389
        # Original creds not mutated
        assert "_forward_host" not in connector.credentials

    asyncio.run(run())


def test_ad_prepare_target_direct_no_stamp(monkeypatch):
    """Direct AD creds should not be stamped."""
    async def run():
        async def passthrough(connector, h, p):
            return (h, p)

        monkeypatch.setattr(ac, "tcp_endpoint", passthrough)

        connector = _make_connector(
            "direct",
            creds={"server": "dc.internal", "port": 389},
        )
        creds = await ac.prepare_ad_target(connector, connector.credentials)
        assert "_forward_host" not in creds
        assert "_forward_port" not in creds

    asyncio.run(run())


def test_ad_get_connection_uses_forwarded_endpoint(monkeypatch):
    """get_connection must build the ldap3 Server against the stamped endpoint."""
    captured = {}

    class _FakeServer:
        def __init__(self, host, port=None, use_ssl=False, get_info=None, tls=None):
            captured["host"] = host
            captured["port"] = port
            captured["use_ssl"] = use_ssl
            captured["tls"] = tls

    class _FakeConnection:
        def __init__(self, server, user=None, password=None, auto_bind=True):
            captured["server"] = server

    monkeypatch.setattr(ac, "Server", _FakeServer)
    monkeypatch.setattr(ac, "Connection", _FakeConnection)

    creds = {
        "server": "dc.internal", "port": 389,
        "bind_dn": "cn=admin", "bind_password": "pw",
        "_forward_host": "127.0.0.1", "_forward_port": 3389,
    }
    ac.get_connection(creds)
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 3389


def test_ad_get_connection_ssl_skip_sets_cert_none(monkeypatch):
    """Routed LDAPS get_connection with _forward_validate should pass CERT_NONE Tls."""
    captured = {}

    class _FakeServer:
        def __init__(self, host, port=None, use_ssl=False, get_info=None, tls=None):
            captured["tls"] = tls
            captured["use_ssl"] = use_ssl

    class _FakeConnection:
        def __init__(self, server, user=None, password=None, auto_bind=True):
            pass

    monkeypatch.setattr(ac, "Server", _FakeServer)
    monkeypatch.setattr(ac, "Connection", _FakeConnection)

    creds = {
        "server": "dc.internal", "port": 636, "use_ssl": "true",
        "bind_dn": "cn=admin", "bind_password": "pw",
        "_forward_host": "127.0.0.1", "_forward_port": 6636,
        "_forward_skip_verify": True,
    }
    ac.get_connection(creds)
    assert captured["use_ssl"] is True
    assert captured["tls"] is not None
    assert captured["tls"].validate == ssl.CERT_NONE
