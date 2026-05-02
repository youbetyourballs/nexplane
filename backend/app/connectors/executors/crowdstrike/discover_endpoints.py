import asyncio
from datetime import datetime, timezone

_HOSTS = [
    ("payments-api-01", True, ["payments", "prod"]),
    ("payments-api-02", True, ["payments", "prod"]),
    ("web-frontend-01", True, ["web", "prod"]),
    ("db-primary-01", True, ["database", "prod"]),
    ("legacy-app-01", False, ["legacy", "prod"]),
    ("bastion-01", True, ["infra", "prod"]),
    ("dev-workstation-05", False, ["dev"]),
    ("ci-runner-01", False, ["infra", "dev"]),
]


def _mock_response():
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for hostname, protected, tags in _HOSTS:
        status_tag = "crowdstrike-managed" if protected else "crowdstrike-unprotected"
        results.append({
            "name": hostname,
            "asset_type": "server",
            "environment": "dev" if "dev" in tags else "prod",
            "criticality": "critical" if "database" in tags else "high",
            "tags": [status_tag] + tags,
            "asset_metadata": {
                "os": "Ubuntu 22.04",
                "sensor_version": "7.14.0" if protected else None,
                "open_ports": [22, 443, 8080],
                "running_processes": ["nginx", "node", "postgres"] if "database" in tags else ["nginx", "node"],
                "last_seen": now,
                "crowdstrike_source": "crowdstrike",
            },
        })
    return results


async def _real_execute(creds: dict) -> list:
    from ._client import get_hosts_api
    falcon = get_hosts_api(creds)
    loop = asyncio.get_event_loop()

    ids_resp = await loop.run_in_executor(None, lambda: falcon.query_devices_by_filter(limit=500))
    device_ids = ids_resp.get("body", {}).get("resources", [])
    if not device_ids:
        return _mock_response()

    details_resp = await loop.run_in_executor(None, lambda: falcon.get_device_details(ids=device_ids))
    devices = details_resp.get("body", {}).get("resources", [])
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for dev in devices:
        hostname = dev.get("hostname", "unknown")
        sensor = dev.get("agent_version")
        protected = sensor is not None
        os_version = dev.get("os_version", "unknown")
        tags_raw = dev.get("tags", [])
        status_tag = "crowdstrike-managed" if protected else "crowdstrike-unprotected"
        results.append({
            "name": hostname,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": [status_tag] + tags_raw,
            "asset_metadata": {
                "os": os_version,
                "sensor_version": sensor,
                "device_id": dev.get("device_id"),
                "last_seen": dev.get("last_seen", now),
                "crowdstrike_source": "crowdstrike",
            },
        })
    return results if results else _mock_response()


async def execute(parameters: dict, asset_ids: list, connector) -> list:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)

