# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

QUERY = """
query AttackPaths($first: Int) {
  attackPaths(first: $first, filterBy: {riskLevel: [CRITICAL, HIGH]}) {
    nodes { id riskLevel attackVector endpoints { id name type } }
  }
}
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "ingest_attack_paths", "attack_paths": [], "count": 0}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds)
    data = await graphql_query(token, QUERY, {"first": 200})
    paths = data.get("data", {}).get("attackPaths", {}).get("nodes", [])
    return {"action": "ingest_attack_paths", "attack_paths": paths, "count": len(paths)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ingest has no rollback"}
