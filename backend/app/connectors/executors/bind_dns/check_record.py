"""Query a specific DNS record and return its current value(s)."""
from __future__ import annotations

import dns.rdatatype
import dns.resolver

from ._client import _resolve_zone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds: dict = getattr(connector, "credentials", None) or {}
    server = creds.get("server", "").strip()
    port = int(creds.get("port", 53) or 53)
    zone = _resolve_zone(parameters, creds)

    record_name: str = parameters["record_name"].strip()
    record_type: str = (parameters.get("record_type") or "A").strip().upper()

    if not server:
        return {
            "record_name": record_name, "record_type": record_type,
            "values": [], "ttl": None, "exists": False,
            "status": "skipped", "reason": "no_dns_credentials",
        }

    fqdn = record_name if record_name.endswith(".") else (
        f"{record_name}.{zone}" if zone else record_name
    )

    resolver = dns.resolver.Resolver()
    resolver.nameservers = [server]
    resolver.port = port

    try:
        answers = resolver.resolve(fqdn, record_type)
        return {
            "record_name": record_name,
            "record_type": record_type,
            "values": [str(r) for r in answers],
            "ttl": answers.rrset.ttl if answers.rrset else None,
            "exists": True,
        }
    except dns.resolver.NXDOMAIN:
        return {
            "record_name": record_name,
            "record_type": record_type,
            "values": [],
            "ttl": None,
            "exists": False,
        }
    except dns.resolver.NoAnswer:
        return {
            "record_name": record_name,
            "record_type": record_type,
            "values": [],
            "ttl": None,
            "exists": False,
        }
