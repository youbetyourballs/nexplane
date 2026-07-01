# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""WinRM connector routing: session target via the agent tunnel.
Run with --noconftest."""
import asyncio
import sys
import types

# pywinrm is an optional runtime dependency and is not installed in the test
# environment. Install a minimal stub so winrm._client (which imports winrm at
# module load) can be imported. We only introspect WinRMClient attributes, so
# no real winrm.Session is constructed.
if "winrm" not in sys.modules:
    _winrm = types.ModuleType("winrm")
    _winrm.Session = object
    sys.modules["winrm"] = _winrm

from app.connectors.executors.winrm import _client


def _connector(network_path, *, skip=False, use_ssl="false"):
    creds = {
        "hostname": "win.internal",
        "port": 5986,
        "username": "admin",
        "password": "secret",
        "use_ssl": use_ssl,
        "verify_ssl": "true",
    }
    return types.SimpleNamespace(
        network_path=network_path,
        connector_type=types.SimpleNamespace(value="winrm"),
        network_tls_skip_verify=skip,
        credentials=creds,
    )


def test_via_agent_targets_forwarder(monkeypatch):
    """A via_agent connector's WinRM session points at the forwarder host:port."""
    async def run():
        async def fake_ep(_c, host, port):
            return ("127.0.0.1", 55986)

        monkeypatch.setattr(_client, "tcp_endpoint", fake_ep)
        client = await _client.prepare_winrm_client(_connector("via_agent:a"))
        assert client._hostname == "127.0.0.1"
        assert client._port == 55986

    asyncio.run(run())


def test_via_agent_skip_verify_disables_cert_validation(monkeypatch):
    """With network_tls_skip_verify + routing, cert validation is disabled since
    the real cert can't validate against the localhost forwarder."""
    async def run():
        async def fake_ep(_c, host, port):
            return ("127.0.0.1", 55986)

        monkeypatch.setattr(_client, "tcp_endpoint", fake_ep)
        client = await _client.prepare_winrm_client(
            _connector("via_agent:a", skip=True, use_ssl="true")
        )
        assert client._hostname == "127.0.0.1"
        assert client._use_ssl is True
        assert client._verify_ssl is False  # downgraded because routed + skip

    asyncio.run(run())


def test_direct_unchanged(monkeypatch):
    """A direct connector keeps its real host/port and verify_ssl."""
    async def run():
        async def fake_ep(_c, host, port):
            return (host, port)

        monkeypatch.setattr(_client, "tcp_endpoint", fake_ep)
        client = await _client.prepare_winrm_client(
            _connector("direct", use_ssl="true")
        )
        assert client._hostname == "win.internal"
        assert client._port == 5986
        assert client._verify_ssl is True  # not routed: verification untouched

    asyncio.run(run())
