# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import hashlib
import time
import httpx


def obfuscate_api_key(api_key: str) -> tuple:
    """Zscaler API key obfuscation as per their docs."""
    timestamp = str(int(time.time() * 1000))
    high = timestamp[-6:]
    low = str(int(high) >> 1)
    obf = ""
    for c in high:
        obf += api_key[int(c)]
    for c in low.zfill(6):
        obf += api_key[int(c) + 2]
    return obf, timestamp


async def get_session(creds: dict, connector=None) -> httpx.AsyncClient:
    from app.connectors.executors.common.tunnel_http import tunnel_http_client
    cloud = creds["cloud"]
    obf_key, ts = obfuscate_api_key(creds["api_key"])
    if connector is not None:
        client = await tunnel_http_client(
            connector,
            base_url=f"https://zsapi.{cloud}/api/v1",
            timeout=30.0,
        )
    else:
        client = httpx.AsyncClient(base_url=f"https://zsapi.{cloud}/api/v1", timeout=30.0)
    resp = await client.post("/authenticatedSession", json={
        "apiKey": obf_key,
        "username": creds["username"],
        "password": creds["password"],
        "timestamp": ts,
    })
    resp.raise_for_status()
    return client
