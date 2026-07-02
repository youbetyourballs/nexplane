# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

from app.connectors.executors.common.tunnel_http import tunnel_http_client


def okta_headers(creds: dict) -> dict:
    return {
        "Authorization": f"SSWS {creds['api_token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def okta_base(creds: dict) -> str:
    return creds["org_url"].rstrip("/") + "/api/v1"


async def get_client(connector) -> httpx.AsyncClient:
    """Return a routed httpx.AsyncClient for Okta API calls."""
    return await tunnel_http_client(connector)
