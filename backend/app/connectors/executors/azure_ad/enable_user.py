# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from .azure_ad_client import get_azure_ad_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user = parameters.get("user_identifier") or parameters.get("user", "")
    if not user:
        raise ValueError("user_identifier is required")
    client = get_azure_ad_client(connector)
    if not client:
        return {"action": "azure_ad_enable_user", "user": user, "status": "skipped",
                "reason": "no_azure_ad_credentials"}
    result = await client.enable_user(user)
    return {**result, "action": "azure_ad_enable_user"}
