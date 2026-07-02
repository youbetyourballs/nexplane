# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

from app.connectors.executors.common.tunnel_http import tunnel_http_client

AUTH_URL = "https://auth.app.wiz.io/oauth/token"
GRAPHQL_URL = "https://api.us1.app.wiz.io/graphql"


async def get_access_token(creds: dict, connector=None) -> str:
    client_kwargs = {}
    if connector is not None:
        http = await tunnel_http_client(connector)
    else:
        http = httpx.AsyncClient()
    async with http as client:
        resp = await client.post(AUTH_URL, data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "audience": "wiz-api",
        })
        resp.raise_for_status()
        return resp.json()["access_token"]


async def graphql_query(token: str, query: str, variables: dict = None, connector=None) -> dict:
    if connector is not None:
        http = await tunnel_http_client(connector)
    else:
        http = httpx.AsyncClient()
    async with http as client:
        resp = await client.post(
            GRAPHQL_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query, "variables": variables or {}},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()
