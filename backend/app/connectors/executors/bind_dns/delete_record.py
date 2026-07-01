# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Delete a DNS record via RFC 2136 dynamic update."""
from __future__ import annotations
from datetime import datetime, timezone

import dns.rdatatype
import dns.resolver

from ._client import _resolve_zone, make_update, send_update


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = _resolve_zone(parameters, creds)

    record_name: str = parameters["record_name"].strip()
    record_type: str = parameters["record_type"].strip().upper()

    if not server or not zone:
        return {
            "record_name": record_name, "record_type": record_type,
            "zone": zone or "mock.local", "previous_value": None,
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "status": "skipped", "reason": "no_dns_credentials",
        }

    # Query current value before deleting (needed for rollback re-creation).
    fqdn = record_name if record_name.endswith(".") else f"{record_name}.{zone}"
    previous_value: str | None = None
    try:
        resolver = dns.resolver.Resolver()
        resolver.nameservers = [server]
        resolver.port = port
        answers = resolver.resolve(fqdn, record_type)
        previous_value = str(answers[0])
    except dns.resolver.NXDOMAIN:
        pass
    except Exception:
        pass

    rdtype = dns.rdatatype.from_text(record_type)
    update = make_update(zone, creds)
    update.delete(record_name, rdtype)
    send_update(update, server, port)

    return {
        "record_name": record_name,
        "record_type": record_type,
        "previous_value": previous_value,
        "zone": zone,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback a delete by re-creating the record with the captured previous value."""
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = execution_result.get("zone") or _resolve_zone(parameters, creds)
    record_name = execution_result.get("record_name") or parameters.get("record_name", "")
    record_type = execution_result.get("record_type") or parameters.get("record_type", "A")
    previous_value = execution_result.get("previous_value")

    if not server or not zone:
        return {"rolled_back": False, "reason": "no_dns_credentials"}
    if not previous_value:
        return {"rolled_back": False, "reason": "no_previous_value_captured"}

    rdtype = dns.rdatatype.from_text(record_type.upper())
    update = make_update(zone, creds)
    update.add(record_name, 300, rdtype, previous_value)
    send_update(update, server, port)
    return {
        "rolled_back": True,
        "record_name": record_name,
        "record_type": record_type,
        "value": previous_value,
        "zone": zone,
    }
