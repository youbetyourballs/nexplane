# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "inventory sync cannot be reversed — re-sync will re-reflect current state"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    inventory_id = parameters["inventory_id"]
    if not creds:
        return {"action": "sync_inventory", "inventory_id": inventory_id, "synced": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post(f"/inventories/{inventory_id}/update_inventory_sources/")
        resp.raise_for_status()
    return {"action": "sync_inventory", "inventory_id": inventory_id, "synced": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "inventory sync cannot be reversed"}
