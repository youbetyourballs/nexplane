# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

from app.connectors.executors.common.tunnel_http import tunnel_http_client

BASE_URL = "https://console.runzero.com/api/v1.0"


async def get_client(connector) -> httpx.AsyncClient:
    creds = getattr(connector, "credentials", {}) or {}
    return await tunnel_http_client(
        connector,
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {creds['api_token']}"},
        timeout=30.0,
    )
