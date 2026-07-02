# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_services", "services": [{"id": "P0001", "name": "Production API", "status": "active"}], "count": 1}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.get("/services", params={"limit": 100})
        resp.raise_for_status()
        services = [{"id": s["id"], "name": s["name"], "status": s.get("status")} for s in resp.json().get("services", [])]
    return {"action": "discover_services", "services": services, "count": len(services)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
