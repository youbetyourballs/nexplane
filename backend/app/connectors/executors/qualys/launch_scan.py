import xml.etree.ElementTree as ET

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "launch_scan", "scan_ref": "scan-mock-001", "launched": True}
    from ._client import get_client
    params = {"action": "launch", "scan_title": "Nexplane Scan"}
    if parameters.get("target_ips"):
        params["ip"] = parameters["target_ips"]
    if parameters.get("asset_group_ids"):
        params["asset_group_ids"] = parameters["asset_group_ids"]
    if parameters.get("option_profile_id"):
        params["option_id"] = parameters["option_profile_id"]
    async with get_client(creds) as client:
        resp = await client.post("/api/2.0/fo/scan/", data=params)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        scan_ref = root.findtext(".//VALUE") or "unknown"
    return {"action": "launch_scan", "scan_ref": scan_ref, "launched": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot cancel an initiated scan"}
