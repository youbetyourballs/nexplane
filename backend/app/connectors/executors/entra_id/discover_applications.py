async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_applications", "applications": [{"id": "mock-app", "name": "Mock App", "publisher": "ACME"}], "count": 1}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get("/applications", params={"$select": "id,displayName,publisherDomain,createdDateTime", "$top": 999})
        resp.raise_for_status()
        apps = [{"id": a["id"], "name": a.get("displayName"), "publisher": a.get("publisherDomain")} for a in resp.json().get("value", [])]
    return {"action": "discover_applications", "applications": apps, "count": len(apps)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
