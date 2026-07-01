# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Shared httpx client factory that routes a connector's HTTP traffic through the
reverse tunnel (SOCKS5) when its network_path is via_agent."""
from __future__ import annotations

import httpx

from app.tunnel.routing import http_proxy


async def tunnel_http_client(connector, **kwargs) -> httpx.AsyncClient:
    """Return an httpx.AsyncClient configured for the connector's network path.

    When the connector's network_path is via_agent:<id>, the client is
    configured with a SOCKS5 proxy URL obtained from the tunnel manager.
    When network_tls_skip_verify is set on a routed connector, verify=False
    is also applied (unless the caller explicitly overrides it).
    """
    proxy = await http_proxy(connector)
    if proxy is not None:
        kwargs["proxy"] = proxy
        if getattr(connector, "network_tls_skip_verify", False):
            kwargs.setdefault("verify", False)
    return httpx.AsyncClient(**kwargs)
