# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

from app.connectors.executors.common.tunnel_http import tunnel_http_client


async def get_client(connector) -> httpx.AsyncClient:
    creds = getattr(connector, "credentials", {}) or {}
    site = creds.get("site", "datadoghq.com")
    return await tunnel_http_client(
        connector,
        base_url=f"https://api.{site}/api/v2",
        headers={"DD-API-KEY": creds["api_key"], "DD-APPLICATION-KEY": creds["app_key"], "Content-Type": "application/json"},
        timeout=30.0,
    )


async def get_v1_client(connector) -> httpx.AsyncClient:
    creds = getattr(connector, "credentials", {}) or {}
    site = creds.get("site", "datadoghq.com")
    return await tunnel_http_client(
        connector,
        base_url=f"https://api.{site}/api/v1",
        headers={"DD-API-KEY": creds["api_key"], "DD-APPLICATION-KEY": creds["app_key"]},
        timeout=30.0,
    )
