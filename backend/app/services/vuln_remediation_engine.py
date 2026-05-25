import uuid
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vulnerability import VulnerabilityFinding, RemediationPolicy

logger = logging.getLogger(__name__)

_RESOURCE_TYPE_MAP: dict[str, str] = {
    "s3_public_access":    "s3_block_public_access",
    "security_group_open": "security_group_update",
    "iam_no_mfa":          "iam_enforce_mfa",
}


def _default_action(finding: VulnerabilityFinding) -> str:
    if finding.finding_type == "cve":
        return "patch_packages"
    return _RESOURCE_TYPE_MAP.get(getattr(finding, "resource_type", None) or "", "generic_remediation")


def match_policy(
    finding: VulnerabilityFinding,
    policies: list[RemediationPolicy],
) -> Optional[RemediationPolicy]:
    """Return the highest-priority enabled policy that matches the finding."""
    candidates = [
        p for p in policies
        if p.enabled
        and (p.match_scanner is None or p.match_scanner == finding.scanner)
        and (p.match_finding_type is None or p.match_finding_type == finding.finding_type)
        and (p.match_severity is None or finding.severity in p.match_severity)
        and (p.match_resource_type is None or p.match_resource_type == getattr(finding, "resource_type", None))
    ]
    return max(candidates, key=lambda p: p.priority) if candidates else None


async def ai_generate_plan(finding: VulnerabilityFinding, action_type: str) -> dict:
    """
    Delegate to the existing AI plan generation service.
    Returns a plan dict. Stub for now — replace with real call to ai_service.
    """
    try:
        from app.services.ai_service import generate_change_plan
        context = {
            "finding_type": finding.finding_type,
            "severity": finding.severity,
            "cve_id": getattr(finding, "cve_id", None),
            "title": finding.title,
            "remediation_hint": getattr(finding, "remediation_hint", None),
            "action_type": action_type,
        }
        return await generate_change_plan(context)
    except Exception as e:
        logger.warning(f"AI plan generation failed for finding {getattr(finding, 'id', '?')}: {e}")
        return {"steps": [], "notes": "AI plan generation unavailable"}


async def generate_change_request_for_finding(
    finding: VulnerabilityFinding,
    policy: Optional[RemediationPolicy],
    db: AsyncSession,
):
    """
    Derive change_type from finding + policy, generate an AI plan,
    and create a DRAFT ChangeRequest linked to the finding.
    Always creates status='draft'.
    """
    from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus

    action_type = policy.action_type if policy else _default_action(finding)
    action_params = (policy.action_params or {}) if policy else {}

    cve_suffix = f" ({finding.cve_id})" if getattr(finding, "cve_id", None) else ""
    title = f"Remediate: {finding.title}{cve_suffix}"

    ai_plan = await ai_generate_plan(finding, action_type)

    # Map action_type to a valid ChangeType enum value; fall back to generic_remediation
    try:
        ct = ChangeType(action_type)
    except ValueError:
        ct = ChangeType.generic_remediation

    # We need a requester_id — use a sentinel org-level system UUID derived from org
    # In production this would be a system user; for now use the org's id as a stand-in
    # The ChangeRequest requires requester_id; we'll use the organization_id as a fallback
    # Look up any user in the org
    from sqlalchemy import select
    from app.models.user import User
    user_result = await db.execute(
        select(User).where(User.organization_id == finding.organization_id).limit(1)
    )
    system_user = user_result.scalar_one_or_none()
    if system_user is None:
        # No user in org — look for any admin in the system as a fallback
        any_user = await db.execute(select(User).limit(1))
        system_user = any_user.scalar_one_or_none()
    if system_user is None:
        raise RuntimeError(
            "Cannot generate auto-remediation CR: no users exist in the system"
        )
    requester_id = system_user.id

    cr = ChangeRequest(
        organization_id=finding.organization_id,
        requester_id=requester_id,
        change_type=ct,
        title=title,
        description=getattr(finding, "description", "") or "",
        target_asset_ids=[str(finding.asset_id)] if finding.asset_id else [],
        desired_outcome=action_params,
        status=ChangeRequestStatus.draft,
        source="auto_remediation",
        finding_id=finding.id,
    )
    db.add(cr)
    await db.flush()

    finding.change_request_id = cr.id
    finding.status = "change_request_generated"

    logger.info(f"Generated DRAFT CR {cr.id} for finding {getattr(finding, 'id', '?')} (action={action_type})")
    return cr


async def link_cr_to_finding(
    db: AsyncSession,
    finding_id,
    cr_id,
    role: str,
) -> None:
    """Create a FindingChangeRequest row linking a CR back to its originating finding."""
    from app.models.vulnerability import FindingChangeRequest
    fcr = FindingChangeRequest(finding_id=finding_id, cr_id=cr_id, role=role)
    db.add(fcr)
    await db.flush()


async def escalate_finding_sla(
    db: AsyncSession,
    finding,
    tier: str,
) -> None:
    """
    Override finding SLA to the given tier immediately.

    tier: "emergency" | "escalated" | "warning"
    Updates the RemediationSLA row for this finding.
    """
    from app.models.vulnerability import RemediationSLA
    from sqlalchemy import select
    from datetime import datetime, timezone, timedelta

    tier_hours = {"warning": 168, "escalated": 72, "emergency": 24}
    hours = tier_hours.get(tier, 24)

    result = await db.execute(
        select(RemediationSLA).where(RemediationSLA.finding_id == finding.id)
    )
    sla = result.scalar_one_or_none()
    if sla:
        sla.sla_hours = hours
        sla.due_at = datetime.now(timezone.utc) + timedelta(hours=hours)
        sla.breached = False
