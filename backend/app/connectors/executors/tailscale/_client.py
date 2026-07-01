# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

TAILSCALE_API_BASE = "https://api.tailscale.com/api/v2"


async def ts_post(path: str, body: dict, creds: dict) -> dict:
    """Make an authenticated POST to the Tailscale API using OAuth client credentials."""
    async with httpx.AsyncClient() as client:
        # Get OAuth token
        token_resp = await client.post(
            "https://api.tailscale.com/api/v2/oauth/token",
            data={
                "client_id": creds["oauth_client_id"],
                "client_secret": creds["oauth_client_secret"],
                "grant_type": "client_credentials",
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        resp = await client.post(
            f"{TAILSCALE_API_BASE}{path}",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


async def ts_delete(path: str, creds: dict) -> None:
    """Make an authenticated DELETE to the Tailscale API using OAuth client credentials."""
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://api.tailscale.com/api/v2/oauth/token",
            data={
                "client_id": creds["oauth_client_id"],
                "client_secret": creds["oauth_client_secret"],
                "grant_type": "client_credentials",
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]

        resp = await client.delete(
            f"{TAILSCALE_API_BASE}{path}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
