# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    prefix = parameters["secret_path_prefix"]
    if not creds:
        return {"action": "revoke_all_leases", "prefix": prefix, "revoked": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.sys.revoke_prefix(prefix=prefix))
    return {"action": "revoke_all_leases", "prefix": prefix, "revoked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "revoked leases cannot be restored"}
