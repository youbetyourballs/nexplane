async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_locations", "locations": [], "count": 0}
    from ._client import get_session
    async with await get_session(creds) as client:
        resp = await client.get("/locations")
        resp.raise_for_status()
        locations = [{"id": l.get("id"), "name": l.get("name"), "country": l.get("country")} for l in resp.json()]
    return {"action": "discover_locations", "locations": locations, "count": len(locations)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
