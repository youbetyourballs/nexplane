# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    issue_key = parameters["issue_key"]
    assignee_id = parameters["assignee_id"]
    if not creds:
        return {"action": "assign_issue", "issue_key": issue_key, "assignee_id": assignee_id, "assigned": True}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.put(f"/issue/{issue_key}/assignee", json={"accountId": assignee_id})
        resp.raise_for_status()
    return {"action": "assign_issue", "issue_key": issue_key, "assignee_id": assignee_id, "assigned": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous assignee not captured — re-assign manually"}
