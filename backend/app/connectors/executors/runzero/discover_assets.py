async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_assets", "assets": [
            {"id": "mock-asset-1", "address": "10.0.0.1", "hostname": "mock-server", "os": "Linux 5.x",
             "type": "server", "first_seen": "2025-01-01T00:00:00Z", "last_seen": "2025-06-01T00:00:00Z"}
        ], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/org/assets", params={"_oid": creds["org_id"], "fields": "id,addresses,hostnames,os,type,first_seen,last_seen,tags"})
        resp.raise_for_status()
        data = resp.json()
        assets = [{"id": a.get("id"), "addresses": a.get("addresses", []), "hostname": a.get("names", [""])[0] if a.get("names") else None, "os": a.get("os"), "type": a.get("type")} for a in data]
    return {"action": "discover_assets", "assets": assets, "count": len(assets)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
