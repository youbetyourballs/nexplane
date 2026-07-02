# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

from app.connectors.executors.common.tunnel_http import tunnel_http_client


async def get_client(connector) -> httpx.AsyncClient:
    creds = getattr(connector, "credentials", {}) or {}
    return await tunnel_http_client(
        connector,
        base_url="https://api.github.com",
        headers={"Authorization": f"Bearer {creds['token']}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        timeout=30.0,
    )
