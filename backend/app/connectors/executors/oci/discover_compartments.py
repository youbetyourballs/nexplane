# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})

    if not creds:
        root_asset = {
            "name": "OCI · mock-tenancy",
            "asset_type": "cloud_account",
            "asset_metadata": {
                "compartment_id": "ocid1.tenancy.oc1..mock",
                "tenancy_id": "ocid1.tenancy.oc1..mock",
                "region": "us-ashburn-1",
                "provider": "oci",
                "lifecycle_state": "ACTIVE",
            },
            "tags": ["oci", "compartment"],
        }
        return {
            "action": "discover_compartments",
            "assets": [root_asset],
            "count": 1,
            "mock": True,
            "_auto_asset": root_asset,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_identity_client

    identity = get_identity_client(creds)
    tenancy_id = creds["tenancy"]
    region = creds["region"]
    loop = asyncio.get_running_loop()

    tenancy = await loop.run_in_executor(
        None, lambda: identity.get_tenancy(tenancy_id).data
    )
    tenancy_name = tenancy.name

    compartments_response = await loop.run_in_executor(
        None,
        lambda: identity.list_compartments(
            tenancy_id,
            compartment_id_in_subtree=True,
            lifecycle_state="ACTIVE",
        ).data,
    )

    assets = []

    root_asset = {
        "name": f"OCI · {tenancy_name}",
        "asset_type": "cloud_account",
        "asset_metadata": {
            "compartment_id": tenancy_id,
            "tenancy_id": tenancy_id,
            "region": region,
            "provider": "oci",
            "lifecycle_state": "ACTIVE",
        },
        "tags": ["oci", "compartment"],
    }
    assets.append(root_asset)

    for comp in compartments_response:
        assets.append({
            "name": f"OCI · {comp.name}",
            "asset_type": "cloud_account",
            "asset_metadata": {
                "compartment_id": comp.id,
                "tenancy_id": tenancy_id,
                "region": region,
                "provider": "oci",
                "lifecycle_state": comp.lifecycle_state,
            },
            "tags": ["oci", "compartment"],
        })

    return {
        "action": "discover_compartments",
        "assets": assets,
        "count": len(assets),
        "_auto_asset": root_asset,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
