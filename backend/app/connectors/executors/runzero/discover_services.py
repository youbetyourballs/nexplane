# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_services", "services": [
            {"asset_id": "mock-asset-1", "port": 22, "protocol": "tcp", "service": "ssh", "banner": "OpenSSH 8.x"}
        ], "count": 1}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.get("/org/services", params={"_oid": creds["org_id"]})
        resp.raise_for_status()
        services = resp.json()
    return {"action": "discover_services", "services": services[:500], "count": len(services)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
