async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_hosts", "hosts": [{"name": "mock-host", "status": "UP", "tags": []}], "count": 1}
    from ._client import get_v1_client
    async with get_v1_client(creds) as client:
        resp = await client.get("/hosts", params={"count": 100})
        resp.raise_for_status()
        hosts = [{"name": h.get("name"), "status": h.get("up"), "tags": h.get("tags_by_source", {}).get("Datadog", [])} for h in resp.json().get("host_list", [])]
    return {"action": "discover_hosts", "hosts": hosts, "count": len(hosts)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
