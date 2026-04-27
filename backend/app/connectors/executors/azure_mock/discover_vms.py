from datetime import datetime, timezone

_VMS = [
    ("azure-web-01", "eastus", "Standard_D2s_v3", "10.1.1.10"),
    ("azure-web-02", "eastus", "Standard_D2s_v3", "10.1.1.11"),
    ("azure-db-01", "eastus", "Standard_E4s_v3", None),
    ("azure-bastion-01", "eastus", "Standard_B2s", "20.50.100.42"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": name,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "critical" if "db" in name else "high",
            "tags": ["azure"],
            "asset_metadata": {
                "region": region,
                "vm_size": size,
                "public_ip": public_ip,
                "os": "Ubuntu 22.04 LTS",
                "subscription": "prod-subscription-001",
                "discovered_at": now,
                "azure_source": "azure_mock",
            },
        }
        for name, region, size, public_ip in _VMS
    ]
