# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import msal
import httpx

API_BASE = "https://api.securitycenter.microsoft.com/api"


async def get_access_token(creds: dict) -> str:
    app = msal.ConfidentialClientApplication(
        creds["client_id"],
        authority=f"https://login.microsoftonline.com/{creds['tenant_id']}",
        client_credential=creds["client_secret"],
    )
    result = app.acquire_token_for_client(scopes=["https://api.securitycenter.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Token acquisition failed: {result.get('error_description')}")
    return result["access_token"]


def get_client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE, headers={"Authorization": f"Bearer {token}"}, timeout=30.0)
