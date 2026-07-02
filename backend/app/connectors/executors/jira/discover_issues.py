# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_issues", "issues": [
            {"key": "SEC-1", "summary": "Mock security issue", "priority": "High", "status": "In Progress", "assignee": "alice"}
        ], "count": 1}
    from ._client import get_client
    jql = parameters.get("jql")
    if not jql:
        project = parameters.get("project_key", "")
        jql = f"project = {project} AND status != Done ORDER BY priority DESC" if project else "status != Done ORDER BY priority DESC"
    async with await get_client(connector) as client:
        resp = await client.post("/search", json={"jql": jql, "maxResults": 100, "fields": ["summary", "priority", "status", "assignee", "components"]})
        resp.raise_for_status()
        issues = [{"key": i["key"], "summary": i["fields"]["summary"], "priority": i["fields"].get("priority", {}).get("name"), "status": i["fields"]["status"]["name"], "assignee": i["fields"].get("assignee", {}).get("displayName") if i["fields"].get("assignee") else None} for i in resp.json().get("issues", [])]
    return {"action": "discover_issues", "issues": issues, "count": len(issues)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
