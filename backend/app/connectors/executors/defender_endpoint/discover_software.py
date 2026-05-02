async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_software", "software": [], "count": 0}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.get("/software", params={"$top": 1000})
        resp.raise_for_status()
        software = [{"id": s.get("id"), "name": s.get("name"), "vendor": s.get("vendor"), "vulnerabilities": s.get("vulnerabilitiesCount", 0)} for s in resp.json().get("value", [])]
    return {"action": "discover_software", "software": software, "count": len(software)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
