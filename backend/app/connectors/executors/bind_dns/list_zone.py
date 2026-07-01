# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""List all records in a DNS zone via AXFR zone transfer."""
from __future__ import annotations
import dns.query
import dns.rdatatype
import dns.zone

from ._client import _resolve_zone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = _resolve_zone(parameters, creds)

    if not server or not zone:
        # No credentials — return mock so the executor can be called in tests without infra.
        return {"zone": "mock.local", "records": [], "count": 0}

    zone_obj = dns.zone.from_xfr(
        dns.query.xfr(server, zone, port=port, timeout=30)
    )
    records: list[dict] = []
    for name, node in zone_obj.nodes.items():
        for rdataset in node.rdatasets:
            for rdata in rdataset:
                records.append({
                    "name": str(name),
                    "type": dns.rdatatype.to_text(rdataset.rdtype),
                    "value": str(rdata),
                    "ttl": rdataset.ttl,
                })
    return {"zone": zone, "records": records, "count": len(records)}
