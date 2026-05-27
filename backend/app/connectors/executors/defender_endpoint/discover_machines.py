from .defender_client import get_defender_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    client = get_defender_client(connector)
    if not client:
        return {"machines": [], "count": 0, "status": "skipped", "reason": "no_defender_credentials"}
    machines = await client.list_machines()
    summary = [
        {
            "id": m.get("id"),
            "computerDnsName": m.get("computerDnsName"),
            "osPlatform": m.get("osPlatform"),
            "healthStatus": m.get("healthStatus"),
            "riskScore": m.get("riskScore"),
            "exposureLevel": m.get("exposureLevel"),
            "lastSeen": m.get("lastSeen"),
        }
        for m in machines
    ]
    return {"machines": summary, "count": len(summary)}
