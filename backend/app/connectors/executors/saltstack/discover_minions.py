# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_minions", "minions": [
            {"id": "minion-001", "os": "CentOS 7", "ip": "10.0.0.5", "kernel": "3.10.0"}
        ], "count": 1}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.post("/", json=[{"client": "local", "tgt": "*", "fun": "grains.items"}])
        resp.raise_for_status()
        data = resp.json().get("return", [{}])[0]
        minions = [{"id": mid, "os": grains.get("os", ""), "ip": grains.get("ipv4", [""])[0], "kernel": grains.get("kernelrelease", "")} for mid, grains in data.items()]
    return {"action": "discover_minions", "minions": minions, "count": len(minions)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
