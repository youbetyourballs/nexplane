# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_privileged_roles", "role_assignments": [], "count": 0}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with await get_graph_client(token, connector) as client:
        resp = await client.get("/roleManagement/directory/roleAssignments", params={"$expand": "principal,roleDefinition", "$top": 999})
        resp.raise_for_status()
        assignments = [{"user_id": a.get("principalId"), "role_name": a.get("roleDefinition", {}).get("displayName"), "role_id": a.get("roleDefinitionId")} for a in resp.json().get("value", [])]
    return {"action": "discover_privileged_roles", "role_assignments": assignments, "count": len(assignments)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
