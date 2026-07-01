# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
from datetime import datetime, timezone
from ._client import get_freeipa_client, FreeIPAClient


async def execute(parameters, asset_ids, connector):
    username = parameters.get("username") or parameters.get("user_identifier", "")
    client = get_freeipa_client(connector)
    if not client and parameters.get("freeipa_url"):
        client = FreeIPAClient(
            url=parameters["freeipa_url"],
            username=parameters.get("freeipa_username", "admin"),
            password=parameters.get("freeipa_password", ""),
            verify_ssl=False,
            connector=connector,
        )
    if not client:
        return {"action": "freeipa_disable_user", "status": "skipped",
                "reason": "no_freeipa_credentials", "username": username}
    result = await client.user_disable(username)
    return {
        **result,
        "action": "freeipa_disable_user",
        "disabled_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters, execution_result, connector):
    username = parameters.get("username") or parameters.get("user_identifier", "")
    client = get_freeipa_client(connector)
    if not client and parameters.get("freeipa_url"):
        client = FreeIPAClient(
            url=parameters["freeipa_url"],
            username=parameters.get("freeipa_username", "admin"),
            password=parameters.get("freeipa_password", ""),
            verify_ssl=False,
            connector=connector,
        )
    if not client:
        return {"rolled_back": False, "reason": "no_freeipa_credentials"}
    result = await client.user_enable(username)
    return {"rolled_back": result.get("success", False), **result}
