# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Placeholder — rollback_dns_zone_migrate is invoked as a rollback_action only."""
    return {"status": "noop", "message": "use as rollback_action only"}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore original NS records and TTL from backup captured during dns_zone_migrate."""
    creds = getattr(connector, "credentials", {})
    rollback_data = execution_result.get("rollback_data", {})
    # zone_name lives inside rollback_data (from dns_zone_migrate) or top-level
    zone = (
        execution_result.get("zone")
        or rollback_data.get("zone_name")
        or execution_result.get("summary", {}).get("zone_name")
    )
    server = execution_result.get("server") or creds.get("server")
    port = execution_result.get("port") or creds.get("port", 53)

    if not zone or not server or not rollback_data:
        return {
            "rolled_back": False,
            "reason": "Missing zone/server/rollback_data in execution_result — cannot restore NS records",
        }

    from app.connectors.executors.bind_dns.dns_zone_migrate import _make_tsig_key, _send_update

    keyring, keyname, algorithm = _make_tsig_key(creds)

    original_ns = rollback_data.get("original_ns", [])
    original_ns_ttl = rollback_data.get("original_ns_ttl", 300)

    if not original_ns:
        return {"rolled_back": False, "reason": "No original_ns in rollback_data"}

    updates = [
        {"op": "replace", "name": "@", "ttl": original_ns_ttl, "rdtype": "NS", "rdata": ns}
        for ns in original_ns
    ]

    rcode = _send_update(server, port, zone, updates, keyring, keyname, algorithm)
    if rcode != 0:
        return {"rolled_back": False, "reason": f"DNS UPDATE failed with rcode={rcode}"}

    logger.info("rollback_dns_zone_migrate: restored NS records for zone %s (ns=%s)", zone, original_ns)
    return {
        "rolled_back": True,
        "zone": zone,
        "restored_ns": original_ns,
        "restored_ns_ttl": original_ns_ttl,
    }
