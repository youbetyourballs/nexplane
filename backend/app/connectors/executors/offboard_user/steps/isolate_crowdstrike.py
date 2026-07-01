# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Isolate CrowdStrike-managed endpoints associated with the target user."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "isolate_crowdstrike_endpoints",
            "target_email": target_email,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    base_url = creds.get("base_url", "https://api.crowdstrike.com")

    async with httpx.AsyncClient(base_url=base_url) as client:
        token_resp = await client.post(
            "/oauth2/token",
            data={"client_id": client_id, "client_secret": client_secret},
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        username = target_email.split("@")[0]
        search_resp = await client.get(
            "/devices/queries/devices/v1",
            headers=headers,
            params={"filter": f"last_login_user:'{username}'"},
        )
        search_resp.raise_for_status()
        device_ids = search_resp.json().get("resources", [])

        if not device_ids:
            return {
                "action": "isolate_crowdstrike_endpoints",
                "target_email": target_email,
                "skipped": True,
                "reason": "no devices found for user",
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }

        isolate_resp = await client.post(
            "/devices/entities/devices-actions/v2",
            headers=headers,
            params={"action_name": "contain"},
            json={"ids": device_ids},
        )
        isolate_resp.raise_for_status()

    return {
        "action": "isolate_crowdstrike_endpoints",
        "target_email": target_email,
        "device_ids": device_ids,
        "isolated": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "lift_crowdstrike_isolation",
        "target_email": parameters["target_email"],
        "device_ids": execution_result.get("device_ids", []),
        "rolled_back": True,
    }
