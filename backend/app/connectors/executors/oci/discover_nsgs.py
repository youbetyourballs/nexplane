# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {
            "action": "discover_nsgs",
            "assets": [
                {
                    "name": "mock-nsg",
                    "asset_type": "firewall",
                    "environment": "prod",
                    "criticality": "medium",
                    "asset_metadata": {
                        "nsg_id": "ocid1.networksecuritygroup.mock",
                        "vcn_id": "ocid1.vcn.mock",
                        "compartment_id": compartment_id or "ocid1.compartment.mock",
                        "lifecycle_state": "AVAILABLE",
                    },
                    "tags": ["oci", "oci-nsg"],
                }
            ],
            "mock": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_network_client
    loop = asyncio.get_running_loop()
    network = get_network_client(creds)
    comp_id = compartment_id or creds.get("tenancy", "")

    nsgs = await loop.run_in_executor(
        None, lambda: network.list_network_security_groups(compartment_id=comp_id).data
    )

    assets = []
    for nsg in nsgs:
        assets.append({
            "name": nsg.display_name,
            "asset_type": "firewall",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": {
                "nsg_id": nsg.id,
                "vcn_id": nsg.vcn_id,
                "compartment_id": nsg.compartment_id,
                "lifecycle_state": nsg.lifecycle_state,
            },
            "tags": ["oci", "oci-nsg"],
            "_dedup_key": nsg.id,
            "_dedup_field": "nsg_id",
        })

    return {
        "action": "discover_nsgs",
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
