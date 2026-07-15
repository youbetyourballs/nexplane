# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


def _mock_response():
    return {
        "action": "discover_block_volumes",
        "assets": [
            {
                "id": "ocid1.volume.oc1..mock1",
                "name": "mock-volume-1",
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "volume_id": "ocid1.volume.oc1..mock1",
                    "compartment_id": "ocid1.compartment.oc1..mock",
                    "size_in_gbs": 50,
                    "lifecycle_state": "AVAILABLE",
                    "vpus_per_gb": 10,
                    "region": "us-ashburn-1",
                },
                "tags": ["oci", "block-volume"],
            },
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_blockstorage_client
    client = get_blockstorage_client(creds)
    compartment_id = creds.get("compartment_id", "")
    region = creds.get("region", "us-ashburn-1")
    loop = asyncio.get_running_loop()

    def _call():
        volumes = client.list_volumes(compartment_id=compartment_id).data
        assets = []
        for v in volumes:
            assets.append({
                "id": v.id,
                "name": v.display_name,
                "asset_type": "storage_bucket",
                "asset_metadata": {
                    "volume_id": v.id,
                    "compartment_id": v.compartment_id,
                    "size_in_gbs": v.size_in_gbs,
                    "lifecycle_state": v.lifecycle_state,
                    "vpus_per_gb": getattr(v, "vpus_per_gb", 10),
                    "region": region,
                },
                "tags": ["oci", "block-volume"],
            })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_block_volumes", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
