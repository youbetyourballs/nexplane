# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_leases", "leases": [], "count": 0}
    from ._client import VaultClient, get_vault_client
    loop = asyncio.get_event_loop()
    hvac_client = get_vault_client(creds)
    vault = VaultClient(hvac_client)
    try:
        leases = await loop.run_in_executor(None, vault.list_leases)
    except Exception:
        leases = []
    return {"action": "discover_leases", "leases": leases, "count": len(leases)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
