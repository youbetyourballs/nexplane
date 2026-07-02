# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

from app.connectors.executors.common.tunnel_http import tunnel_http_client

CF_BASE = "https://api.cloudflare.com/client/v4"


def cf_headers(creds: dict) -> dict:
    return {"Authorization": f"Bearer {creds['api_token']}", "Content-Type": "application/json"}


async def cf_get(path: str, creds: dict, connector=None) -> dict:
    http = await tunnel_http_client(connector) if connector is not None else httpx.AsyncClient()
    async with http as client:
        resp = await client.get(f"{CF_BASE}{path}", headers=cf_headers(creds))
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare error: {data.get('errors')}")
        return data


async def cf_post(path: str, body: dict, creds: dict, connector=None) -> dict:
    http = await tunnel_http_client(connector) if connector is not None else httpx.AsyncClient()
    async with http as client:
        resp = await client.post(f"{CF_BASE}{path}", headers=cf_headers(creds), json=body)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare error: {data.get('errors')}")
        return data


async def cf_put(path: str, body: dict, creds: dict, connector=None) -> dict:
    http = await tunnel_http_client(connector) if connector is not None else httpx.AsyncClient()
    async with http as client:
        resp = await client.put(f"{CF_BASE}{path}", headers=cf_headers(creds), json=body)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Cloudflare error: {data.get('errors')}")
        return data


async def cf_delete(path: str, creds: dict, connector=None) -> dict:
    http = await tunnel_http_client(connector) if connector is not None else httpx.AsyncClient()
    async with http as client:
        resp = await client.delete(f"{CF_BASE}{path}", headers=cf_headers(creds))
        resp.raise_for_status()
        return resp.json()
