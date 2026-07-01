# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx
import base64


def get_client(creds: dict) -> httpx.AsyncClient:
    auth = base64.b64encode(f"{creds['email']}:{creds['api_token']}".encode()).decode()
    base = creds["base_url"].rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{base}/rest/api/3",
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json", "Content-Type": "application/json"},
        timeout=30.0,
    )
