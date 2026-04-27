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

async def execute(parameters: dict, asset_ids: list, connector) -> list:
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
                "crowdstrike_source": "crowdstrike_mock",
            },
        })
    return results
