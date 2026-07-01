# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import httpx
from azure.identity import ClientSecretCredential

ARM_BASE = "https://management.azure.com"
API_VERSION = "2021-04-01"


def _get_token(creds: dict) -> str:
    credential = ClientSecretCredential(
        tenant_id=creds["tenant_id"],
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )
    token = credential.get_token("https://management.azure.com/.default")
    return token.token


def arm_get(creds: dict, path: str) -> dict:
    token = _get_token(creds)
    resp = httpx.get(
        f"{ARM_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params={"api-version": API_VERSION},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def arm_put(creds: dict, path: str, body: dict) -> httpx.Response:
    token = _get_token(creds)
    resp = httpx.put(
        f"{ARM_BASE}{path}",
        json=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        params={"api-version": API_VERSION},
        timeout=300,
    )
    resp.raise_for_status()
    return resp


def arm_delete(creds: dict, path: str) -> httpx.Response:
    token = _get_token(creds)
    resp = httpx.delete(
        f"{ARM_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params={"api-version": API_VERSION},
        timeout=120,
    )
    if resp.status_code == 404:
        return resp  # already gone
    resp.raise_for_status()
    return resp
