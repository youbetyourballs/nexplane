# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    url = creds["management_url"].rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{url}/web/api/v2.1",
        headers={"Authorization": f"ApiToken {creds['api_token']}", "Content-Type": "application/json"},
        timeout=30.0,
    )
