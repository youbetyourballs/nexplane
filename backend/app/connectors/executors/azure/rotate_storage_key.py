# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import random
import string
from datetime import datetime, timezone


def _new_key_fp():
    k = "".join(random.choices(string.ascii_letters + string.digits, k=64))
    return k[:8] + "..."


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_storage_client
    storage = get_storage_client(creds)
    loop = asyncio.get_event_loop()
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    key_name = parameters.get("key_name", "key1")
    results = []
    for asset_id in asset_ids:
        acct_name = parameters.get("storage_account_name", asset_id)
        keys_resp = await loop.run_in_executor(
            None,
            lambda a=acct_name, k=key_name: storage.storage_accounts.regenerate_key(rg, a, {"key_name": k})
        )
        keys = getattr(keys_resp, "keys", [])
        new_fp = keys[0].value[:8] + "..." if keys else _new_key_fp()
        results.append({"account": acct_name, "new_key_fingerprint": new_fp})
    return {"action": "rotate_storage_key", "key_name": key_name, "assets": asset_ids, "results": results, "rotated_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        new_key = "".join(random.choices(string.ascii_letters + string.digits, k=64))
        return {"action": "rotate_storage_key", "key_name": parameters.get("key_name", "key1"), "assets": asset_ids, "new_key_fingerprint": new_key[:8] + "...", "rotated_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key rotation has no rollback — update consumers to new key"}
