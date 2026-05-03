import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vulnerability import VulnerabilityFinding, RemediationPolicy, RemediationSLA

logger = logging.getLogger(__name__)


async def enforce_slas(db: AsyncSession) -> None:
    """
    1. Find RemediationSLA rows where due_at < now() and breached = false.
    2. Set breached = true.
    3. If finding.status == 'open' and no change_request_id:
       - Auto-generate a DRAFT change request.
    4. Update breach_notified_at.
    """
    from app.services.vuln_remediation_engine import generate_change_request_for_finding, match_policy

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(RemediationSLA).where(
            RemediationSLA.due_at < now,
            RemediationSLA.breached == False,
        )
    )
    overdue_slas = result.scalars().all()

    for sla in overdue_slas:
        sla.breached = True
        sla.breach_notified_at = now

        finding = await db.get(VulnerabilityFinding, sla.finding_id)
        if not finding or finding.status != "open" or finding.change_request_id:
            continue

        policies_result = await db.execute(
            select(RemediationPolicy).where(
                RemediationPolicy.organization_id == sla.organization_id,
                RemediationPolicy.enabled == True,
            ).order_by(RemediationPolicy.priority.desc())
        )
        policies = policies_result.scalars().all()
        matched = match_policy(finding, list(policies))

        try:
            await generate_change_request_for_finding(finding, matched, db)
            logger.info(f"SLA breach: auto-generated CR for finding {finding.id}")
        except Exception as e:
            logger.error(f"SLA breach CR generation failed for finding {finding.id}: {e}")

    await db.commit()
    logger.info(f"SLA enforcement: processed {len(overdue_slas)} overdue SLAs")
