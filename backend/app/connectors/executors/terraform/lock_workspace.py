# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "lock_workspace", "workspace_id": workspace_id, "locked": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post(f"/workspaces/{workspace_id}/actions/lock", json={"reason": parameters.get("reason", "Locked by Nexplane")})
        resp.raise_for_status()
    return {"action": "lock_workspace", "workspace_id": workspace_id, "locked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.terraform.unlock_workspace import execute as unlock
    return await unlock(parameters, [], connector)
