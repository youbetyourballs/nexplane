# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from .azure_ad_client import get_azure_ad_client

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    client = get_azure_ad_client(connector)
    if not client:
        return {"action": "azure_ad_discover_users", "status": "skipped", "reason": "no_azure_ad_credentials"}
    users = await client.list_users()
    return {
        "action": "azure_ad_discover_users",
        "users": users,
        "count": len(users),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
