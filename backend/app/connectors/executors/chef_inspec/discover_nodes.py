async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_nodes", "nodes": [{"id": "mock-node", "name": "mock-server", "platform": "ubuntu"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/nodes/search", json={"filters": [], "page": 1, "per_page": 100})
        resp.raise_for_status()
        nodes = [{"id": n.get("id"), "name": n.get("name"), "platform": n.get("platform")} for n in resp.json().get("nodes", [])]
    return {"action": "discover_nodes", "nodes": nodes, "count": len(nodes)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
