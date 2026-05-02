async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_machines", "machines": [
            {"id": "mock-machine-1", "hostname": "mock-server", "os": "Windows 10", "health_status": "Active", "risk_level": "Medium", "exposure_level": "Medium"}
        ], "count": 1}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.get("/machines", params={"$top": 1000})
        resp.raise_for_status()
        machines = [{"id": m["id"], "hostname": m.get("computerDnsName"), "os": m.get("osPlatform"), "health_status": m.get("healthStatus"), "risk_level": m.get("riskScore"), "exposure_level": m.get("exposureLevel")} for m in resp.json().get("value", [])]
    return {"action": "discover_machines", "machines": machines, "count": len(machines)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
