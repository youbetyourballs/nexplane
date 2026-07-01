# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not parameters.get("confirm_destroy"):
        return {"action": "destroy_workspace_resources", "error": "confirm_destroy must be true"}
    if not creds:
        return {"action": "destroy_workspace_resources", "workspace_id": workspace_id, "run_id": "run-mock-destroy", "status": "pending"}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/runs", json={"data": {"attributes": {"is-destroy": True, "auto-apply": True}, "relationships": {"workspace": {"data": {"type": "workspaces", "id": workspace_id}}}, "type": "runs"}})
        resp.raise_for_status()
        run = resp.json()["data"]
    return {"action": "destroy_workspace_resources", "workspace_id": workspace_id, "run_id": run["id"], "status": run["attributes"]["status"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "destroy is irreversible"}
