# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "DNS zone deletion is destructive; zone records cannot be recovered after deletion"

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Delete an OCI DNS Zone."""
    creds = getattr(connector, "credentials", {})
    zone_id = parameters.get("zone_id", "") or parameters.get("zone_name_or_id", "")

    if not creds:
        return {"action": "delete_dns_zone", "zone_id": zone_id, "mock": True}

    if not zone_id:
        return {"action": "delete_dns_zone", "skipped": True, "reason": "no zone_id"}

    import oci
    config = {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds.get("region", "us-ashburn-1"),
    }
    dns_client = oci.dns.DnsClient(config)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: dns_client.delete_zone(zone_id))
    return {
        "action": "delete_dns_zone",
        "zone_id": zone_id,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_dns_zone has no rollback"}
