# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""SSH connector routing: forwarded socket via the agent tunnel.
Run with --noconftest."""
import asyncio
import socket
import sys
import types

# paramiko is an optional runtime dependency and is not installed in the test
# environment. Install a minimal stub so ssh._client (which imports paramiko at
# module load) can be imported; tests monkeypatch paramiko.SSHClient anyway.
if "paramiko" not in sys.modules:
    _paramiko = types.ModuleType("paramiko")
    _paramiko.SSHClient = object
    _paramiko.AutoAddPolicy = object

    class _RSAKey:
        @staticmethod
        def from_private_key(_fp):
            return object()

    _paramiko.RSAKey = _RSAKey
    sys.modules["paramiko"] = _paramiko

from app.connectors.executors.ssh import _client


def _connector(network_path, *, skip=False):
    return types.SimpleNamespace(
        network_path=network_path,
        connector_type=types.SimpleNamespace(value="ssh"),
        network_tls_skip_verify=skip,
    )


def _creds():
    return {
        "hostname": "box.internal",
        "port": 22,
        "username": "u",
        "password": "pw",
    }


def test_prepare_ssh_target_via_agent(monkeypatch):
    """A via_agent connector's creds get _forward_host/_forward_port pointing at
    the forwarder, while hostname is preserved for host-key checking."""
    async def run():
        async def fake_ep(_c, host, port):
            return ("127.0.0.1", 2222)

        monkeypatch.setattr(_client, "tcp_endpoint", fake_ep)
        creds = await _client.prepare_ssh_target(_connector("via_agent:a"), _creds())
        assert creds["hostname"] == "box.internal"   # host-key identity preserved
        assert creds["_forward_host"] == "127.0.0.1"
        assert creds["_forward_port"] == 2222

    asyncio.run(run())


def test_prepare_ssh_target_direct(monkeypatch):
    """A direct connector's creds get no forwarding annotations and the caller's
    dict is not mutated."""
    async def run():
        async def fake_ep(_c, host, port):
            return (host, port)

        monkeypatch.setattr(_client, "tcp_endpoint", fake_ep)
        original = _creds()
        creds = await _client.prepare_ssh_target(_connector("direct"), original)
        assert "_forward_host" not in creds
        assert "_forward_port" not in creds
        assert "_forward_host" not in original  # caller's dict untouched

    asyncio.run(run())


class _DummyClient:
    """Stand-in for paramiko.SSHClient that records connect kwargs."""

    def __init__(self):
        self.connect_kwargs = None

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, **kwargs):
        self.connect_kwargs = kwargs


def test_get_ssh_client_routed_uses_sock(monkeypatch):
    """With _forward_host set, get_ssh_client connects with a sock to the
    forwarder and the real hostname; port is not passed alongside sock."""
    dummy = _DummyClient()
    monkeypatch.setattr(_client.paramiko, "SSHClient", lambda: dummy)

    marker = object()
    captured = {}

    def fake_create_connection(addr, timeout=None):
        captured["addr"] = addr
        return marker

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    creds = _creds()
    creds["_forward_host"] = "127.0.0.1"
    creds["_forward_port"] = 2222

    client = _client.get_ssh_client(creds)
    assert client is dummy
    assert dummy.connect_kwargs["sock"] is marker
    assert dummy.connect_kwargs["hostname"] == "box.internal"  # real host preserved
    assert "port" not in dummy.connect_kwargs                  # no port with sock
    assert captured["addr"] == ("127.0.0.1", 2222)


def test_get_ssh_client_direct_no_sock(monkeypatch):
    """Without _forward_host, get_ssh_client connects normally with host+port and
    no sock."""
    dummy = _DummyClient()
    monkeypatch.setattr(_client.paramiko, "SSHClient", lambda: dummy)

    def boom(*a, **k):
        raise AssertionError("socket.create_connection should not be called")

    monkeypatch.setattr(socket, "create_connection", boom)

    client = _client.get_ssh_client(_creds())
    assert client is dummy
    assert "sock" not in dummy.connect_kwargs
    assert dummy.connect_kwargs["hostname"] == "box.internal"
    assert dummy.connect_kwargs["port"] == 22
