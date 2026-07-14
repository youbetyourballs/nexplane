# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")
    name = parameters.get("name", "nexplane-test.example.com")
    zone_type = parameters.get("zone_type", "PRIMARY")

    auto_asset = {
        "name": name,
        "asset_type": "dns_zone",
        "environment": "prod",
        "criticality": "high",
        "asset_metadata": {
            "zone_id": "ocid1.dns-zone.mock",
            "zone_name": name,
            "zone_type": zone_type,
            "compartment_id": compartment_id or "ocid1.compartment.mock",
            "serial": 1,
            "provider": "oci",
        },
        "tags": ["oci", "dns", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_dns_zone",
            "zone_id": "ocid1.dns-zone.mock",
            "zone_name": name,
            "zone_type": zone_type,
            "mock": True,
            "_auto_asset": auto_asset,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_dns_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    dns_client = get_dns_client(creds)
    comp_id = compartment_id or creds.get("tenancy", "")

    details = oci_sdk.dns.models.CreateZoneDetails(
        compartment_id=comp_id,
        name=name,
        zone_type=zone_type,
    )
    zone = await loop.run_in_executor(
        None, lambda: dns_client.create_zone(details).data
    )

    # Wait for ACTIVE state (up to 2 min)
    zone_id = zone.id
    for _ in range(24):
        await asyncio.sleep(5)
        zone = await loop.run_in_executor(
            None, lambda: dns_client.get_zone(zone_id).data
        )
        if zone.lifecycle_state == "ACTIVE":
            break
        if zone.lifecycle_state == "FAILED":
            raise RuntimeError(
                f"DNS zone '{name}' reached FAILED state — "
                "this may indicate a quota limit or invalid zone name on this tenancy."
            )

    auto_asset["asset_metadata"].update({
        "zone_id": zone.id,
        "zone_name": zone.name,
        "serial": zone.serial,
        "compartment_id": zone.compartment_id,
    })

    return {
        "action": "create_dns_zone",
        "zone_id": zone.id,
        "zone_name": zone.name,
        "zone_type": zone.zone_type,
        "_auto_asset": auto_asset,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the created DNS zone."""
    creds = getattr(connector, "credentials", {})
    zone_id = execution_result.get("zone_id")
    if not zone_id:
        return {"rolled_back": False, "reason": "no zone_id in execution result"}
    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_dns_client
    loop = asyncio.get_running_loop()
    dns_client = get_dns_client(creds)

    await loop.run_in_executor(None, lambda: dns_client.delete_zone(zone_id))
    return {"rolled_back": True, "zone_id": zone_id}
