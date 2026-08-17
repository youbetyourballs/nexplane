# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Platform feature smoke tests.

Exercises core lifecycle flows for platform features that require no cloud
infrastructure: Runbooks, Access Reviews/Campaigns, Projects, Maintenance
Windows, Vulnerability Pipeline, and Compliance.

All tests run against the live database inside the Docker container.
Each test creates a fresh asyncpg connection via NullPool to avoid
event-loop conflicts between function-scoped pytest-asyncio tests.
"""

import hashlib
import hmac
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
_WEBHOOK_SECRET = "changeme"
_DB_URL = "postgresql+asyncpg://nexplane:nexplane_dev@db:5432/nexplane"


@asynccontextmanager
async def fresh_db():
    """Create a per-test async session that doesn't share the global connection pool."""
    engine = create_async_engine(_DB_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Runbook lifecycle
# ---------------------------------------------------------------------------

async def test_runbook_create_trigger_abort():
    from app.services.runbook_service import RunbookService
    from app.schemas.runbook import RunbookCreate, RunbookStepCreate
    from app.models.runbook import RunbookExecution

    async with fresh_db() as db:
        svc = RunbookService(db)

        rb = await svc.create_runbook(
            _ORG_ID,
            _USER_ID,
            RunbookCreate(
                name=f"smoke-rb-{uuid.uuid4().hex[:8]}",
                description="Platform feature smoke test",
                auto_execute=True,
                steps=[
                    RunbookStepCreate(
                        step_number=1,
                        name="smoke-checkpoint",
                        type="human_checkpoint",
                        prompt="Waiting for smoke approval",
                        timeout_hours=1,
                        on_timeout="abort",
                        on_failure="abort",
                    )
                ],
            ),
        )
        assert rb.id is not None
        assert rb.name.startswith("smoke-rb-")
        rb_id = rb.id

        # Trigger — checkpoint step pauses immediately at waiting_human
        exc = await svc.trigger_runbook(str(rb_id), _ORG_ID, _USER_ID, {})
        assert exc.status in ("running", "waiting_human", "pending")
        exc_id = exc.id

        # Abort
        aborted = await svc.abort_execution(exc_id, _ORG_ID)
        assert aborted.status == "aborted"

        # Verify execution record persisted
        row = await db.execute(
            select(RunbookExecution).where(RunbookExecution.id == exc_id)
        )
        persisted = row.scalar_one_or_none()
        assert persisted is not None
        assert persisted.status == "aborted"

        # Cleanup
        await svc.delete_runbook(str(rb_id), _ORG_ID)


# ---------------------------------------------------------------------------
# Review campaigns lifecycle
# ---------------------------------------------------------------------------

async def test_review_campaign_create_and_cancel():
    from app.models.review_campaign import ReviewCampaign

    async with fresh_db() as db:
        campaign = ReviewCampaign(
            organization_id=_ORG_ID,
            created_by=_USER_ID,
            title=f"smoke-campaign-{uuid.uuid4().hex[:8]}",
            campaign_type="access_review",
            scope={"connector_types": []},
            reviewer_assignment_rule="security_team",
            evidence_options={"include_last_login": True, "inactive_days_threshold": 90},
            status="draft",
        )
        db.add(campaign)
        await db.flush()
        camp_id = campaign.id
        assert camp_id is not None

        # Verify status = draft
        row = await db.execute(
            select(ReviewCampaign).where(ReviewCampaign.id == camp_id)
        )
        fetched = row.scalar_one()
        assert fetched.status == "draft"

        # Cancel
        fetched.status = "cancelled"
        await db.flush()

        row2 = await db.execute(
            select(ReviewCampaign).where(ReviewCampaign.id == camp_id)
        )
        assert row2.scalar_one().status == "cancelled"

        # Cleanup
        await db.execute(delete(ReviewCampaign).where(ReviewCampaign.id == camp_id))
        await db.commit()


# ---------------------------------------------------------------------------
# Projects lifecycle
# ---------------------------------------------------------------------------

async def test_project_create_with_success_criteria():
    from app.models.project import Project
    from app.models.project_success_criteria import ProjectSuccessCriteria

    async with fresh_db() as db:
        project = Project(
            organization_id=_ORG_ID,
            created_by=_USER_ID,
            name=f"smoke-project-{uuid.uuid4().hex[:8]}",
            description="Platform smoke test project",
            goal="Verify platform feature smoke coverage",
            status="draft",
        )
        db.add(project)
        await db.flush()
        proj_id = project.id
        assert proj_id is not None

        # Add success criterion
        criterion = ProjectSuccessCriteria(
            project_id=proj_id,
            type="manual",
            description="Smoke test criterion",
            assertion="Smoke test passes",
        )
        db.add(criterion)
        await db.flush()

        # Verify criterion linked to project
        row = await db.execute(
            select(ProjectSuccessCriteria).where(
                ProjectSuccessCriteria.project_id == proj_id
            )
        )
        criteria = row.scalars().all()
        assert len(criteria) == 1
        assert criteria[0].type == "manual"
        assert criteria[0].last_result == "not_checked"

        # Verify project status = draft
        p_row = await db.execute(select(Project).where(Project.id == proj_id))
        fetched = p_row.scalar_one()
        assert fetched.status == "draft"

        # Cleanup
        await db.execute(
            delete(ProjectSuccessCriteria).where(
                ProjectSuccessCriteria.project_id == proj_id
            )
        )
        await db.execute(delete(Project).where(Project.id == proj_id))
        await db.commit()


# ---------------------------------------------------------------------------
# Maintenance windows
# ---------------------------------------------------------------------------

async def test_maintenance_window_crud_and_status():
    from app.models.maintenance_window import MaintenanceWindow

    async with fresh_db() as db:
        win = MaintenanceWindow(
            organization_id=_ORG_ID,
            name=f"smoke-window-{uuid.uuid4().hex[:8]}",
            cron_schedule="0 3 * * 0",
            duration_minutes=120,
            applies_to_tags=None,
            enabled=True,
            enforcement="advisory",
        )
        db.add(win)
        await db.flush()
        win_id = win.id
        assert win_id is not None

        # Read back and verify fields
        row = await db.execute(
            select(MaintenanceWindow).where(MaintenanceWindow.id == win_id)
        )
        fetched = row.scalar_one()
        assert fetched.cron_schedule == "0 3 * * 0"
        assert fetched.duration_minutes == 120
        assert fetched.enforcement == "advisory"
        assert fetched.enabled is True

        # Disable
        fetched.enabled = False
        await db.flush()
        row2 = await db.execute(
            select(MaintenanceWindow).where(MaintenanceWindow.id == win_id)
        )
        assert row2.scalar_one().enabled is False

        # Cleanup
        await db.execute(
            delete(MaintenanceWindow).where(MaintenanceWindow.id == win_id)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Change freeze window (compliance)
# ---------------------------------------------------------------------------

async def test_change_freeze_window_lifecycle():
    from app.models.compliance import ChangeFreezeWindow

    now = datetime.now(timezone.utc)
    async with fresh_db() as db:
        freeze = ChangeFreezeWindow(
            reason="Smoke test freeze window",
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=3),
            emergency_bypass_role="admin",
            created_by=_USER_ID,
        )
        db.add(freeze)
        await db.flush()
        freeze_id = freeze.id
        assert freeze_id is not None

        row = await db.execute(
            select(ChangeFreezeWindow).where(ChangeFreezeWindow.id == freeze_id)
        )
        fetched = row.scalar_one()
        assert fetched.reason == "Smoke test freeze window"
        assert fetched.start_at > now

        # Cleanup
        await db.execute(
            delete(ChangeFreezeWindow).where(ChangeFreezeWindow.id == freeze_id)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Compliance baseline CRUD + attestation
# ---------------------------------------------------------------------------

async def test_compliance_baseline_and_attestation():
    from app.models.compliance import ComplianceBaseline
    from app.models.compliance_attestation import ComplianceAttestation

    async with fresh_db() as db:
        baseline = ComplianceBaseline(
            organization_id=_ORG_ID,
            name=f"smoke-baseline-{uuid.uuid4().hex[:8]}",
            scope_type="tag",
            scope_value="smoke",
            cis_level=1,
            os_family="ubuntu",
            config={},
            version=1,
            auto_execute=False,
        )
        db.add(baseline)
        await db.flush()
        bl_id = baseline.id
        assert bl_id is not None

        # Attest a CIS control
        attestation = ComplianceAttestation(
            organization_id=_ORG_ID,
            control_id="CIS-1.1",
            attested_by=_USER_ID,
            evidence_description="Smoke test attestation",
            expires_at=datetime.now(timezone.utc) + timedelta(days=90),
        )
        db.add(attestation)
        await db.flush()
        att_id = attestation.id
        assert att_id is not None

        # Verify both rows exist
        bl_row = await db.execute(
            select(ComplianceBaseline).where(ComplianceBaseline.id == bl_id)
        )
        assert bl_row.scalar_one().cis_level == 1

        att_row = await db.execute(
            select(ComplianceAttestation).where(ComplianceAttestation.id == att_id)
        )
        assert att_row.scalar_one().control_id == "CIS-1.1"

        # Cleanup
        await db.execute(
            delete(ComplianceAttestation).where(ComplianceAttestation.id == att_id)
        )
        await db.execute(
            delete(ComplianceBaseline).where(ComplianceBaseline.id == bl_id)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Vulnerability pipeline — webhook ingest + SLA creation
# ---------------------------------------------------------------------------

async def test_vuln_webhook_ingest_and_sla():
    from app.models.vulnerability import VulnerabilityFinding, RemediationSLA
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    cve_id = f"CVE-2024-SMOKE-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "findings": [
            {
                "scanner": "trivy",
                "finding_type": "cve",
                "severity": "critical",
                "cve_id": cve_id,
                "title": f"Platform smoke test finding {cve_id}",
                "affected_package": "smoke-pkg",
                "affected_version": "1.0.0",
                "fixed_version": "1.0.1",
            }
        ]
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(
        _WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/vulnerability/webhooks/vulnerability-findings",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Nexplane-Signature": sig,
                "X-Org-Id": str(_ORG_ID),
            },
        )
    assert resp.status_code in (200, 202), resp.text
    data = resp.json()
    assert data.get("accepted", 0) >= 1

    # Verify finding + SLA persisted
    async with fresh_db() as db:
        f_row = await db.execute(
            select(VulnerabilityFinding).where(
                VulnerabilityFinding.cve_id == cve_id,
                VulnerabilityFinding.organization_id == _ORG_ID,
            )
        )
        finding = f_row.scalar_one_or_none()
        assert finding is not None, f"Finding {cve_id} not persisted"
        assert finding.severity == "critical"
        assert finding.status == "open"

        sla_row = await db.execute(
            select(RemediationSLA).where(RemediationSLA.finding_id == finding.id)
        )
        sla = sla_row.scalar_one_or_none()
        assert sla is not None, "SLA row not created for critical finding"
        assert sla.sla_hours == 72  # critical SLA

        # Cleanup
        await db.execute(
            delete(RemediationSLA).where(RemediationSLA.finding_id == finding.id)
        )
        await db.execute(
            delete(VulnerabilityFinding).where(VulnerabilityFinding.id == finding.id)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# IR Playbook template — create runbook with IR step types
# ---------------------------------------------------------------------------

async def test_ir_playbook_runbook_structure():
    from app.services.runbook_service import RunbookService
    from app.schemas.runbook import RunbookCreate, RunbookStepCreate

    async with fresh_db() as db:
        svc = RunbookService(db)

        rb = await svc.create_runbook(
            _ORG_ID,
            _USER_ID,
            RunbookCreate(
                name=f"smoke-ir-playbook-{uuid.uuid4().hex[:8]}",
                description="Account compromise IR playbook (smoke)",
                tags=["ir", "smoke"],
                auto_execute=True,
                steps=[
                    RunbookStepCreate(
                        step_number=1,
                        name="preserve-evidence",
                        type="human_checkpoint",
                        prompt="Confirm evidence collection complete before isolation",
                        required_role="ir_responder",
                        timeout_hours=2,
                        on_timeout="abort",
                        on_failure="abort",
                    ),
                    RunbookStepCreate(
                        step_number=2,
                        name="notify-ir-team",
                        type="change",
                        change_type="pagerduty_create_incident",
                        parameters={
                            "title": "Account compromise — active response",
                            "severity": "critical",
                        },
                        on_failure="abort",
                    ),
                ],
            ),
        )
        assert rb.id is not None
        assert len(rb.steps) == 2

        step_types = {s.type for s in rb.steps}
        assert "human_checkpoint" in step_types
        assert "change" in step_types

        # Verify evidence-before-remediation ordering is encoded
        checkpoint_step = next(s for s in rb.steps if s.type == "human_checkpoint")
        assert checkpoint_step.step_number == 1

        # Cleanup
        await svc.delete_runbook(str(rb.id), _ORG_ID)
