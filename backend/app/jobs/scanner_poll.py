# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vulnerability import VulnerabilityFinding, RemediationSLA, SLA_HOURS

logger = logging.getLogger(__name__)


async def fetch_crowdstrike_findings(org_id: uuid.UUID, credential: dict) -> list[dict]:
    """
    Call CrowdStrike Spotlight API: GET /spotlight/queries/vulnerabilities/v1
    Returns a list of normalized finding dicts.
    Stub implementation — replace with real API client.
    TODO: implement CrowdStrike Spotlight API client using credential["api_key"]
    and credential["base_url"]. Paginate using after cursor.
    """
    return []


async def poll_crowdstrike(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Pull vulnerability findings from CrowdStrike Spotlight for one org."""
    from app.models.connector_credential import ConnectorCredential
    from sqlalchemy import select

    cred_result = await db.execute(
        select(ConnectorCredential).where(
            ConnectorCredential.organization_id == org_id,
        )
    )
    cred = cred_result.scalar_one_or_none()
    if not cred:
        logger.debug(f"No CrowdStrike credential for org {org_id}, skipping")
        return

    raw_findings = await fetch_crowdstrike_findings(org_id, {})

    for raw in raw_findings:
        scanner_finding_id = raw.get("id")
        from sqlalchemy import select as sa_select
        existing = await db.execute(
            sa_select(VulnerabilityFinding).where(
                VulnerabilityFinding.organization_id == org_id,
                VulnerabilityFinding.scanner == "crowdstrike",
                VulnerabilityFinding.scanner_finding_id == scanner_finding_id,
            )
        )
        if existing.scalar_one_or_none():
            continue

        severity = raw.get("severity", "medium").lower()
        finding = VulnerabilityFinding(
            organization_id=org_id,
            scanner="crowdstrike",
            scanner_finding_id=scanner_finding_id,
            source="poll",
            finding_type=raw.get("finding_type", "cve"),
            severity=severity,
            cve_id=raw.get("cve_id"),
            title=raw.get("title", "CrowdStrike Finding"),
            description=raw.get("description"),
            affected_package=raw.get("affected_package"),
            affected_version=raw.get("affected_version"),
            fixed_version=raw.get("fixed_version"),
            target_ip=raw.get("target_ip"),
            target_hostname=raw.get("target_hostname"),
            raw_payload=raw,
        )
        db.add(finding)
        await db.flush()

        sla_hours = SLA_HOURS.get(severity)
        if sla_hours:
            sla = RemediationSLA(
                organization_id=org_id,
                finding_id=finding.id,
                severity=severity,
                sla_hours=sla_hours,
                due_at=datetime.now(timezone.utc) + timedelta(hours=sla_hours),
            )
            db.add(sla)

    await db.commit()
    logger.info(f"CrowdStrike poll complete for org {org_id}: ingested {len(raw_findings)} findings")
