# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Test that GitLabClient threads the connector through to tunnel_http_client.
Run with --noconftest."""
import asyncio
import types

from app.connectors.executors.gitlab import _client as gitlab_module
from app.connectors.executors.common import tunnel_http


def _via_agent_connector():
    return types.SimpleNamespace(
        network_path="via_agent:agent1",
        connector_type=types.SimpleNamespace(value="gitlab"),
        network_tls_skip_verify=False,
        credentials={"url": "https://gitlab.example.com", "token": "tok"},
    )


def test_gitlab_client_passes_connector_to_tunnel_http_client(monkeypatch):
    """get_gitlab_client must pass the connector into GitLabClient so that
    tunnel_http_client receives it (and can apply proxy routing)."""
    received = {}

    async def fake_tunnel_http_client(connector, **kwargs):
        received["connector"] = connector

        class _FakeCtx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *_):
                pass

        return _FakeCtx()

    monkeypatch.setattr(tunnel_http, "tunnel_http_client", fake_tunnel_http_client)
    # Also patch the name imported into the gitlab _client module
    monkeypatch.setattr(gitlab_module, "tunnel_http_client", fake_tunnel_http_client)

    connector = _via_agent_connector()
    client = gitlab_module.get_gitlab_client(connector)
    assert client is not None

    async def run():
        # Call _http() directly to verify routing without making real HTTP call
        await client._http()

    asyncio.run(run())
    assert received.get("connector") is connector
