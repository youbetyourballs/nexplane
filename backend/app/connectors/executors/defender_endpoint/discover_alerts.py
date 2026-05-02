async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_alerts", "alerts": [], "count": 0}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.get("/alerts", params={"$filter": "status ne 'Resolved'", "$top": 1000})
        resp.raise_for_status()
        alerts = [{"id": a["id"], "title": a.get("title"), "severity": a.get("severity"), "machine_id": a.get("machineId"), "status": a.get("status")} for a in resp.json().get("value", [])]
    return {"action": "discover_alerts", "alerts": alerts, "count": len(alerts)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
