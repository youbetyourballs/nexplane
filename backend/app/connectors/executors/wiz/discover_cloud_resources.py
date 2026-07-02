# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

QUERY = """
query CloudResources($first: Int) {
  cloudResources(first: $first) {
    nodes { id type name cloudAccount { id name } region tags { key value } }
  }
}
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_cloud_resources", "resources": [
            {"id": "mock-res-1", "type": "virtual_machine", "name": "mock-vm", "cloud_account": "mock-account"}
        ], "count": 1}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds, connector)
    data = await graphql_query(token, QUERY, {"first": 500}, connector=connector)
    resources = [{"id": r["id"], "type": r["type"], "name": r["name"]} for r in data.get("data", {}).get("cloudResources", {}).get("nodes", [])]
    return {"action": "discover_cloud_resources", "resources": resources, "count": len(resources)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
