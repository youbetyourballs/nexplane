async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_jobs", "jobs": [], "count": 0}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.get("/jobs")
        resp.raise_for_status()
        jobs_data = resp.json().get("return", [{}])[0]
        jobs = [{"jid": jid, "function": info.get("Function"), "target": info.get("Target"), "started": info.get("StartTime")} for jid, info in jobs_data.items()]
    return {"action": "discover_jobs", "jobs": jobs[:100], "count": len(jobs)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
