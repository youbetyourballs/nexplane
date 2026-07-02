# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    event_id = parameters["event_id"]
    if not creds:
        return {"action": "suppress_notable", "event_id": event_id, "suppressed": True}
    from ._client import get_rest_client
    data = {"ruleUIDs[]": event_id, "status": "5", "comment": parameters.get("comment", "Suppressed by Nexplane")}
    async with await get_rest_client(connector) as client:
        resp = await client.post("/services/notable_update", data=data)
        resp.raise_for_status()
    return {"action": "suppress_notable", "event_id": event_id, "suppressed": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot un-suppress notable events automatically"}
