import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_endpoints",
        "assets": [
            {
                "id": "device-mock-001",
                "name": "workstation-01",
                "asset_type": "endpoint",
                "asset_metadata": {
                    "device_id": "device-mock-001",
                    "hostname": "workstation-01",
                    "platform_name": "Windows",
                    "os_version": "Windows 10",
                    "containment_status": "normal",
                    "agent_version": "7.0.0",
                    "provider": "crowdstrike",
                },
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()

    def _call():
        # Get all device IDs (paginated)
        device_ids = []
        offset = 0
        limit = 100
        while True:
            resp = falcon.query_devices_by_filter(limit=limit, offset=offset)
            body = (resp or {}).get("body", {})
            ids = body.get("resources", [])
            device_ids.extend(ids)
            total = body.get("meta", {}).get("pagination", {}).get("total", 0)
            offset += limit
            if offset >= total or not ids:
                break

        if not device_ids:
            return []

        # Get details in batches of 100
        assets = []
        for i in range(0, len(device_ids), 100):
            batch = device_ids[i:i+100]
            detail_resp = falcon.get_device_details(ids=batch)
            resources = (detail_resp or {}).get("body", {}).get("resources", [])
            for device in resources:
                hostname = device.get("hostname", device.get("device_id", "unknown"))
                assets.append({
                    "id": device["device_id"],
                    "name": hostname,
                    "asset_type": "endpoint",
                    "asset_metadata": {
                        "device_id": device["device_id"],
                        "hostname": hostname,
                        "platform_name": device.get("platform_name"),
                        "os_version": device.get("os_version"),
                        "containment_status": device.get("network_containment_status", "normal"),
                        "agent_version": device.get("agent_version"),
                        "provider": "crowdstrike",
                    },
                })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_endpoints", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
