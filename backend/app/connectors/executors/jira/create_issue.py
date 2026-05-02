async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "create_issue", "issue_key": "SEC-MOCK-1", "id": "mock-issue-id"}
    from ._client import get_client
    fields = {
        "project": {"key": parameters["project_key"]},
        "issuetype": {"name": parameters["issue_type"]},
        "summary": parameters["summary"],
    }
    if parameters.get("description"):
        fields["description"] = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": parameters["description"]}]}]}
    if parameters.get("priority"):
        fields["priority"] = {"name": parameters["priority"]}
    if parameters.get("labels"):
        fields["labels"] = parameters["labels"]
    if parameters.get("assignee_id"):
        fields["assignee"] = {"id": parameters["assignee_id"]}
    async with get_client(creds) as client:
        resp = await client.post("/issue", json={"fields": fields})
        resp.raise_for_status()
        issue = resp.json()
    return {"action": "create_issue", "issue_key": issue["key"], "id": issue["id"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    issue_key = execution_result.get("issue_key")
    if not issue_key:
        return {"rolled_back": False, "reason": "no issue_key in result"}
    from app.connectors.executors.jira.transition_issue import execute as transition
    return await transition({"issue_key": issue_key, "transition_name": "Cancelled"}, [], connector)
