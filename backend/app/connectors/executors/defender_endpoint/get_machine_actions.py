# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    if not creds:
        return {"action": "get_machine_actions", "machine_id": machine_id, "actions": [], "count": 0}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.get("/machineactions", params={"$filter": f"machineId eq '{machine_id}'"})
        resp.raise_for_status()
        actions = [{"id": a["id"], "type": a.get("type"), "status": a.get("status"), "created": a.get("creationDateTimeUtc")} for a in resp.json().get("value", [])]
    return {"action": "get_machine_actions", "machine_id": machine_id, "actions": actions, "count": len(actions)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "status check has no rollback"}
