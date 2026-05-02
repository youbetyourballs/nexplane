async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_wireless_networks", "networks": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/org/wireless", params={"_oid": creds["org_id"]})
        resp.raise_for_status()
        networks = resp.json()
    return {"action": "discover_wireless_networks", "networks": networks, "count": len(networks)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
