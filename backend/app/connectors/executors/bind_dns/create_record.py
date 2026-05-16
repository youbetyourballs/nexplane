"""Create a DNS record via RFC 2136 dynamic update."""
from __future__ import annotations
from datetime import datetime, timezone

import dns.rdatatype

from ._client import _resolve_zone, make_update, send_update


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = _resolve_zone(parameters, creds)

    record_name: str = parameters["record_name"].strip()
    record_type: str = parameters["record_type"].strip().upper()
    value: str = parameters["value"].strip()
    ttl: int = int(parameters.get("ttl") or 300)

    if not server or not zone:
        return {
            "record_name": record_name, "record_type": record_type,
            "value": value, "ttl": ttl, "zone": zone or "mock.local",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "skipped", "reason": "no_dns_credentials",
        }

    rdtype = dns.rdatatype.from_text(record_type)
    update = make_update(zone, creds)
    # Prereq: record must not already exist (idempotency guard)
    update.absent(record_name, rdtype)
    update.add(record_name, ttl, rdtype, value)
    send_update(update, server, port)

    return {
        "record_name": record_name,
        "record_type": record_type,
        "value": value,
        "ttl": ttl,
        "zone": zone,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback a create by deleting the record that was just added."""
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = execution_result.get("zone") or _resolve_zone(parameters, creds)
    record_name = execution_result.get("record_name") or parameters.get("record_name", "")
    record_type = execution_result.get("record_type") or parameters.get("record_type", "A")

    if not server or not zone:
        return {"rolled_back": False, "reason": "no_dns_credentials"}

    rdtype = dns.rdatatype.from_text(record_type.upper())
    update = make_update(zone, creds)
    update.delete(record_name, rdtype)
    send_update(update, server, port)
    return {"rolled_back": True, "record_name": record_name, "record_type": record_type, "zone": zone}
