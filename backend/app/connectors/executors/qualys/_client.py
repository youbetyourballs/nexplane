# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx
import base64

from app.connectors.executors.common.tunnel_http import tunnel_http_client


async def get_client(connector) -> httpx.AsyncClient:
    creds = getattr(connector, "credentials", {}) or {}
    credentials = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
    return await tunnel_http_client(
        connector,
        base_url=creds["api_url"].rstrip("/"),
        headers={"Authorization": f"Basic {credentials}", "X-Requested-With": "Nexplane"},
        timeout=60.0,
    )
