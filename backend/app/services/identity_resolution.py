# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Identity resolution service.

Given a target email, queries the assets table across all connectors in the
tenant to find matching user accounts. Each connector type stores the email
in a different JSONB metadata key; this module encodes that mapping.
"""
import uuid
from dataclasses import dataclass
from sqlalchemy import select, or_, cast, String, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.connector import Connector


# Maps connector_type -> list of JSONB path expressions for the email field.
_EMAIL_PATHS: dict[str, list[str]] = {
    "active_directory": [
        "metadata->>'mail'",
        "metadata->>'userPrincipalName'",
    ],
    "okta": [
        "metadata->>'login'",
        "metadata->>'email'",
    ],
    "entra_id": [
        "metadata->>'userPrincipalName'",
        "metadata->>'mail'",
    ],
    "google_workspace": [
        "metadata->>'primaryEmail'",
    ],
    "github": [
        "metadata->>'email'",
    ],
    "slack": [
        "metadata->'profile'->>'email'",
    ],
    "crowdstrike": [
        "metadata->>'last_logged_in_user'",
    ],
}


def _email_filter_for_connector(connector_type: str, email: str):
    """Return a SQLAlchemy text clause that matches the email for the given connector type,
    or None if the connector type is unknown."""
    paths = _EMAIL_PATHS.get(connector_type)
    if not paths:
        return None
    clauses = [text(f"({path} = :email)") for path in paths]
    return or_(*clauses)


async def resolve_user_across_connectors(
    db: AsyncSession,
    target_email: str,
    organization_id: uuid.UUID,
) -> list[dict]:
    """
    Returns a list of dicts with keys:
      connector_id, connector_type, asset_id, display_name, account_status
    for every connector in the organization that has an asset matching target_email.
    """
    # Fetch all active connectors for the org
    conn_result = await db.execute(
        select(Connector).where(Connector.organization_id == organization_id)
    )
    connectors = conn_result.scalars().all()

    results = []
    for connector in connectors:
        filter_clause = _email_filter_for_connector(connector.connector_type, target_email)
        if filter_clause is None:
            continue
        asset_result = await db.execute(
            select(Asset).where(
                Asset.connector_id == connector.id,
                Asset.organization_id == organization_id,
                filter_clause.bindparams(email=target_email),
            )
        )
        assets = asset_result.scalars().all()
        for asset in assets:
            results.append({
                "connector_id": connector.id,
                "connector_type": connector.connector_type,
                "asset_id": asset.id,
                "display_name": asset.name,
                "account_status": asset.asset_metadata.get("status", "unknown"),
            })

    return results


# ---------------------------------------------------------------------------
# 4-tier consumer identity resolution (Task 2: Reference Scan)
# ---------------------------------------------------------------------------

@dataclass
class IdentityResolutionResult:
    asset_id: uuid.UUID | None
    tier: int
    confidence: float
    new_asset_data: dict | None = None


async def resolve_consumer_identity(
    db: AsyncSession,
    organization_id: uuid.UUID,
    consumer_identity: dict,
) -> IdentityResolutionResult:
    """
    4-tier identity resolution for scan hit consumers.

    Tier 1: stable cloud ID (ARN, resource ID) matches asset_metadata JSON.
    Tier 2: hostname or DNS name matches asset name or asset_metadata.
    Tier 3: composite fingerprint — match on >=2 signals from surface_metadata.
    Tier 4: no match — return new_asset_data for auto-registration.
    """
    stable_id = consumer_identity.get("stable_id")
    hostname = consumer_identity.get("hostname")
    surface_meta = consumer_identity.get("surface_metadata") or {}

    base_q = select(Asset).where(Asset.organization_id == organization_id)

    # Tier 1: stable cloud ID in asset_metadata (arn, resource_id, instance_id, etc.)
    if stable_id:
        for field in ("arn", "resource_id", "instance_id", "function_name", "cluster_id"):
            q = base_q.where(
                cast(Asset.asset_metadata[field], String) == f'"{stable_id}"'
            )
            r = await db.execute(q)
            asset = r.scalar_one_or_none()
            if asset:
                return IdentityResolutionResult(asset_id=asset.id, tier=1, confidence=0.99)

    # Tier 2: hostname / DNS name match on asset.name
    if hostname:
        q = base_q.where(
            or_(
                Asset.name == hostname,
                Asset.name == hostname.split(".")[0],
            )
        )
        r = await db.execute(q)
        assets = r.scalars().all()
        if len(assets) == 1:
            return IdentityResolutionResult(asset_id=assets[0].id, tier=2, confidence=0.85)
        if len(assets) > 1:
            # Ambiguous — pick closest and flag lower confidence
            return IdentityResolutionResult(asset_id=assets[0].id, tier=2, confidence=0.50)

    # Tier 3: composite fingerprint — >=2 signals from surface_metadata must match
    # the *same* asset. Collect per-asset match counts, then pick the best.
    from collections import Counter
    import uuid as _uuid_mod
    mac = surface_meta.get("mac_address")
    os_type = surface_meta.get("os_type")
    iface = surface_meta.get("primary_interface_ip")
    signal_matches: Counter = Counter()
    candidates: dict = {}
    for signal_field, signal_val in [
        ("mac_address", mac),
        ("os_type", os_type),
        ("primary_interface_ip", iface),
    ]:
        if not signal_val:
            continue
        q = base_q.where(
            cast(Asset.asset_metadata[signal_field], String) == f'"{signal_val}"'
        )
        r = await db.execute(q)
        a = r.scalar_one_or_none()
        if a:
            signal_matches[str(a.id)] += 1
            candidates[str(a.id)] = a
    best_id = signal_matches.most_common(1)[0][0] if signal_matches else None
    if best_id and signal_matches[best_id] >= 2:
        return IdentityResolutionResult(asset_id=candidates[best_id].id, tier=3, confidence=0.70)

    # Tier 4: no match — build new asset data for auto-registration
    name = hostname or stable_id or surface_meta.get("label") or f"unknown-consumer-{str(_uuid_mod.uuid4())[:8]}"
    new_asset_data = {
        "name": name,
        "asset_type": "application",
        "environment": "unknown",
        "asset_metadata": {
            "discovered_via": "reference_scan",
            "stable_id": stable_id,
            "hostname": hostname,
            **surface_meta,
        },
    }
    return IdentityResolutionResult(asset_id=None, tier=4, confidence=0.0, new_asset_data=new_asset_data)
