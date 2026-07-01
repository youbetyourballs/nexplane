# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone

import httpx

TAILSCALE_API_BASE = "https://api.tailscale.com/api/v2"


def _tailscale_auth(creds: dict) -> dict | None:
    """Return headers dict or None if no credentials."""
    api_key = creds.get("api_key") or creds.get("token")
    if not api_key:
        return None
    import base64
    token = base64.b64encode(f"{api_key}:".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", None) or {}
    device_id = parameters.get("device_id") or parameters.get("deviceId", "")
    headers = _tailscale_auth(creds)
    if not headers:
        return {
            "action": "expire_device_key",
            "device_id": device_id,
            "status": "skipped",
            "reason": "no_tailscale_credentials",
            "mock": True,
        }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{TAILSCALE_API_BASE}/devices/{device_id}/expire",
            headers=headers,
        )
        resp.raise_for_status()

    return {
        "action": "expire_device_key",
        "device_id": device_id,
        "status": "expired",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key expiry cannot be reversed; device must re-authenticate"}
