from datetime import datetime, timezone

_STORAGE = [
    ("acmeprodbackups", False, "GRS"),
    ("acmepublicassets", True, "LRS"),
    ("acmelogarchive", False, "GRS"),
    ("acmeuploads", True, "LRS"),
]

async def execute(parameters: dict, asset_ids: list, connector) -> list:
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for name, public_access, replication in _STORAGE:
        tags = ["azure-storage"]
        if public_access:
            tags.append("azure-public-storage")
        results.append({
            "name": name,
            "asset_type": "cloud_account",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "public_blob_access": public_access,
                "replication_type": replication,
                "encryption_enabled": True,
                "region": "eastus",
                "discovered_at": now,
                "azure_source": "azure_mock",
            },
        })
    return results
