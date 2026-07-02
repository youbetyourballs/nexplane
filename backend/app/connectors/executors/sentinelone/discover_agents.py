# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_agents", "agents": [
            {"id": "mock-agent-1", "hostname": "mock-server", "os": "Linux", "version": "22.1.0", "status": "online", "threat_count": 0}
        ], "count": 1}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.get("/agents", params={"limit": 1000})
        resp.raise_for_status()
        agents = [{"id": a["id"], "hostname": a.get("computerName"), "os": a.get("osName"), "version": a.get("agentVersion"), "status": a.get("isActive")} for a in resp.json().get("data", [])]
    return {"action": "discover_agents", "agents": agents, "count": len(agents)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
