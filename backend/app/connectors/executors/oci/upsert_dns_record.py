# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name_or_id = parameters.get("zone_name_or_id", "")
    domain = parameters.get("domain", "www.nexplane-test.example.com")
    rtype = parameters.get("rtype", "A")
    ttl = int(parameters.get("ttl", 300))
    rdata = parameters.get("rdata", "10.0.0.1")

    if not creds:
        return {
            "action": "upsert_dns_record",
            "zone_name_or_id": zone_name_or_id or "mock-zone.example.com",
            "domain": domain,
            "rtype": rtype,
            "ttl": ttl,
            "rdata": rdata,
            "mock": True,
            "previous_rdata": None,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_dns_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    dns_client = get_dns_client(creds)

    # Capture existing record for rollback
    previous_rdata = None
    try:
        existing = await loop.run_in_executor(
            None,
            lambda: dns_client.get_rr_set(
                zone_name_or_id=zone_name_or_id,
                domain=domain,
                rtype=rtype,
            ).data
        )
        if existing.items:
            previous_rdata = existing.items[0].rdata
    except Exception:
        pass  # Record may not exist yet — that's fine for an upsert

    record_item = oci_sdk.dns.models.RecordDetails(
        domain=domain,
        ttl=ttl,
        rtype=rtype,
        rdata=rdata,
    )
    # Use update_rr_set for a clean upsert
    update_details = oci_sdk.dns.models.UpdateRRSetDetails(items=[record_item])
    await loop.run_in_executor(
        None,
        lambda: dns_client.update_rr_set(
            zone_name_or_id=zone_name_or_id,
            domain=domain,
            rtype=rtype,
            update_rr_set_details=update_details,
        )
    )

    return {
        "action": "upsert_dns_record",
        "zone_name_or_id": zone_name_or_id,
        "domain": domain,
        "rtype": rtype,
        "ttl": ttl,
        "rdata": rdata,
        "previous_rdata": previous_rdata,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore previous DNS record value, or delete the record if it was new."""
    creds = getattr(connector, "credentials", {})
    zone_name_or_id = execution_result.get("zone_name_or_id", parameters.get("zone_name_or_id"))
    domain = execution_result.get("domain", parameters.get("domain"))
    rtype = execution_result.get("rtype", parameters.get("rtype", "A"))
    ttl = int(execution_result.get("ttl", parameters.get("ttl", 300)))
    previous_rdata = execution_result.get("previous_rdata")

    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_dns_client
    import oci as oci_sdk
    loop = asyncio.get_running_loop()
    dns_client = get_dns_client(creds)

    if previous_rdata is None:
        # Record was new — delete it
        await loop.run_in_executor(
            None, lambda: dns_client.delete_rr_set(
                zone_name_or_id=zone_name_or_id, domain=domain, rtype=rtype
            )
        )
        return {"rolled_back": True, "action": "deleted_new_record", "domain": domain, "rtype": rtype}
    else:
        # Restore previous value
        record_item = oci_sdk.dns.models.RecordDetails(domain=domain, ttl=ttl, rtype=rtype, rdata=previous_rdata)
        update_details = oci_sdk.dns.models.UpdateRRSetDetails(items=[record_item])
        await loop.run_in_executor(
            None,
            lambda: dns_client.update_rr_set(
                zone_name_or_id=zone_name_or_id,
                domain=domain,
                rtype=rtype,
                update_rr_set_details=update_details,
            )
        )
        return {"rolled_back": True, "action": "restored_previous_record", "domain": domain, "rdata": previous_rdata}
