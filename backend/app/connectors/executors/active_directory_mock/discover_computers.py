from datetime import datetime, timezone
import random

_OUS = ["OU=Servers,DC=acme,DC=example", "OU=Workstations,DC=acme,DC=example", "OU=DMZ,DC=acme,DC=example"]
_HOSTS = ["dc-01", "dc-02", "web-01", "web-02", "app-01", "app-02", "db-01", "payments-api-01", "bastion-01"]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    return [
        {
            "name": host,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["ad-joined"],
            "asset_metadata": {
                "os": "Windows Server 2022",
                "ou": random.choice(_OUS),
                "last_logon": now,
                "ad_source": "active_directory_mock",
            },
        }
        for host in _HOSTS
    ]
