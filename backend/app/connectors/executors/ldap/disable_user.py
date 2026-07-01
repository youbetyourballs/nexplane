# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone
from ._client import get_ldap_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    username = parameters.get("username") or parameters.get("user_identifier", "")
    if not username:
        raise ValueError("username is required")
    client = await get_ldap_client(connector)
    if not client:
        return {"action": "ldap_disable_user", "status": "skipped",
                "reason": "no_ldap_credentials", "username": username,
                "_asset_ids": [str(a) for a in asset_ids]}
    result = await asyncio.to_thread(client.disable_user, username)
    return {
        "action": "ldap_disable_user", **result,
        "disabled_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    username = parameters.get("username") or parameters.get("user_identifier", "")
    client = await get_ldap_client(connector)
    if not client:
        return {"rolled_back": False, "reason": "no_ldap_credentials"}
    result = await asyncio.to_thread(client.enable_user, username)
    return {"rolled_back": result.get("success", False), **result}
