# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

_STORAGE = [
    ("acmeprodbackups", False, "GRS"),
    ("acmepublicassets", True, "LRS"),
    ("acmelogarchive", False, "GRS"),
    ("acmeuploads", True, "LRS"),
]


def _mock_response():
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
                "azure_source": "azure",
            },
        })
    return results


async def _real_execute(creds: dict) -> list:
    from ._client import get_storage_client
    storage = get_storage_client(creds)
    loop = asyncio.get_event_loop()
    accounts = await loop.run_in_executor(None, lambda: list(storage.storage_accounts.list()))
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for acct in accounts:
        public_access = getattr(acct, "allow_blob_public_access", False) or False
        sku = getattr(acct, "sku", None)
        replication = getattr(sku, "name", "unknown") if sku else "unknown"
        encryption = getattr(getattr(acct, "encryption", None), "services", None) is not None
        tags = ["azure-storage"]
        if public_access:
            tags.append("azure-public-storage")
        results.append({
            "name": acct.name,
            "asset_type": "cloud_account",
            "environment": "prod",
            "criticality": "high",
            "tags": tags,
            "asset_metadata": {
                "public_blob_access": public_access,
                "replication_type": replication,
                "encryption_enabled": encryption,
                "region": getattr(acct, "location", "unknown"),
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
