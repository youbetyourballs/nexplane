# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""OCI DNS DNSSEC enable executor.

Enables DNSSEC signing on an OCI DNS zone via UpdateZone API.
OCI auto-manages keys.

Rollback: UpdateZone with dnssec_state="DISABLED".
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    zone_id = parameters["zone_id"]
    dry_run = parameters.get("dry_run", False)

    def _get_zone():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        dns_client = oci.dns.DnsClient(config)
        return dns_client.get_zone(zone_name_or_id=zone_id).data

    zone = await _run(_get_zone)

    if getattr(zone, "dnssec_state", "DISABLED") == "ENABLED":
        logger.info("oci_dns_dnssec_enable: zone %s already ENABLED — no-op", zone_id)
        return {
            "status": "enabled",
            "already_enabled": True,
            "zone_id": zone_id,
            "enabled_at": datetime.now(timezone.utc).isoformat(),
        }

    if dry_run:
        return {"status": "dry_run", "zone_id": zone_id}

    def _update_zone():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        dns_client = oci.dns.DnsClient(config)
        details = oci.dns.models.UpdateZoneDetails(dnssec_state="ENABLED")
        return dns_client.update_zone(zone_name_or_id=zone_id, update_zone_details=details)

    await _run(_update_zone)
    logger.info("oci_dns_dnssec_enable: UpdateZone called for %s", zone_id)

    # Poll until ENABLED (up to 120s)
    deadline = time.time() + 120
    while time.time() < deadline:
        await asyncio.sleep(5)

        def _poll():
            import oci
            from app.connectors.executors.oci._client import get_oci_config
            config = get_oci_config(creds)
            dns_client = oci.dns.DnsClient(config)
            return dns_client.get_zone(zone_name_or_id=zone_id).data

        polled = await _run(_poll)
        if getattr(polled, "dnssec_state", "") == "ENABLED":
            break
    else:
        raise TimeoutError(f"OCI DNS zone {zone_id} did not reach DNSSEC ENABLED within 120s")

    return {
        "status": "enabled",
        "already_enabled": False,
        "zone_id": zone_id,
        "enabled_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    if execution_result.get("already_enabled"):
        return {"rolled_back": False, "reason": "DNSSEC was already enabled before execution — nothing to undo"}

    creds = connector.credentials
    zone_id = parameters["zone_id"]

    def _disable():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        dns_client = oci.dns.DnsClient(config)
        details = oci.dns.models.UpdateZoneDetails(dnssec_state="DISABLED")
        return dns_client.update_zone(zone_name_or_id=zone_id, update_zone_details=details)

    await _run(_disable)
    logger.info("oci_dns_dnssec_enable rollback: DNSSEC disabled for zone %s", zone_id)

    return {
        "rolled_back": True,
        "zone_id": zone_id,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
