# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

from app.connectors.executors.common.tunnel_http import tunnel_http_client

GD_BASE = "https://api.godaddy.com"


def gd_headers(creds: dict) -> dict:
    token = creds.get("personal_access_token") or creds.get("api_token", "")
    headers = {
        "Authorization": f"sso-key {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    customer_id = creds.get("customer_id", "")
    if customer_id:
        headers["X-Shopper-Id"] = str(customer_id)
    return headers


async def gd_get(path: str, creds: dict, connector=None) -> dict | list:
    http = await tunnel_http_client(connector) if connector is not None else httpx.AsyncClient()
    async with http as client:
        resp = await client.get(f"{GD_BASE}{path}", headers=gd_headers(creds))
        resp.raise_for_status()
        return resp.json()


async def gd_put(path: str, body: list | dict, creds: dict, connector=None) -> None:
    http = await tunnel_http_client(connector) if connector is not None else httpx.AsyncClient()
    async with http as client:
        resp = await client.put(f"{GD_BASE}{path}", headers=gd_headers(creds), json=body)
        resp.raise_for_status()


async def gd_patch(path: str, body: dict, creds: dict, connector=None) -> None:
    http = await tunnel_http_client(connector) if connector is not None else httpx.AsyncClient()
    async with http as client:
        resp = await client.patch(f"{GD_BASE}{path}", headers=gd_headers(creds), json=body)
        resp.raise_for_status()


async def gd_delete(path: str, creds: dict, connector=None) -> None:
    http = await tunnel_http_client(connector) if connector is not None else httpx.AsyncClient()
    async with http as client:
        resp = await client.delete(f"{GD_BASE}{path}", headers=gd_headers(creds))
        resp.raise_for_status()
