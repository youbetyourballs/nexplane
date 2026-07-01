# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_runs", "runs": [
            {"id": "run-mock", "workspace_id": "ws-mock", "status": "applied", "triggered_by": "user@example.com"}
        ], "count": 1}
    from ._client import get_client
    workspace_id = parameters.get("workspace_id")
    async with get_client(creds) as client:
        if workspace_id:
            resp = await client.get(f"/workspaces/{workspace_id}/runs", params={"page[size]": 50})
        else:
            resp = await client.get(f"/organizations/{creds['organization']}/runs", params={"page[size]": 50})
        resp.raise_for_status()
        runs = [{"id": r["id"], "status": r["attributes"]["status"], "created": r["attributes"].get("created-at")} for r in resp.json().get("data", [])]
    return {"action": "discover_runs", "runs": runs, "count": len(runs)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
