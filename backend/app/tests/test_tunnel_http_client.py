# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Tests for the tunnel_http_client helper. Run with --noconftest."""
import asyncio
import types

import httpx
import pytest

from app.connectors.executors.common import tunnel_http


def _conn(network_path="direct", skip=False):
    return types.SimpleNamespace(
        network_path=network_path,
        connector_type=types.SimpleNamespace(value="gitlab"),
        network_tls_skip_verify=skip,
    )


# ---------------------------------------------------------------------------
# Helper: fake AsyncClient that captures the kwargs it was built with
# ---------------------------------------------------------------------------

class _FakeAsyncClient:
    def __init__(self, **kwargs):
        self.captured = kwargs

    async def aclose(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_direct_connector_has_no_proxy(monkeypatch):
    """Direct connector: tunnel_http_client must NOT set a proxy kwarg."""
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return _FakeAsyncClient(**kwargs)

    async def fake_proxy(_c):
        return None

    async def run():
        monkeypatch.setattr(tunnel_http, "http_proxy", fake_proxy)
        monkeypatch.setattr(httpx, "AsyncClient", fake_client)
        client = await tunnel_http.tunnel_http_client(_conn("direct"))
        await client.aclose()

    asyncio.run(run())
    assert "proxy" not in captured


def test_via_agent_connector_sets_proxy(monkeypatch):
    """via_agent connector: tunnel_http_client must pass the SOCKS URL as proxy."""
    captured = {}
    proxy_url = "socks5://a:t@127.0.0.1:1080"

    def fake_client(**kwargs):
        captured.update(kwargs)
        return _FakeAsyncClient(**kwargs)

    async def fake_proxy(_c):
        return proxy_url

    async def run():
        monkeypatch.setattr(tunnel_http, "http_proxy", fake_proxy)
        monkeypatch.setattr(httpx, "AsyncClient", fake_client)
        client = await tunnel_http.tunnel_http_client(_conn("via_agent:agent1"))
        await client.aclose()

    asyncio.run(run())
    assert captured.get("proxy") == proxy_url


def test_via_agent_tls_skip_verify(monkeypatch):
    """via_agent + tls_skip_verify: verify must be False."""
    captured = {}
    proxy_url = "socks5://a:t@127.0.0.1:1080"

    def fake_client(**kwargs):
        captured.update(kwargs)
        return _FakeAsyncClient(**kwargs)

    async def fake_proxy(_c):
        return proxy_url

    async def run():
        monkeypatch.setattr(tunnel_http, "http_proxy", fake_proxy)
        monkeypatch.setattr(httpx, "AsyncClient", fake_client)
        client = await tunnel_http.tunnel_http_client(_conn("via_agent:agent1", skip=True))
        await client.aclose()

    asyncio.run(run())
    assert captured.get("proxy") == proxy_url
    assert captured.get("verify") is False


def test_via_agent_caller_verify_not_overridden(monkeypatch):
    """Caller-supplied verify=True should NOT be clobbered by tls_skip_verify=False."""
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return _FakeAsyncClient(**kwargs)

    async def fake_proxy(_c):
        return "socks5://a:t@127.0.0.1:1080"

    async def run():
        monkeypatch.setattr(tunnel_http, "http_proxy", fake_proxy)
        monkeypatch.setattr(httpx, "AsyncClient", fake_client)
        # skip=False but caller passes verify=True explicitly
        client = await tunnel_http.tunnel_http_client(_conn("via_agent:agent1", skip=False), verify=True)
        await client.aclose()

    asyncio.run(run())
    # verify stays True because skip=False means we don't setdefault anything
    assert captured.get("verify") is True
