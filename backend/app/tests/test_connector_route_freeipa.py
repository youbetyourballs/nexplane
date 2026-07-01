# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Routing unit tests for the FreeIPA HTTP client.

Run with --noconftest to avoid unrelated import issues.
"""
import asyncio
import types

from app.connectors.executors.freeipa import _client as fc


def _make_connector(network_path: str, creds: dict | None = None):
    return types.SimpleNamespace(
        network_path=network_path,
        network_tls_skip_verify=False,
        credentials=creds or {},
    )


class _FakeAsyncClient:
    """Minimal async-context httpx.AsyncClient stand-in."""
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        raise AssertionError("network should not be hit in this test")


def test_freeipa_via_agent_passes_connector_to_helper(monkeypatch):
    """A via_agent FreeIPAClient should route httpx through tunnel_http_client
    with its own connector."""
    async def run():
        captured = {}

        async def fake_tunnel_http_client(connector, **kwargs):
            captured["connector"] = connector
            captured["kwargs"] = kwargs
            return _FakeAsyncClient(**kwargs)

        monkeypatch.setattr(fc, "tunnel_http_client", fake_tunnel_http_client)

        connector = _make_connector(
            "via_agent:a",
            creds={"url": "https://ipa.internal", "username": "admin",
                   "password": "pw"},
        )
        client = fc.get_freeipa_client(connector)
        assert client is not None
        assert client._connector is connector

        # Trigger the login path which builds the http client.
        try:
            await client._login()
        except AssertionError:
            pass  # _FakeAsyncClient.post raises deliberately

        assert captured["connector"] is connector
        assert captured["kwargs"].get("verify") is False

    asyncio.run(run())


def test_freeipa_direct_still_builds_client(monkeypatch):
    """A direct connector still passes its connector to the helper."""
    async def run():
        captured = {}

        async def fake_tunnel_http_client(connector, **kwargs):
            captured["connector"] = connector
            return _FakeAsyncClient(**kwargs)

        monkeypatch.setattr(fc, "tunnel_http_client", fake_tunnel_http_client)

        connector = _make_connector(
            "direct",
            creds={"url": "https://ipa.internal", "username": "admin",
                   "password": "pw"},
        )
        client = fc.get_freeipa_client(connector)
        assert client is not None
        try:
            await client._login()
        except AssertionError:
            pass
        assert captured["connector"] is connector

    asyncio.run(run())
