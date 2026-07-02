# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_id = parameters["agent_id"]
    if not creds:
        return {"action": "kill_process", "agent_id": agent_id, "process": parameters.get("process_name"), "killed": True}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.post("/agents/actions/kill-process", json={"filter": {"ids": [agent_id]}, "data": {"processName": parameters["process_name"]}})
        resp.raise_for_status()
    return {"action": "kill_process", "agent_id": agent_id, "process": parameters["process_name"], "killed": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "killed processes cannot be restarted automatically"}
