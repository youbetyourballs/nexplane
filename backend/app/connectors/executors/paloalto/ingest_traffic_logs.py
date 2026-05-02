from datetime import datetime, timezone

_FLOWS = [
    ("https-payments", "payments-api-01", "10.0.1.10", 443, 95000000, True),
    ("postgres-db", "db-primary-01", "10.0.3.10", 5432, 42000000, False),
    ("ssh-mgmt", "bastion-01", "10.0.5.10", 22, 1200000, False),
    ("http-web", "web-frontend-01", "10.0.2.10", 80, 200000000, True),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for service_name, host, dest_ip, port, bytes_total, high_volume in _FLOWS:
        tags = ["palo-observed"]
        if high_volume:
            tags.append("high-volume")
        results.append({
            "name": service_name,
            "asset_type": "application",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "source_host": host,
                "destination_ip": dest_ip,
                "port": port,
                "protocol": "TCP",
                "bytes_total": bytes_total,
                "last_seen": now,
                "paloalto_source": "paloalto",
            },
        })
    return results

