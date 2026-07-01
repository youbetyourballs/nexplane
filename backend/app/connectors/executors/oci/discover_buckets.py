# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_buckets",
        "assets": [
            {
                "id": "oci-bucket-mock-1",
                "name": "my-private-bucket",
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": "my-private-bucket",
                    "namespace": "mock-namespace",
                    "compartment_id": "ocid1.compartment.oc1..mock",
                    "region": "us-ashburn-1",
                    "provider": "oci",
                    "public_access_type": "NoPublicAccess",
                    "storage_tier": "Standard",
                    "versioning": "Disabled",
                },
                "tags": ["oci", "object-storage"],
            },
            {
                "id": "oci-bucket-mock-2",
                "name": "my-public-bucket",
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": "my-public-bucket",
                    "namespace": "mock-namespace",
                    "compartment_id": "ocid1.compartment.oc1..mock",
                    "region": "us-ashburn-1",
                    "provider": "oci",
                    "public_access_type": "ObjectRead",
                    "storage_tier": "Standard",
                    "versioning": "Disabled",
                },
                "tags": ["oci", "object-storage"],
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_objectstorage_client
    client = get_objectstorage_client(creds)
    compartment_id = creds.get("compartment_id", "")
    region = creds.get("region", "us-ashburn-1")
    loop = asyncio.get_running_loop()

    def _call():
        namespace = client.get_namespace().data
        buckets = client.list_buckets(namespace_name=namespace, compartment_id=compartment_id).data
        assets = []
        for b in buckets:
            assets.append({
                "id": f"oci-bucket-{b.name}",
                "name": b.name,
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "bucket_name": b.name,
                    "namespace": namespace,
                    "compartment_id": b.compartment_id,
                    "region": region,
                    "provider": "oci",
                    "public_access_type": getattr(b, "public_access_type", "NoPublicAccess") or "NoPublicAccess",
                    "storage_tier": getattr(b, "storage_tier", "Standard") or "Standard",
                    "versioning": getattr(b, "versioning", "Disabled") or "Disabled",
                },
                "tags": ["oci", "object-storage"],
            })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_buckets", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
