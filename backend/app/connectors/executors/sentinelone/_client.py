# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

from app.connectors.executors.common.tunnel_http import tunnel_http_client


async def get_client(connector) -> httpx.AsyncClient:
    creds = getattr(connector, "credentials", {}) or {}
    url = creds["management_url"].rstrip("/")
    return await tunnel_http_client(
        connector,
        base_url=f"{url}/web/api/v2.1",
        headers={"Authorization": f"ApiToken {creds['api_token']}", "Content-Type": "application/json"},
        timeout=30.0,
    )
