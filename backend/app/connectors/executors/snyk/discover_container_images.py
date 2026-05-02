async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_container_images", "images": [], "count": 0}
    from ._client import get_client
    org_id = creds["org_id"]
    async with get_client(creds) as client:
        resp = await client.get(f"/orgs/{org_id}/projects", params={"version": "2023-05-29", "limit": 100, "types": "dockerfileScan,containerImage"})
        resp.raise_for_status()
        images = [{"id": p["id"], "name": p["attributes"]["name"], "type": p["attributes"].get("type")} for p in resp.json().get("data", [])]
    return {"action": "discover_container_images", "images": images, "count": len(images)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
