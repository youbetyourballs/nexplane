# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.pagerduty.com",
        headers={"Authorization": f"Token token={creds['api_key']}", "Accept": "application/vnd.pagerduty+json;version=2", "Content-Type": "application/json", "From": creds.get("from_email", "nexplane@nexplane.local")},
        timeout=30.0,
    )
