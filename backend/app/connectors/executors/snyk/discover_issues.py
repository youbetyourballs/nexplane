async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_issues", "issues": [], "count": 0}
    from ._client import get_client
    org_id = creds["org_id"]
    async with get_client(creds) as client:
        resp = await client.get(f"/orgs/{org_id}/issues", params={"version": "2023-05-29", "limit": 100})
        resp.raise_for_status()
        issues = [{"id": i["id"], "severity": i["attributes"].get("effective_severity_level"), "title": i["attributes"].get("title")} for i in resp.json().get("data", [])]
    return {"action": "discover_issues", "issues": issues, "count": len(issues)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
