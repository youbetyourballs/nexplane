# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.pulumi.com/api",
        headers={"Authorization": f"token {creds['api_token']}", "Accept": "application/vnd.pulumi+8", "Content-Type": "application/json"},
        timeout=30.0,
    )
