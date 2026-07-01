# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vulnerability import VulnerabilityFinding, RemediationPolicy, RemediationSLA

logger = logging.getLogger(__name__)

# Hours after breach before escalating (per severity)
ESCALATION_HOURS: dict[str, int] = {
    "critical": 4,
    "high": 24,
    "medium": 72,
}


async def enforce_slas(db: AsyncSession) -> None:
    """
    1. Find RemediationSLA rows where due_at < now() and breached = False → mark as breached.
    2. Find breached SLAs where escalated_at is None and breach has exceeded threshold → escalate.
    """
    from app.services.vuln_remediation_engine import generate_change_request_for_finding, match_policy

    now = datetime.now(timezone.utc)

    # Step 1: Mark new breaches
    result = await db.execute(
        select(RemediationSLA).where(
            RemediationSLA.due_at < now,
            RemediationSLA.breached == False,
        )
    )
    overdue_slas = result.scalars().all()

    newly_breached = 0
    for sla in overdue_slas:
        sla.breached = True
        sla.breach_notified_at = now
        newly_breached += 1

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

    # Step 2: Escalate findings that have been breached past their threshold
    breached_result = await db.execute(
        select(RemediationSLA).where(
            RemediationSLA.breached == True,
            RemediationSLA.escalated_at == None,  # noqa: E711
        )
    )
    breached_slas = breached_result.scalars().all()

    newly_escalated = 0
    for sla in breached_slas:
        threshold_hours = ESCALATION_HOURS.get(sla.severity)
        if threshold_hours is None:
            continue
        # due_at is when the breach started; escalate if now > due_at + threshold
        escalate_after = sla.due_at + timedelta(hours=threshold_hours)
        if now >= escalate_after:
            sla.escalated_at = now
            newly_escalated += 1
            logger.info(
                f"SLA escalated: finding {sla.finding_id} severity={sla.severity} "
                f"breach_age={(now - sla.due_at).total_seconds() / 3600:.1f}h"
            )

    await db.commit()
    logger.info(
        f"SLA enforcement: {newly_breached} newly breached, {newly_escalated} escalated"
    )
