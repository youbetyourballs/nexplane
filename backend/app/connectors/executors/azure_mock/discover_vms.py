import asyncio
from datetime import datetime, timezone

_VMS = [
    ("azure-web-01", "eastus", "Standard_D2s_v3", "10.1.1.10"),
    ("azure-web-02", "eastus", "Standard_D2s_v3", "10.1.1.11"),
    ("azure-db-01", "eastus", "Standard_E4s_v3", None),
    ("azure-bastion-01", "eastus", "Standard_B2s", "20.50.100.42"),
]


def _mock_response():
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


async def _real_execute(creds: dict) -> list:
    from ._client import get_compute_client
    compute = get_compute_client(creds)
    loop = asyncio.get_event_loop()
    vms = await loop.run_in_executor(None, lambda: list(compute.virtual_machines.list_all()))
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for vm in vms:
        location = getattr(vm, "location", "unknown")
        hw = getattr(vm, "hardware_profile", None)
        vm_size = getattr(hw, "vm_size", "unknown") if hw else "unknown"
        results.append({
            "name": vm.name,
            "asset_type": "server",
            "environment": "prod",
            "criticality": "high",
            "tags": ["azure"],
            "asset_metadata": {
                "region": location,
                "vm_size": vm_size,
                "public_ip": None,
                "os": "unknown",
                "subscription": creds.get("subscription_id"),
                "discovered_at": now,
                "azure_source": "azure",
            },
        })
    return results if results else _mock_response()


async def execute(parameters: dict, asset_ids: list, connector) -> list:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)
