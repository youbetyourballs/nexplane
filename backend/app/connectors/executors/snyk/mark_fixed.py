async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    project_id = parameters["project_id"]
    issue_id = parameters["issue_id"]
    if not creds:
        return {"action": "mark_fixed", "project_id": project_id, "issue_id": issue_id, "marked_fixed": True}
    from ._client import get_client
    org_id = creds["org_id"]
    async with get_client(creds) as client:
        resp = await client.patch(f"/orgs/{org_id}/projects/{project_id}/issues/{issue_id}", json={"data": {"attributes": {"status": "resolved"}}}, params={"version": "2023-05-29"})
        resp.raise_for_status()
    return {"action": "mark_fixed", "project_id": project_id, "issue_id": issue_id, "marked_fixed": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unmark fixed manually in Snyk UI"}
