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
            "action": "discover_dns_zones",
            "assets": [
                {
                    "name": "mock-zone.example.com",
                    "asset_type": "dns_zone",
                    "environment": "prod",
                    "criticality": "high",
                    "asset_metadata": {
                        "zone_id": "ocid1.dns-zone.mock",
                        "zone_name": "mock-zone.example.com",
                        "zone_type": "PRIMARY",
                        "compartment_id": compartment_id or "ocid1.compartment.mock",
                        "serial": 1,
                        "provider": "oci",
                    },
                    "tags": ["oci", "dns"],
                }
            ],
            "mock": True,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_dns_client
    loop = asyncio.get_running_loop()
    dns_client = get_dns_client(creds)
    comp_id = compartment_id or creds.get("tenancy", "")

    zones = await loop.run_in_executor(
        None, lambda: dns_client.list_zones(compartment_id=comp_id).data
    )

    assets = []
    for zone in zones:
        assets.append({
            "name": zone.name,
            "asset_type": "dns_zone",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "zone_id": zone.id,
                "zone_name": zone.name,
                "zone_type": zone.zone_type,
                "compartment_id": zone.compartment_id,
                "serial": zone.serial,
                "provider": "oci",
            },
            "tags": ["oci", "dns"],
            "_dedup_key": zone.id,
            "_dedup_field": "zone_id",
        })

    return {
        "action": "discover_dns_zones",
        "assets": assets,
        "count": len(assets),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
