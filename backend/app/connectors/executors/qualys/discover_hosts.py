import xml.etree.ElementTree as ET

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_hosts", "hosts": [{"ip": "10.0.0.1", "hostname": "mock-host", "os": "Linux"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/api/2.0/fo/asset/host/", params={"action": "list", "details": "All", "truncation_limit": 500})
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        hosts = [{"id": h.findtext("ID"), "ip": h.findtext("IP"), "hostname": h.findtext("DNS"), "os": h.findtext("OS")} for h in root.findall(".//HOST")]
    return {"action": "discover_hosts", "hosts": hosts, "count": len(hosts)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
