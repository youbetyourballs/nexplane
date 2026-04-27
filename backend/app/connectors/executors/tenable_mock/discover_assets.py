from datetime import datetime, timezone

_HOSTS = [
    ("payments-api-01", "10.0.1.10", [22, 443, 8080]),
    ("payments-api-02", "10.0.1.11", [22, 443, 8080]),
    ("web-frontend-01", "10.0.2.10", [22, 80, 443]),
    ("db-primary-01", "10.0.3.10", [22, 5432]),
    ("legacy-app-01", "10.0.4.10", [22, 80, 8443, 21]),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": hostname,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "critical" if "db" in hostname else "high",
            "tags": ["tenable-scanned"],
            "asset_metadata": {
                "ip_address": ip,
                "open_ports": ports,
                "os": "Ubuntu 22.04",
                "last_scanned": now,
                "tenable_source": "tenable_mock",
            },
        }
        for hostname, ip, ports in _HOSTS
    ]
