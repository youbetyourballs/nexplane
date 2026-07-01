# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    key = parameters["key"]
    if not creds:
        return {"action": "set_variable", "workspace_id": workspace_id, "key": key, "set": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/workspaces/{workspace_id}/vars")
        resp.raise_for_status()
        existing = {v["attributes"]["key"]: v["id"] for v in resp.json().get("data", [])}
        var_data = {"data": {"type": "vars", "attributes": {"key": key, "value": parameters["value"], "category": parameters.get("category", "terraform"), "sensitive": parameters.get("sensitive", False)}}}
        if key in existing:
            resp2 = await client.patch(f"/workspaces/{workspace_id}/vars/{existing[key]}", json=var_data)
        else:
            var_data["data"]["relationships"] = {"workspace": {"data": {"type": "workspaces", "id": workspace_id}}}
            resp2 = await client.post("/vars", json=var_data)
        resp2.raise_for_status()
    return {"action": "set_variable", "workspace_id": workspace_id, "key": key, "set": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous variable value not captured — restore manually"}
