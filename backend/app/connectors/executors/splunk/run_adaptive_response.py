# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    event_id = parameters["event_id"]
    action_name = parameters["action_name"]
    if not creds:
        return {"action": "run_adaptive_response", "event_id": event_id, "action_name": action_name, "triggered": True}
    from ._client import get_rest_client
    data = {"ruleUIDs[]": event_id, "action_name": action_name}
    async with get_rest_client(creds) as client:
        resp = await client.post("/services/notable_adaptive_response", data=data)
        resp.raise_for_status()
    return {"action": "run_adaptive_response", "event_id": event_id, "action_name": action_name, "triggered": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "adaptive response actions cannot be reversed"}
