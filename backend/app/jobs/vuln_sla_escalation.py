"""Scheduled job: SLA breach escalation."""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


async def process_sla_breaches(sla_config: dict, org_id: str) -> None:
    from app.database import AsyncSessionLocal
    from app.models.vulnerability import VulnerabilityFinding, RemediationSLA
    from sqlalchemy import select

    escalate_to = sla_config.get("escalate_to_user_id")
    auto_execute = sla_config.get("auto_execute_on_breach", False)
    upgrade_hours = sla_config.get("severity_upgrade_hours", 48)
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RemediationSLA).where(
                RemediationSLA.organization_id == uuid.UUID(org_id),
                RemediationSLA.due_at <= now,
                RemediationSLA.breached == False,
            )
        )
        overdue = result.scalars().all()
        for sla in overdue:
            finding = await db.get(VulnerabilityFinding, sla.finding_id)
            if not finding or finding.status not in ("open", "change_request_generated"):
                continue
            sla.breached = True
            sla.breach_notified_at = now
            if escalate_to and not finding.assigned_to_user_id:
                finding.assigned_to_user_id = uuid.UUID(escalate_to)
                sla.escalated_at = now
            if auto_execute and finding.change_request_id:
                try:
                    from app.models.change_request import ChangeRequest, ChangeRequestStatus
                    cr = await db.get(ChangeRequest, finding.change_request_id)
                    if cr and cr.status == ChangeRequestStatus.awaiting_approval:
                        cr.status = ChangeRequestStatus.approved
                except Exception as e:
                    logger.warning(f"Auto-execute failed for finding {finding.id}: {e}")
        await db.commit()

    # Severity upgrade for long-breached findings
    cutoff = now - timedelta(hours=upgrade_hours)
    _UPGRADE = {"medium": "high", "high": "critical"}
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RemediationSLA).where(
                RemediationSLA.organization_id == uuid.UUID(org_id),
                RemediationSLA.breached == True,
                RemediationSLA.breach_notified_at <= cutoff,
                RemediationSLA.escalated_at == None,
            )
        )
        for sla in result.scalars().all():
            finding = await db.get(VulnerabilityFinding, sla.finding_id)
            if finding and finding.severity in _UPGRADE:
                finding.severity = _UPGRADE[finding.severity]
                sla.escalated_at = now
        await db.commit()


async def run_sla_escalation_for_all_orgs() -> None:
    from app.database import AsyncSessionLocal
    from app.models.org_settings import OrganizationSettings
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(OrganizationSettings))
        all_settings = result.scalars().all()

    for settings in all_settings:
        config = settings.sla_config or {}
        cfg = {"critical": 72, "high": 168, "medium": 720, "escalate_to_user_id": None,
               "auto_execute_on_breach": False, "severity_upgrade_hours": 48}
        cfg.update({k: v for k, v in config.items() if v is not None})
        try:
            await process_sla_breaches(cfg, str(settings.organization_id))
        except Exception as e:
            logger.error(f"SLA escalation failed for org {settings.organization_id}: {e}")
