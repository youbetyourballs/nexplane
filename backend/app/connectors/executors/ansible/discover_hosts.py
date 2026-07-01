# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_hosts", "hosts": [{"id": 1, "name": "mock-host", "enabled": True}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/hosts/", params={"page_size": 100})
        resp.raise_for_status()
        hosts = [{"id": h["id"], "name": h["name"], "enabled": h.get("enabled"), "inventory_id": h.get("inventory")} for h in resp.json().get("results", [])]
    return {"action": "discover_hosts", "hosts": hosts, "count": len(hosts)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
