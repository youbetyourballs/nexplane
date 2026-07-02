# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

QUERY = """
query Issues($first: Int) {
  issues(first: $first, filterBy: {status: [OPEN]}) {
    nodes { id type severity status sourceRule { name } resource { id name type } }
  }
}
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "ingest_issues", "issues": [], "count": 0}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds, connector)
    data = await graphql_query(token, QUERY, {"first": 500}, connector=connector)
    issues = data.get("data", {}).get("issues", {}).get("nodes", [])
    return {"action": "ingest_issues", "issues": issues, "count": len(issues)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ingest has no rollback"}
