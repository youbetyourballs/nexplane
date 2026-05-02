async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_compliance_results", "results": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/compliance/reporting/nodes/search", json={"filters": [], "page": 1, "per_page": 50})
        resp.raise_for_status()
        results = [{"node_id": n.get("id"), "node_name": n.get("name"), "status": n.get("status")} for n in resp.json().get("nodes", [])]
    return {"action": "discover_compliance_results", "results": results, "count": len(results)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
