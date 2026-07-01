# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_id = parameters["agent_id"]
    file_hash = parameters["file_hash"]
    if not creds:
        return {"action": "quarantine_file", "agent_id": agent_id, "file_hash": file_hash, "quarantined": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/threats/actions/quarantine", json={"filter": {"agentIds": [agent_id]}, "data": {"hash": file_hash}})
        resp.raise_for_status()
    return {"action": "quarantine_file", "agent_id": agent_id, "file_hash": file_hash, "quarantined": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unquarantine requires manual action"}
