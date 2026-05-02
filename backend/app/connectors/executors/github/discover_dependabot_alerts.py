async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_dependabot_alerts", "alerts": [], "count": 0}
    from ._client import get_client
    org = creds["org"]
    async with get_client(creds) as client:
        resp = await client.get(f"/orgs/{org}/dependabot/alerts", params={"state": "open", "per_page": 100})
        resp.raise_for_status()
        alerts = [{"number": a["number"], "severity": a.get("security_advisory", {}).get("severity"), "package": a.get("dependency", {}).get("package", {}).get("name"), "repo": a.get("repository", {}).get("name")} for a in resp.json()]
    return {"action": "discover_dependabot_alerts", "alerts": alerts, "count": len(alerts)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
