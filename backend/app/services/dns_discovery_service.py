# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
DNS Discovery Service — find all DNS A/AAAA records pointing to a given asset's
current IP addresses by cross-referencing:
  1. asset_metadata.dns_names[]  (populated by DNS connector ingest)
  2. dns_zone assets whose asset_metadata.records[] reference the asset's IPs
  3. asset.name if it looks like an FQDN (contains at least one dot)
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def discover_dns_records_for_asset(
    db: "AsyncSession",
    asset_id: str,
) -> list[dict]:
    """Find all DNS A/AAAA records pointing to an asset's current IPs.

    Returns a list of record dicts:
    {
        "name":         str,   # e.g. "api.corp.example.com"
        "type":         str,   # "A" or "AAAA"
        "value":        str,   # the IP address the record currently resolves to
        "ttl":          int,   # record TTL in seconds
        "provider":     str,   # "route53", "azure_dns", "cloudflare", etc.
        "connector_id": str,   # UUID of the Nexplane connector that manages this zone
        "zone_id":      str,   # provider-specific zone/hosted-zone identifier
    }
    """
    import uuid as _uuid
    from sqlalchemy import select
    from app.models.asset import Asset, AssetType

    asset_uuid = _uuid.UUID(asset_id) if isinstance(asset_id, str) else asset_id

    # Load the target asset
    asset = await db.get(Asset, asset_uuid)
    if asset is None:
        return []

    meta = asset.asset_metadata or {}

    # Collect the asset's current IP addresses
    current_ips: set[str] = set()
    for ip in meta.get("ip_addresses", []):
        # Strip CIDR prefix if present (e.g. "10.0.0.100/24" -> "10.0.0.100")
        current_ips.add(ip.split("/")[0])
    if not current_ips:
        return []

    records: list[dict] = []
    seen: set[tuple] = set()  # (name, type, value) dedup key

    # -----------------------------------------------------------------------
    # Source 1: asset_metadata.dns_names[] — records already linked to asset
    # -----------------------------------------------------------------------
    for dns_name in meta.get("dns_names", []):
        _add_record(records, seen, {
            "name": dns_name,
            "type": "A",
            "value": next(iter(current_ips)),  # best-guess; caller can verify
            "ttl": meta.get("dns_ttl", 3600),
            "provider": meta.get("dns_provider", "unknown"),
            "connector_id": str(meta.get("dns_connector_id", "")),
            "zone_id": meta.get("dns_zone_id", ""),
        })

    # -----------------------------------------------------------------------
    # Source 2: dns_zone assets whose records[] reference the asset's IPs
    # -----------------------------------------------------------------------
    dns_zone_result = await db.execute(
        select(Asset).where(
            Asset.organization_id == asset.organization_id,
            Asset.asset_type == AssetType.dns_zone,
        )
    )
    dns_zone_assets = dns_zone_result.scalars().all()

    for zone_asset in dns_zone_assets:
        zone_meta = zone_asset.asset_metadata or {}
        zone_records = zone_meta.get("records", [])
        connector_id = str(zone_asset.connector_id or "")
        zone_id = zone_meta.get("zone_id", str(zone_asset.id))
        provider = zone_meta.get("provider", "unknown")

        for rec in zone_records:
            rec_type = rec.get("type", "").upper()
            if rec_type not in ("A", "AAAA"):
                continue
            rec_value = rec.get("value", "").split("/")[0]
            if rec_value not in current_ips:
                continue
            _add_record(records, seen, {
                "name": rec.get("name", ""),
                "type": rec_type,
                "value": rec_value,
                "ttl": int(rec.get("ttl", 3600)),
                "provider": provider,
                "connector_id": connector_id,
                "zone_id": zone_id,
            })

    # -----------------------------------------------------------------------
    # Source 3: asset.name if it looks like an FQDN
    # -----------------------------------------------------------------------
    if "." in asset.name:
        for ip in current_ips:
            _add_record(records, seen, {
                "name": asset.name,
                "type": "A" if ":" not in ip else "AAAA",
                "value": ip,
                "ttl": 3600,
                "provider": "unknown",
                "connector_id": "",
                "zone_id": "",
            })

    return records


def _add_record(
    records: list[dict],
    seen: set[tuple],
    rec: dict,
) -> None:
    key = (rec["name"], rec["type"], rec["value"])
    if key not in seen:
        seen.add(key)
        records.append(rec)
