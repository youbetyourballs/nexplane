import xml.etree.ElementTree as ET

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_asset_groups", "groups": [{"id": "1", "title": "Default Group"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/api/2.0/fo/asset/group/", params={"action": "list"})
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        groups = [{"id": g.findtext("ID"), "title": g.findtext("TITLE")} for g in root.findall(".//ASSET_GROUP")]
    return {"action": "discover_asset_groups", "groups": groups, "count": len(groups)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
