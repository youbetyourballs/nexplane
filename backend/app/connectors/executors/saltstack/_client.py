# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx


async def get_token(creds: dict) -> str:
    url = creds["api_url"].rstrip("/")
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(f"{url}/login", json={"username": creds["username"], "password": creds["password"], "eauth": creds.get("eauth", "pam")})
        resp.raise_for_status()
        return resp.json()["return"][0]["token"]


def get_client(creds: dict, token: str) -> httpx.AsyncClient:
    url = creds["api_url"].rstrip("/")
    return httpx.AsyncClient(base_url=url, headers={"X-Auth-Token": token, "Accept": "application/json"}, timeout=60.0, verify=False)
