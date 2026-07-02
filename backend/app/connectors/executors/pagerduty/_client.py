# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

from app.connectors.executors.common.tunnel_http import tunnel_http_client


async def get_client(connector) -> httpx.AsyncClient:
    creds = getattr(connector, "credentials", {}) or {}
    return await tunnel_http_client(
        connector,
        base_url="https://api.pagerduty.com",
        headers={"Authorization": f"Token token={creds['api_key']}", "Accept": "application/vnd.pagerduty+json;version=2", "Content-Type": "application/json", "From": creds.get("from_email", "nexplane@nexplane.local")},
        timeout=30.0,
    )
