# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Okta API token rotation connector action."""

import httpx
from datetime import datetime
from typing import Any


async def rotate_okta_api_key(connector_config: Any, key_name: str) -> dict:
    """
    Lists existing Okta API tokens, creates a new one named
    '<key_name>-rotated-YYYYMMDD', and returns the new token value and
    the old token's ID (for subsequent revocation after propagation).

    Returns:
        {"new_api_key": "<token>", "old_key_id": "<id or empty string>"}
    """
    headers = {
        "Authorization": f"SSWS {connector_config.api_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        keys_resp = await client.get(
            f"{connector_config.domain}/api/v1/api-tokens",
            headers=headers,
        )
        keys_resp.raise_for_status()
        old_token = next(
            (k for k in keys_resp.json() if k["name"] == key_name), None
        )
        old_id = old_token["id"] if old_token else ""

        create_resp = await client.post(
            f"{connector_config.domain}/api/v1/api-tokens",
            headers=headers,
            json={"name": f"{key_name}-rotated-{datetime.utcnow():%Y%m%d}"},
        )
        create_resp.raise_for_status()
        new_key = create_resp.json()["token"]

    return {"new_api_key": new_key, "old_key_id": old_id}


async def revoke_okta_api_key(connector_config: Any, key_id: str) -> None:
    """Revokes the Okta API token with the given ID."""
    headers = {"Authorization": f"SSWS {connector_config.api_token}"}
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"{connector_config.domain}/api/v1/api-tokens/{key_id}",
            headers=headers,
        )
        resp.raise_for_status()
