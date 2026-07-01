# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx

BASE_URL = "https://console.runzero.com/api/v1.0"


def get_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {creds['api_token']}"},
        timeout=30.0,
    )
