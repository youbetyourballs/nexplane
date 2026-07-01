# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Okta service account (user) password rotation connector action."""

import httpx
from typing import Any


async def update_okta_password(
    connector_config: Any,
    username: str,
    new_password: str,
) -> None:
    """
    Resets the Okta user password to new_password using the lifecycle
    reset_password endpoint. The new_password is injected via StepOutputs
    and is never logged.
    """
    headers = {
        "Authorization": f"SSWS {connector_config.api_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient() as client:
        # Fetch user ID by login/username
        resp = await client.get(
            f"{connector_config.domain}/api/v1/users/{username}",
            headers=headers,
        )
        resp.raise_for_status()
        user_id = resp.json()["id"]

        # Set new password
        pw_resp = await client.post(
            f"{connector_config.domain}/api/v1/users/{user_id}/lifecycle/reset_password",
            headers=headers,
            json={"newPassword": {"value": new_password}},
        )
        pw_resp.raise_for_status()
