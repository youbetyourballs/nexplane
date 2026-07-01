# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    role_id = parameters["role_id"]
    if not creds:
        return {"action": "remove_from_role", "user_id": user_id, "role_id": role_id, "removed": True}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get("/roleManagement/directory/roleAssignments", params={"$filter": f"principalId eq '{user_id}' and roleDefinitionId eq '{role_id}'"})
        resp.raise_for_status()
        assignments = resp.json().get("value", [])
        for assignment in assignments:
            await client.delete(f"/roleManagement/directory/roleAssignments/{assignment['id']}")
    return {"action": "remove_from_role", "user_id": user_id, "role_id": role_id, "removed": len(assignments)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "re-adding to role requires explicit action"}
