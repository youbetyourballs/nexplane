# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters.get("user_id", "")

    if not creds:
        return {
            "action": "disable_iam_user",
            "user_id": user_id,
            "previous_can_use_console_password": True,
            "previous_can_use_api_keys": True,
            "mock": True,
        }

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        import oci
        user = client.get_user(user_id).data
        caps = user.capabilities
        prev_console = caps.can_use_console_password if caps else True
        prev_api = caps.can_use_api_keys if caps else True
        details = oci.identity.models.UpdateUserCapabilitiesDetails(
            can_use_console_password=False,
            can_use_api_keys=False,
            can_use_auth_tokens=False,
            can_use_smtp_credentials=False,
        )
        client.update_user_capabilities(user_id, details)
        return prev_console, prev_api

    prev_console, prev_api = await loop.run_in_executor(None, _call)
    return {
        "action": "disable_iam_user",
        "user_id": user_id,
        "previous_can_use_console_password": prev_console,
        "previous_can_use_api_keys": prev_api,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.enable_iam_user import execute as enable
    return await enable(
        {
            "user_id": execution_result.get("user_id", parameters.get("user_id")),
            "can_use_console_password": execution_result.get("previous_can_use_console_password", True),
            "can_use_api_keys": execution_result.get("previous_can_use_api_keys", True),
        },
        [],
        connector,
    )
