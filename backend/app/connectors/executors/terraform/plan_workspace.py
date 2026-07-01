# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "plan_workspace", "workspace_id": workspace_id, "run_id": "run-mock-plan", "status": "planning"}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/runs", json={"data": {"attributes": {"plan-only": True}, "relationships": {"workspace": {"data": {"type": "workspaces", "id": workspace_id}}}, "type": "runs"}})
        resp.raise_for_status()
        run = resp.json()["data"]
    return {"action": "plan_workspace", "workspace_id": workspace_id, "run_id": run["id"], "status": run["attributes"]["status"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "plan-only run has no rollback"}
