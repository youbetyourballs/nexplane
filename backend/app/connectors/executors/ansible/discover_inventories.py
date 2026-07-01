# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_inventories", "inventories": [{"id": 1, "name": "Production", "total_hosts": 10}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/inventories/", params={"page_size": 100})
        resp.raise_for_status()
        inventories = [{"id": i["id"], "name": i["name"], "total_hosts": i.get("total_hosts", 0)} for i in resp.json().get("results", [])]
    return {"action": "discover_inventories", "inventories": inventories, "count": len(inventories)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
