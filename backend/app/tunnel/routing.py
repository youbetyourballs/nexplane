# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
Connector network-path routing facade.

A connector's `network_path` is "direct" or "via_agent:<agent_id>". This module
turns that into either an httpx SOCKS proxy URL (HTTP connectors) or a localhost
TCP endpoint (raw-TCP connectors), both riding the reverse tunnel. It also gates
routing on the agent actually being connected.
"""
from __future__ import annotations

from .manager import get_manager
from .forwarder import get_forwarder
from .connector_proxy import get_connector_proxy

_VIA = "via_agent:"

# Connector types whose outbound client we route through the tunnel this sprint.
ROUTABLE_CONNECTOR_TYPES: set[str] = {
    # raw TCP
    "postgres", "redis", "mongodb", "ssh", "winrm",
    "ldap", "active_directory", "freeipa",
    # on-prem HTTP
    "gitlab", "gitea", "keycloak", "nessus", "openvas", "wazuh",
    "elastic", "opnsense", "teleport", "infisical",
}


class ConnectorRoutingError(Exception):
    pass


def agent_id_for(connector) -> str | None:
    np = getattr(connector, "network_path", "direct") or "direct"
    return np[len(_VIA):] if np.startswith(_VIA) else None


def guard_online(connector) -> None:
    agent_id = agent_id_for(connector)
    if agent_id is None:
        return
    if not get_manager().is_online(agent_id):
        ctype = getattr(getattr(connector, "connector_type", None), "value", "connector")
        raise ConnectorRoutingError(f"agent {agent_id} is not connected; cannot route {ctype}")


async def http_proxy(connector) -> str | None:
    agent_id = agent_id_for(connector)
    if agent_id is None:
        return None
    guard_online(connector)
    return await get_connector_proxy().proxy_url(agent_id)


async def tcp_endpoint(connector, host: str, port: int) -> tuple[str, int]:
    agent_id = agent_id_for(connector)
    if agent_id is None:
        return host, port
    guard_online(connector)
    return await get_forwarder().endpoint_for(agent_id, host, port)
