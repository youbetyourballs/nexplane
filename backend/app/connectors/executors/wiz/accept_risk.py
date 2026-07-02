# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

MUTATION = """
mutation AcceptRisk($id: ID!, $days: Int!, $note: String!) {
  updateIssue(input: {id: $id, status: IN_PROGRESS, dueAt: $days, note: $note}) {
    issue { id status }
  }
}
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    issue_id = parameters["issue_id"]
    if not creds:
        return {"action": "accept_risk", "issue_id": issue_id, "duration_days": parameters.get("duration_days"), "accepted": True}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds, connector)
    data = await graphql_query(token, MUTATION, {"id": issue_id, "days": parameters["duration_days"], "note": parameters["reason"]}, connector=connector)
    return {"action": "accept_risk", "issue_id": issue_id, "result": data.get("data", {})}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "risk acceptance cannot be undone automatically"}
