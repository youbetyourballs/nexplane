import uuid
from typing import Optional
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.schemas.vulnerability import AffectedAsset


async def match_asset(
    db: AsyncSession,
    organization_id: uuid.UUID,
    ip: Optional[str],
    hostname: Optional[str],
) -> Optional[uuid.UUID]:
    """
    Return the asset_id of the best matching asset for the given IP/hostname.
    IP takes precedence over hostname. Returns None if no match.
    Asset IP and hostname are stored in asset_metadata JSON under keys
    'ip_address' and 'hostname'.
    """
    if ip:
        result = await db.execute(
            select(Asset.id).where(
                Asset.organization_id == organization_id,
                Asset.asset_metadata["ip_address"].astext == ip,
            ).limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            return row

    if hostname:
        result = await db.execute(
            select(Asset.id).where(
                Asset.organization_id == organization_id,
                Asset.asset_metadata["hostname"].astext == hostname,
            ).limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            return row

    return None


async def blast_radius_query(
    db: AsyncSession,
    organization_id: uuid.UUID,
    cve_id: str,
    package: Optional[str] = None,
    affected_version: Optional[str] = None,
) -> tuple[list[AffectedAsset], Optional[str]]:
    """
    Query asset_metadata JSONB for assets running the vulnerable package/version.
    If package/affected_version are not supplied, look them up from VulnerabilityFinding rows.
    Returns (affected_assets, known_fixed_version).
    """
    from app.models.vulnerability import VulnerabilityFinding

    fixed_version: Optional[str] = None

    if not package or not affected_version:
        finding_result = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.organization_id == organization_id,
                VulnerabilityFinding.cve_id == cve_id,
                VulnerabilityFinding.affected_package.isnot(None),
            ).order_by(VulnerabilityFinding.ingested_at.desc()).limit(1)
        )
        finding = finding_result.scalar_one_or_none()
        if not finding:
            return [], None
        package = finding.affected_package
        affected_version = finding.affected_version
        fixed_version = finding.fixed_version

    if not package or not affected_version:
        return [], fixed_version

    # Query assets that have this package/version in their software metadata
    # asset_metadata->>'software' is a JSON array
    # each element: {"package": "openssl", "installed_version": "3.0.2", "os": "Ubuntu 22.04"}
    # Use Python-side filtering since SQLite doesn't support jsonb_array_elements
    all_assets_result = await db.execute(
        select(Asset).where(Asset.organization_id == organization_id)
    )
    all_assets = all_assets_result.scalars().all()

    affected = []
    for asset in all_assets:
        metadata = asset.asset_metadata or {}
        software_list = metadata.get("software", [])
        for sw in software_list:
            if (
                sw.get("package") == package
                and sw.get("installed_version") == affected_version
            ):
                affected.append(AffectedAsset(
                    asset_id=asset.id,
                    hostname=metadata.get("hostname"),
                    ip_address=metadata.get("ip_address"),
                    package=sw.get("package"),
                    installed_version=sw.get("installed_version"),
                    os=sw.get("os"),
                ))
                break

    return affected, fixed_version
