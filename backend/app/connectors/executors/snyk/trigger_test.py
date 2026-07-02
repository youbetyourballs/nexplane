# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    project_id = parameters["project_id"]
    if not creds:
        return {"action": "trigger_test", "project_id": project_id, "triggered": True}
    from ._client import get_client
    org_id = creds["org_id"]
    async with await get_client(connector) as client:
        resp = await client.post(f"/orgs/{org_id}/projects/{project_id}/test", params={"version": "2023-05-29"})
        resp.raise_for_status()
    return {"action": "trigger_test", "project_id": project_id, "triggered": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan has no rollback"}
