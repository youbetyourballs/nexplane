from datetime import datetime, timezone

_NSGS = [
    ("payments-subnet-nsg", ["payments"], 12, False),
    ("web-tier-nsg", ["web", "dmz"], 8, True),
    ("mgmt-nsg", ["infra"], 5, False),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for name, subnets, rule_count, permissive in _NSGS:
        tags = ["azure-nsg"]
        if permissive:
            tags.append("azure-nsg-permissive")
        results.append({
            "name": name,
            "asset_type": "firewall",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "associated_subnets": subnets,
                "rule_count": rule_count,
                "has_any_source_rules": permissive,
                "region": "eastus",
                "discovered_at": now,
                "azure_source": "azure_mock",
            },
        })
    return results
