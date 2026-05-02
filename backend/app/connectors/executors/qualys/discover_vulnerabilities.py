import xml.etree.ElementTree as ET

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_vulnerabilities", "vulnerabilities": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/api/2.0/fo/asset/host/vm/detection/", data={"action": "list", "show_results": 1, "status": "New,Active"})
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        vulns = [{"qid": d.findtext("QID"), "ip": d.getparent().findtext("IP") if hasattr(d, "getparent") else None, "severity": d.findtext("SEVERITY"), "status": d.findtext("STATUS")} for d in root.findall(".//DETECTION")]
    return {"action": "discover_vulnerabilities", "vulnerabilities": vulns, "count": len(vulns)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
