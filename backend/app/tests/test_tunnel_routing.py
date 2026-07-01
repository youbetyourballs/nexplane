# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Run with --noconftest."""
import asyncio, types
import pytest
from app.tunnel import routing

def _conn(network_path="direct", ctype="postgres"):
    return types.SimpleNamespace(network_path=network_path, connector_type=types.SimpleNamespace(value=ctype), network_tls_skip_verify=False)

def test_agent_id_for():
    assert routing.agent_id_for(_conn("direct")) is None
    assert routing.agent_id_for(_conn("via_agent:abc")) == "abc"

def test_direct_passthrough():
    async def run():
        assert await routing.http_proxy(_conn("direct")) is None
        assert await routing.tcp_endpoint(_conn("direct"), "db", 5432) == ("db", 5432)
    asyncio.run(run())

def test_guard_online_raises_when_offline(monkeypatch):
    monkeypatch.setattr(routing, "get_manager", lambda: types.SimpleNamespace(is_online=lambda _id: False))
    with pytest.raises(routing.ConnectorRoutingError):
        routing.guard_online(_conn("via_agent:abc"))

def test_routable_set_has_families():
    wired = ["postgres", "redis", "mongodb", "ssh", "winrm", "ldap", "active_directory", "freeipa", "gitlab", "gitea", "keycloak"]
    for t in wired:
        assert t in routing.ROUTABLE_CONNECTOR_TYPES
    # Deferred/unwired types must NOT be in the set
    for t in ["nessus", "openvas", "wazuh", "elastic", "opnsense", "teleport", "infisical"]:
        assert t not in routing.ROUTABLE_CONNECTOR_TYPES
