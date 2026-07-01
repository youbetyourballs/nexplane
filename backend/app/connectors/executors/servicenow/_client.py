# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx
import base64


def get_client(creds: dict) -> httpx.AsyncClient:
    auth = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
    url = creds["instance_url"].rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{url}/api/now/v2/table",
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json", "Content-Type": "application/json"},
        timeout=30.0,
    )
