MUTATION = """
mutation ResolveIssue($id: ID!, $note: String!) {
  updateIssue(input: {id: $id, status: RESOLVED, note: $note}) {
    issue { id status }
  }
}
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    issue_id = parameters["issue_id"]
    if not creds:
        return {"action": "resolve_issue", "issue_id": issue_id, "status": "RESOLVED"}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds)
    data = await graphql_query(token, MUTATION, {"id": issue_id, "note": parameters["reason"]})
    return {"action": "resolve_issue", "issue_id": issue_id, "result": data.get("data", {}).get("updateIssue", {})}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "issue resolution cannot be undone automatically"}
