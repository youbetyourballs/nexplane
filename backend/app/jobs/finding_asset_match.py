import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vulnerability import VulnerabilityFinding

logger = logging.getLogger(__name__)


async def retry_asset_matching(db: AsyncSession) -> None:
    """Retry matching for VulnerabilityFinding rows where asset_id is NULL."""
    from app.services.vuln_asset_matcher import match_asset

    result = await db.execute(
        select(VulnerabilityFinding).where(VulnerabilityFinding.asset_id.is_(None))
    )
    unmatched = result.scalars().all()
    resolved = 0

    for finding in unmatched:
        asset_id = await match_asset(
            db,
            finding.organization_id,
            ip=finding.target_ip,
            hostname=finding.target_hostname,
        )
        if asset_id:
            finding.asset_id = asset_id
            resolved += 1

    await db.commit()
    logger.info(f"Asset re-match: resolved {resolved}/{len(unmatched)} unmatched findings")
