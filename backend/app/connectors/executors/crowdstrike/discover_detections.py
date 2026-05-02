from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_detections",
        "assets": [
            {"id": "host-001", "name": "workstation-01", "asset_type": "server", "metadata": {"tags": ["cs-detection:Malware.Generic", "cs-severity:high"]}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    from ._client import get_token
    import httpx
    base_url = creds.get('base_url', 'https://api.crowdstrike.com')
    token = await get_token(creds)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{base_url}/detects/queries/detects/v1?limit=100&sort=created_timestamp.desc", headers=headers)
        resp.raise_for_status()
        detect_ids = resp.json().get('resources', [])
        if not detect_ids:
            return {"action": "discover_detections", "assets": [], "discovered_at": datetime.now(timezone.utc).isoformat()}
        details_resp = await client.post(f"{base_url}/detects/entities/summaries/GET/v1", headers={**headers, "Content-Type": "application/json"}, json={"ids": detect_ids[:100]})
        details_resp.raise_for_status()
        assets = []
        for det in details_resp.json().get('resources', []):
            device = det.get('device', {})
            assets.append({
                "id": device.get('device_id', det['detection_id']),
                "name": device.get('hostname', 'unknown'),
                "asset_type": "server",
                "metadata": {"detection_id": det['detection_id'], "severity": det.get('max_severity_displayname'), "tags": [f"cs-detection:{det.get('behaviors', [{}])[0].get('tactic', 'unknown')}"]},
            })
    return {"action": "discover_detections", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
