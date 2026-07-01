# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        headers={"Authorization": f"Bearer {creds['token']}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        timeout=30.0,
    )
