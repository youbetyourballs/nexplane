# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters.get("user_id", "")
    can_use_console = parameters.get("can_use_console_password", True)
    can_use_api = parameters.get("can_use_api_keys", True)

    if not creds:
        return {"action": "enable_iam_user", "user_id": user_id, "mock": True}

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        import oci
        details = oci.identity.models.UpdateUserCapabilitiesDetails(
            can_use_console_password=can_use_console,
            can_use_api_keys=can_use_api,
            can_use_auth_tokens=True,
            can_use_smtp_credentials=True,
        )
        client.update_user_capabilities(user_id, details)

    await loop.run_in_executor(None, _call)
    return {
        "action": "enable_iam_user",
        "user_id": user_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.disable_iam_user import execute as disable
    return await disable(
        {"user_id": execution_result.get("user_id", parameters.get("user_id"))},
        [],
        connector,
    )
