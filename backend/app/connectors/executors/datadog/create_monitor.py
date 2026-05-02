async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters["name"]
    if not creds:
        return {"action": "create_monitor", "name": name, "id": "mock-monitor-id"}
    from ._client import get_v1_client
    body = {"name": name, "type": parameters["type"], "query": parameters["query"]}
    if parameters.get("message"):
        body["message"] = parameters["message"]
    async with get_v1_client(creds) as client:
        resp = await client.post("/monitor", json=body)
        resp.raise_for_status()
        monitor = resp.json()
    return {"action": "create_monitor", "name": name, "id": monitor.get("id")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete monitor manually if needed"}
