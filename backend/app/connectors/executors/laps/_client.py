# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

GRAPH_BASE = "https://graph.microsoft.com/beta"


def get_token(creds: dict) -> str:
    url = f"https://login.microsoftonline.com/{creds['tenant_id']}/oauth2/v2.0/token"
    resp = httpx.post(url, data={
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "scope": "https://graph.microsoft.com/.default",
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def graph_get(token: str, path: str) -> dict:
    resp = httpx.get(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def graph_post(token: str, path: str, json: dict = None) -> dict:
    resp = httpx.post(
        f"{GRAPH_BASE}{path}",
        json=json or {},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}
