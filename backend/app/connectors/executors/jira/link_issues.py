async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "link_issues", "inward": parameters.get("inward_issue_key"), "outward": parameters.get("outward_issue_key"), "linked": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/issueLink", json={"type": {"name": parameters["link_type"]}, "inwardIssue": {"key": parameters["inward_issue_key"]}, "outwardIssue": {"key": parameters["outward_issue_key"]}})
        resp.raise_for_status()
    return {"action": "link_issues", "inward": parameters["inward_issue_key"], "outward": parameters["outward_issue_key"], "linked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "link removal requires knowing the link ID — remove manually"}
