async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_oncall", "oncall": [{"schedule": "Primary", "user": "alice@example.com"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/oncalls", params={"limit": 100})
        resp.raise_for_status()
        oncall = [{"schedule": o.get("schedule", {}).get("summary"), "user": o.get("user", {}).get("email"), "start": o.get("start"), "end": o.get("end")} for o in resp.json().get("oncalls", [])]
    return {"action": "discover_oncall", "oncall": oncall, "count": len(oncall)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
