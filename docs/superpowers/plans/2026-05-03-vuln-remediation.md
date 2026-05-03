# Vulnerability Remediation Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the loop between scanner findings (Qualys, Tenable, Wiz, Snyk, CrowdStrike) and automated fixes — ingesting findings via push webhook and pull polling, matching them to Nexplane assets, auto-generating DRAFT change requests per configurable `RemediationPolicy` rules, enforcing SLA timers, and surfacing everything in a new "Pending Remediation" queue in the frontend. No finding auto-approves without an explicit policy rule with `approval_level = "auto"`.

**Architecture:** Three new DB tables (`vulnerability_findings`, `remediation_policies`, `remediation_slas`) feed a new FastAPI router (`/api/v1/vulnerability/*` plus `/webhooks/vulnerability-findings`). An asset-matcher service joins findings to existing `assets` rows by IP/hostname. A remediation engine applies `RemediationPolicy` rules and calls the existing AI plan generation to produce DRAFT `ChangeRequest` rows. Three APScheduler background jobs (scanner poll, SLA enforcement, asset re-match) run on fixed cadences. The frontend gains a Remediation page, CVE Blast Radius component, SLA dashboard widget, and a policy settings panel.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 async ORM / PostgreSQL 16 (JSONB, ARRAY) / Alembic / APScheduler 3.x / React 18 + TanStack Query v5 / TypeScript.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/alembic/versions/XXXX_vulnerability_remediation.py` | Create | Three new tables + two columns on `change_requests` |
| `backend/app/models/vulnerability.py` | Create | `VulnerabilityFinding`, `RemediationPolicy`, `RemediationSLA` SQLAlchemy models |
| `backend/app/schemas/vulnerability.py` | Create | Pydantic schemas for all three models plus webhook payload |
| `backend/app/routers/vulnerability.py` | Create | All `/api/v1/vulnerability/*` and `/webhooks/vulnerability-findings` endpoints |
| `backend/app/services/vuln_asset_matcher.py` | Create | Match finding IP/hostname to `Asset` rows; blast-radius JSONB query |
| `backend/app/services/vuln_remediation_engine.py` | Create | `generate_change_request_for_finding`, `match_policy`, `_default_action` |
| `backend/app/jobs/scanner_poll.py` | Create | CrowdStrike Spotlight pull job (stub for Qualys/Tenable future pull) |
| `backend/app/jobs/sla_enforcement.py` | Create | SLA breach detection, auto-CR generation, notification |
| `backend/app/jobs/finding_asset_match.py` | Create | Retry asset matching for unmatched findings |
| `backend/app/main.py` | Modify | Register vulnerability router; schedule three new jobs |
| `backend/tests/test_vulnerability_models.py` | Create | Model CRUD tests |
| `backend/tests/test_vulnerability_api.py` | Create | Router/endpoint tests |
| `backend/tests/test_vuln_asset_matcher.py` | Create | Asset matcher service tests |
| `backend/tests/test_vuln_remediation_engine.py` | Create | Remediation engine tests |
| `backend/tests/test_vuln_jobs.py` | Create | Background job tests |
| `frontend/src/pages/VulnerabilityRemediation.tsx` | Create | CVE search bar, affected assets list, one-click campaign generation |
| `frontend/src/components/FindingQueue.tsx` | Create | Pending remediation queue with draft CR links |
| `frontend/src/components/SLAWidget.tsx` | Create | SLA status dashboard widget |
| `frontend/src/components/RemediationPolicyEditor.tsx` | Create | Per-severity policy CRUD: auto-generate CR, auto-approve, SLA days |
| `frontend/src/App.tsx` | Modify | Add `/remediation` route |

---

## Task 1: DB Models and Schemas

**Files:**
- Create: `backend/app/models/vulnerability.py`
- Create: `backend/app/schemas/vulnerability.py`
- Create: `backend/alembic/versions/XXXX_vulnerability_remediation.py`
- Create: `backend/tests/test_vulnerability_models.py`

### Step 1.1: Write failing model CRUD tests first

Create `backend/tests/test_vulnerability_models.py`:

```python
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from app.models.vulnerability import VulnerabilityFinding, RemediationPolicy, RemediationSLA


@pytest.mark.asyncio
async def test_create_vulnerability_finding(db_session, test_org, test_asset):
    finding = VulnerabilityFinding(
        organization_id=test_org.id,
        asset_id=test_asset.id,
        scanner="qualys",
        scanner_finding_id="QID-12345",
        source="webhook",
        finding_type="cve",
        severity="critical",
        cve_id="CVE-2024-1234",
        title="OpenSSL Buffer Overflow",
        description="Critical buffer overflow in openssl",
        affected_package="openssl",
        affected_version="3.0.2",
        fixed_version="3.0.7",
        target_ip="10.0.1.50",
        target_hostname="web-prod-01",
    )
    db_session.add(finding)
    await db_session.flush()
    assert finding.id is not None
    assert finding.status == "open"
    assert finding.ingested_at is not None


@pytest.mark.asyncio
async def test_finding_unique_constraint(db_session, test_org):
    """Duplicate (org, scanner, scanner_finding_id) must be rejected."""
    finding_a = VulnerabilityFinding(
        organization_id=test_org.id,
        scanner="qualys",
        scanner_finding_id="QID-DUPE",
        source="webhook",
        finding_type="cve",
        severity="high",
        title="Dupe finding",
    )
    finding_b = VulnerabilityFinding(
        organization_id=test_org.id,
        scanner="qualys",
        scanner_finding_id="QID-DUPE",
        source="webhook",
        finding_type="cve",
        severity="high",
        title="Dupe finding",
    )
    db_session.add(finding_a)
    await db_session.flush()
    db_session.add(finding_b)
    with pytest.raises(Exception):  # IntegrityError from unique constraint
        await db_session.flush()


@pytest.mark.asyncio
async def test_create_remediation_policy(db_session, test_org):
    policy = RemediationPolicy(
        organization_id=test_org.id,
        name="Auto-patch critical CVEs",
        match_finding_type="cve",
        match_severity=["critical", "high"],
        action_type="patch_packages",
        approval_level="require_approval",
        priority=10,
        enabled=True,
    )
    db_session.add(policy)
    await db_session.flush()
    assert policy.id is not None
    assert policy.enabled is True


@pytest.mark.asyncio
async def test_create_remediation_sla(db_session, test_org, test_finding):
    due = datetime.now(timezone.utc) + timedelta(hours=72)
    sla = RemediationSLA(
        organization_id=test_org.id,
        finding_id=test_finding.id,
        severity="critical",
        sla_hours=72,
        due_at=due,
    )
    db_session.add(sla)
    await db_session.flush()
    assert sla.id is not None
    assert sla.breached is False


@pytest.mark.asyncio
async def test_sla_unique_per_finding(db_session, test_org, test_finding):
    """Each finding can have at most one SLA row."""
    due = datetime.now(timezone.utc) + timedelta(hours=72)
    sla_a = RemediationSLA(
        organization_id=test_org.id,
        finding_id=test_finding.id,
        severity="critical",
        sla_hours=72,
        due_at=due,
    )
    sla_b = RemediationSLA(
        organization_id=test_org.id,
        finding_id=test_finding.id,
        severity="critical",
        sla_hours=72,
        due_at=due,
    )
    db_session.add(sla_a)
    await db_session.flush()
    db_session.add(sla_b)
    with pytest.raises(Exception):
        await db_session.flush()
```

- [ ] **Step 1.2: Run tests — expect import/table failure**

```bash
cd backend && python -m pytest tests/test_vulnerability_models.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'VulnerabilityFinding'` or table-not-found.

### Step 1.3: Create the models

Create `backend/app/models/vulnerability.py`:

```python
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, ForeignKey,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

from app.database import Base


class VulnerabilityFinding(Base):
    __tablename__ = "vulnerability_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    asset_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True, index=True)

    # Source
    scanner: Mapped[str] = mapped_column(String, nullable=False)
    scanner_finding_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "webhook" | "poll"

    # Finding details
    finding_type: Mapped[str] = mapped_column(String, nullable=False)  # "cve" | "misconfiguration" | "secret" | "iac"
    severity: Mapped[str] = mapped_column(String, nullable=False, index=True)
    cve_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    remediation_hint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    affected_package: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    affected_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    fixed_version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resource_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Target asset identity (before/during match)
    target_ip: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    target_hostname: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Lifecycle
    status: Mapped[str] = mapped_column(String, nullable=False, default="open", index=True)
    change_request_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    first_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "scanner", "scanner_finding_id", name="uq_finding_scanner_id"),
    )


class RemediationPolicy(Base):
    __tablename__ = "remediation_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    # Matching criteria (None = match any)
    match_scanner: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    match_finding_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    match_severity: Mapped[Optional[list]] = mapped_column(ARRAY(String), nullable=True)
    match_resource_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Action
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    action_params: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Approval gate
    approval_level: Mapped[str] = mapped_column(String, nullable=False, default="require_approval")

    # Priority: higher number wins when multiple rules match
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)


class RemediationSLA(Base):
    __tablename__ = "remediation_slas"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("vulnerability_findings.id"), nullable=False, unique=True)

    severity: Mapped[str] = mapped_column(String, nullable=False)
    sla_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    breached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    breach_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


SLA_HOURS: dict[str, int] = {
    "critical": 72,
    "high": 168,
    "medium": 720,
}
```

### Step 1.4: Create Pydantic schemas

Create `backend/app/schemas/vulnerability.py`:

```python
import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel


# ── VulnerabilityFinding ────────────────────────────────────────────────────

class FindingIngest(BaseModel):
    """Single finding as sent inside the webhook body."""
    scanner_finding_id: Optional[str] = None
    finding_type: str
    severity: str
    cve_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    remediation_hint: Optional[str] = None
    affected_package: Optional[str] = None
    affected_version: Optional[str] = None
    fixed_version: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    target_ip: Optional[str] = None
    target_hostname: Optional[str] = None
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    raw_payload: Optional[dict[str, Any]] = None


class WebhookFindingsPayload(BaseModel):
    scanner: str
    organization_id: uuid.UUID
    findings: list[FindingIngest]


class WebhookAcceptedResponse(BaseModel):
    accepted: int
    duplicates_skipped: int
    queued_for_matching: int


class FindingRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    asset_id: Optional[uuid.UUID]
    scanner: str
    scanner_finding_id: Optional[str]
    source: str
    finding_type: str
    severity: str
    cve_id: Optional[str]
    title: str
    description: Optional[str]
    remediation_hint: Optional[str]
    affected_package: Optional[str]
    affected_version: Optional[str]
    fixed_version: Optional[str]
    resource_type: Optional[str]
    resource_id: Optional[str]
    target_ip: Optional[str]
    target_hostname: Optional[str]
    status: str
    change_request_id: Optional[uuid.UUID]
    ingested_at: datetime
    first_seen_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    # Joined fields (populated by router)
    asset_name: Optional[str] = None
    sla_due_at: Optional[datetime] = None
    sla_breached: Optional[bool] = None


class FindingListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    findings: list[FindingRead]


class FindingStatusUpdate(BaseModel):
    status: str  # "accepted_risk" | "false_positive"
    reason: Optional[str] = None


class GenerateCRRequest(BaseModel):
    action_type: Optional[str] = None
    action_params: Optional[dict[str, Any]] = None
    title_override: Optional[str] = None


# ── RemediationPolicy ───────────────────────────────────────────────────────

class PolicyCreate(BaseModel):
    name: str
    match_scanner: Optional[str] = None
    match_finding_type: Optional[str] = None
    match_severity: Optional[list[str]] = None
    match_resource_type: Optional[str] = None
    action_type: str
    action_params: Optional[dict[str, Any]] = None
    approval_level: str = "require_approval"
    priority: int = 0
    enabled: bool = True


class PolicyUpdate(BaseModel):
    name: Optional[str] = None
    match_scanner: Optional[str] = None
    match_finding_type: Optional[str] = None
    match_severity: Optional[list[str]] = None
    match_resource_type: Optional[str] = None
    action_type: Optional[str] = None
    action_params: Optional[dict[str, Any]] = None
    approval_level: Optional[str] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None


class PolicyRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    match_scanner: Optional[str]
    match_finding_type: Optional[str]
    match_severity: Optional[list[str]]
    match_resource_type: Optional[str]
    action_type: str
    action_params: Optional[dict[str, Any]]
    approval_level: str
    priority: int
    enabled: bool
    created_at: datetime
    updated_at: Optional[datetime]


# ── SLA Dashboard ───────────────────────────────────────────────────────────

class SLASeverityStats(BaseModel):
    total: int
    overdue: int
    due_soon: int


class SLADashboardResponse(BaseModel):
    critical: SLASeverityStats
    high: SLASeverityStats
    medium: SLASeverityStats


# ── Blast Radius ────────────────────────────────────────────────────────────

class AffectedAsset(BaseModel):
    asset_id: uuid.UUID
    hostname: Optional[str]
    ip_address: Optional[str]
    package: Optional[str]
    installed_version: Optional[str]
    os: Optional[str]


class BlastRadiusResponse(BaseModel):
    cve_id: str
    affected_assets: list[AffectedAsset]
    total_affected: int
    known_fixed_version: Optional[str]


class PatchCampaignRequest(BaseModel):
    target_asset_ids: list[uuid.UUID]
    batch_size: int = 10
    rollout_strategy: str = "rolling"
```

### Step 1.5: Create the Alembic migration

```bash
cd backend && alembic revision --autogenerate -m "add_vulnerability_remediation_tables"
```

Then verify and clean up the generated file. The final migration must match:

Create `backend/alembic/versions/XXXX_vulnerability_remediation.py` (replace XXXX with the generated revision ID):

```python
"""Add vulnerability remediation tables

Revision ID: <generated>
Revises: <previous>
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY


def upgrade() -> None:
    op.create_table(
        "vulnerability_findings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("asset_id", UUID(as_uuid=True), sa.ForeignKey("assets.id"), nullable=True),
        sa.Column("scanner", sa.String, nullable=False),
        sa.Column("scanner_finding_id", sa.String, nullable=True),
        sa.Column("source", sa.String, nullable=False),
        sa.Column("finding_type", sa.String, nullable=False),
        sa.Column("severity", sa.String, nullable=False),
        sa.Column("cve_id", sa.String, nullable=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("remediation_hint", sa.Text, nullable=True),
        sa.Column("affected_package", sa.String, nullable=True),
        sa.Column("affected_version", sa.String, nullable=True),
        sa.Column("fixed_version", sa.String, nullable=True),
        sa.Column("resource_type", sa.String, nullable=True),
        sa.Column("resource_id", sa.String, nullable=True),
        sa.Column("target_ip", sa.String, nullable=True),
        sa.Column("target_hostname", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="open"),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload", JSONB, nullable=True),
        sa.UniqueConstraint("organization_id", "scanner", "scanner_finding_id", name="uq_finding_scanner_id"),
    )
    op.create_index("ix_vf_org_id",   "vulnerability_findings", ["organization_id"])
    op.create_index("ix_vf_asset_id", "vulnerability_findings", ["asset_id"])
    op.create_index("ix_vf_severity", "vulnerability_findings", ["severity"])
    op.create_index("ix_vf_status",   "vulnerability_findings", ["status"])

    op.create_table(
        "remediation_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("match_scanner", sa.String, nullable=True),
        sa.Column("match_finding_type", sa.String, nullable=True),
        sa.Column("match_severity", ARRAY(sa.String), nullable=True),
        sa.Column("match_resource_type", sa.String, nullable=True),
        sa.Column("action_type", sa.String, nullable=False),
        sa.Column("action_params", JSONB, nullable=True),
        sa.Column("approval_level", sa.String, nullable=False, server_default="require_approval"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rp_org_id", "remediation_policies", ["organization_id"])

    op.create_table(
        "remediation_slas",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("finding_id", UUID(as_uuid=True), sa.ForeignKey("vulnerability_findings.id"), nullable=False, unique=True),
        sa.Column("severity", sa.String, nullable=False),
        sa.Column("sla_hours", sa.Integer, nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("breached", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("breach_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_rs_org_due", "remediation_slas", ["organization_id", "due_at"])

    op.add_column(
        "change_requests",
        sa.Column("finding_id", UUID(as_uuid=True), sa.ForeignKey("vulnerability_findings.id"), nullable=True),
    )
    op.add_column(
        "change_requests",
        sa.Column("source", sa.String, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("change_requests", "source")
    op.drop_column("change_requests", "finding_id")
    op.drop_table("remediation_slas")
    op.drop_table("remediation_policies")
    op.drop_table("vulnerability_findings")
```

- [ ] **Step 1.6: Run migration**

```bash
cd backend && alembic upgrade head
```

Expected: migration applies cleanly, three new tables exist.

- [ ] **Step 1.7: Run model tests — expect pass**

```bash
cd backend && python -m pytest tests/test_vulnerability_models.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 1.8: Commit**

```bash
git add backend/app/models/vulnerability.py backend/app/schemas/vulnerability.py backend/alembic/versions/ backend/tests/test_vulnerability_models.py
git commit -m "feat(vuln): add VulnerabilityFinding, RemediationPolicy, RemediationSLA models + schemas + migration"
```

---

## Task 2: Vulnerability API Router

**Files:**
- Create: `backend/app/routers/vulnerability.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_vulnerability_api.py`

### Step 2.1: Write failing API tests first

Create `backend/tests/test_vulnerability_api.py`:

```python
import hashlib
import hmac
import json
import uuid
import pytest
from httpx import AsyncClient


WEBHOOK_SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str) -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


@pytest.mark.asyncio
async def test_webhook_requires_valid_hmac(client: AsyncClient, test_org):
    payload = {
        "scanner": "qualys",
        "organization_id": str(test_org.id),
        "findings": [],
    }
    body = json.dumps(payload).encode()
    resp = await client.post(
        "/api/v1/vulnerability/webhooks/vulnerability-findings",
        content=body,
        headers={"X-Nexplane-Signature": "sha256=badhash", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_webhook_accepts_valid_findings(client: AsyncClient, test_org, mock_webhook_secret):
    payload = {
        "scanner": "qualys",
        "organization_id": str(test_org.id),
        "findings": [
            {
                "scanner_finding_id": "QID-99",
                "finding_type": "cve",
                "severity": "critical",
                "cve_id": "CVE-2024-9999",
                "title": "Test CVE",
                "target_ip": "10.0.0.1",
                "target_hostname": "test-host",
            }
        ],
    }
    body = json.dumps(payload).encode()
    sig = _sign(body, WEBHOOK_SECRET)
    resp = await client.post(
        "/api/v1/vulnerability/webhooks/vulnerability-findings",
        content=body,
        headers={"X-Nexplane-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["accepted"] == 1
    assert data["duplicates_skipped"] == 0


@pytest.mark.asyncio
async def test_list_findings_empty(auth_client: AsyncClient):
    resp = await auth_client.get("/api/v1/vulnerability/findings")
    assert resp.status_code == 200
    data = resp.json()
    assert "findings" in data
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_findings_with_severity_filter(auth_client: AsyncClient, test_finding):
    resp = await auth_client.get("/api/v1/vulnerability/findings?severity=critical")
    assert resp.status_code == 200
    data = resp.json()
    assert all(f["severity"] == "critical" for f in data["findings"])


@pytest.mark.asyncio
async def test_get_finding_detail(auth_client: AsyncClient, test_finding):
    resp = await auth_client.get(f"/api/v1/vulnerability/findings/{test_finding.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(test_finding.id)
    assert data["cve_id"] == test_finding.cve_id


@pytest.mark.asyncio
async def test_get_finding_not_found(auth_client: AsyncClient):
    resp = await auth_client.get(f"/api/v1/vulnerability/findings/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_finding_status_accepted_risk(auth_client: AsyncClient, test_finding):
    resp = await auth_client.patch(
        f"/api/v1/vulnerability/findings/{test_finding.id}/status",
        json={"status": "accepted_risk", "reason": "Mitigated by WAF"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted_risk"


@pytest.mark.asyncio
async def test_update_finding_status_invalid_transition(auth_client: AsyncClient, test_finding):
    resp = await auth_client.patch(
        f"/api/v1/vulnerability/findings/{test_finding.id}/status",
        json={"status": "remediated"},  # system-only transition
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_policies_empty(auth_client: AsyncClient):
    resp = await auth_client.get("/api/v1/vulnerability/policies")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_create_policy(auth_client: AsyncClient):
    resp = await auth_client.post(
        "/api/v1/vulnerability/policies",
        json={
            "name": "Auto-patch critical",
            "match_finding_type": "cve",
            "match_severity": ["critical"],
            "action_type": "patch_packages",
            "approval_level": "require_approval",
            "priority": 10,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Auto-patch critical"
    assert data["action_type"] == "patch_packages"


@pytest.mark.asyncio
async def test_patch_policy_disable(auth_client: AsyncClient, test_policy):
    resp = await auth_client.patch(
        f"/api/v1/vulnerability/policies/{test_policy.id}",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


@pytest.mark.asyncio
async def test_delete_policy(auth_client: AsyncClient, test_policy):
    resp = await auth_client.delete(f"/api/v1/vulnerability/policies/{test_policy.id}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_sla_dashboard(auth_client: AsyncClient):
    resp = await auth_client.get("/api/v1/vulnerability/sla/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert "critical" in data
    assert "overdue" in data["critical"]
    assert "due_soon" in data["critical"]


@pytest.mark.asyncio
async def test_blast_radius(auth_client: AsyncClient):
    resp = await auth_client.get("/api/v1/vulnerability/cve/CVE-2024-1234/blast-radius")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cve_id"] == "CVE-2024-1234"
    assert "affected_assets" in data
    assert "total_affected" in data
```

- [ ] **Step 2.2: Run tests — expect 404 (router not registered)**

```bash
cd backend && python -m pytest tests/test_vulnerability_api.py -v 2>&1 | head -40
```

Expected: all fail with `404 Not Found` or import error.

### Step 2.3: Implement the router

Create `backend/app/routers/vulnerability.py`:

```python
import hashlib
import hmac
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.vulnerability import VulnerabilityFinding, RemediationPolicy, RemediationSLA, SLA_HOURS
from app.models.asset import Asset
from app.models.user import User
from app.routers import current_user
from app.schemas.vulnerability import (
    WebhookFindingsPayload, WebhookAcceptedResponse,
    FindingRead, FindingListResponse, FindingStatusUpdate, GenerateCRRequest,
    PolicyCreate, PolicyUpdate, PolicyRead,
    SLADashboardResponse, SLASeverityStats,
    BlastRadiusResponse, AffectedAsset, PatchCampaignRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/vulnerability", tags=["Vulnerability"])

ALLOWED_STATUS_TRANSITIONS = {
    "open": {"accepted_risk", "false_positive"},
}


def _verify_hmac(body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


async def _ingest_findings_background(
    payload: WebhookFindingsPayload,
    db_factory,
) -> None:
    """Background task: deduplicate, insert, match assets, create SLAs, apply policies."""
    from app.database import AsyncSessionLocal
    from app.services.vuln_asset_matcher import match_asset
    from app.services.vuln_remediation_engine import generate_change_request_for_finding, match_policy

    async with AsyncSessionLocal() as db:
        for f_in in payload.findings:
            # Deduplicate
            if f_in.scanner_finding_id:
                existing = await db.execute(
                    select(VulnerabilityFinding).where(
                        VulnerabilityFinding.organization_id == payload.organization_id,
                        VulnerabilityFinding.scanner == payload.scanner,
                        VulnerabilityFinding.scanner_finding_id == f_in.scanner_finding_id,
                    )
                )
                if existing.scalar_one_or_none():
                    continue

            asset_id = await match_asset(db, payload.organization_id, f_in.target_ip, f_in.target_hostname)

            finding = VulnerabilityFinding(
                organization_id=payload.organization_id,
                asset_id=asset_id,
                scanner=payload.scanner,
                scanner_finding_id=f_in.scanner_finding_id,
                source="webhook",
                finding_type=f_in.finding_type,
                severity=f_in.severity,
                cve_id=f_in.cve_id,
                title=f_in.title,
                description=f_in.description,
                remediation_hint=f_in.remediation_hint,
                affected_package=f_in.affected_package,
                affected_version=f_in.affected_version,
                fixed_version=f_in.fixed_version,
                resource_type=f_in.resource_type,
                resource_id=f_in.resource_id,
                target_ip=f_in.target_ip,
                target_hostname=f_in.target_hostname,
                first_seen_at=f_in.first_seen_at,
                last_seen_at=f_in.last_seen_at,
                raw_payload=f_in.raw_payload,
            )
            db.add(finding)
            await db.flush()

            # Create SLA if applicable
            sla_hours = SLA_HOURS.get(f_in.severity)
            if sla_hours:
                sla = RemediationSLA(
                    organization_id=payload.organization_id,
                    finding_id=finding.id,
                    severity=f_in.severity,
                    sla_hours=sla_hours,
                    due_at=datetime.now(timezone.utc) + timedelta(hours=sla_hours),
                )
                db.add(sla)

            # Apply policy
            policies_result = await db.execute(
                select(RemediationPolicy).where(
                    RemediationPolicy.organization_id == payload.organization_id,
                    RemediationPolicy.enabled == True,
                ).order_by(RemediationPolicy.priority.desc())
            )
            policies = policies_result.scalars().all()
            matched_policy = match_policy(finding, list(policies))
            if matched_policy and matched_policy.action_type != "notify_only" and matched_policy.action_type != "suppress":
                await generate_change_request_for_finding(finding, matched_policy, db)

        await db.commit()


@router.post("/webhooks/vulnerability-findings", response_model=WebhookAcceptedResponse, status_code=202)
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_nexplane_signature: str = Header(..., alias="X-Nexplane-Signature"),
    db: AsyncSession = Depends(get_db),
):
    body = await request.body()
    # TODO: look up per-org webhook secret from connector_credentials
    # For now, fall back to a settings-level secret
    from app.config import settings
    webhook_secret = getattr(settings, "webhook_secret", "changeme")

    if not _verify_hmac(body, x_nexplane_signature, webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    import json
    payload = WebhookFindingsPayload(**json.loads(body))

    background_tasks.add_task(_ingest_findings_background, payload, None)

    return WebhookAcceptedResponse(
        accepted=len(payload.findings),
        duplicates_skipped=0,
        queued_for_matching=len(payload.findings),
    )


@router.get("/findings", response_model=FindingListResponse)
async def list_findings(
    severity: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    scanner: Optional[str] = Query(None),
    finding_type: Optional[str] = Query(None),
    asset_id: Optional[uuid.UUID] = Query(None),
    overdue: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(VulnerabilityFinding).where(
        VulnerabilityFinding.organization_id == user.organization_id
    )
    if severity:
        stmt = stmt.where(VulnerabilityFinding.severity == severity)
    if status:
        stmt = stmt.where(VulnerabilityFinding.status == status)
    if scanner:
        stmt = stmt.where(VulnerabilityFinding.scanner == scanner)
    if finding_type:
        stmt = stmt.where(VulnerabilityFinding.finding_type == finding_type)
    if asset_id:
        stmt = stmt.where(VulnerabilityFinding.asset_id == asset_id)
    if overdue:
        now = datetime.now(timezone.utc)
        stmt = stmt.join(
            RemediationSLA, RemediationSLA.finding_id == VulnerabilityFinding.id
        ).where(RemediationSLA.due_at < now, RemediationSLA.breached == False)

    total_result = await db.execute(select(sqlfunc.count()).select_from(stmt.subquery()))
    total = total_result.scalar_one()

    stmt = stmt.offset((page - 1) * page_size).limit(page_size).order_by(VulnerabilityFinding.ingested_at.desc())
    result = await db.execute(stmt)
    findings = result.scalars().all()

    finding_reads = []
    for f in findings:
        sla = None
        if f.id:
            sla_result = await db.execute(
                select(RemediationSLA).where(RemediationSLA.finding_id == f.id)
            )
            sla = sla_result.scalar_one_or_none()

        asset_name = None
        if f.asset_id:
            asset = await db.get(Asset, f.asset_id)
            asset_name = asset.name if asset else None

        finding_reads.append(FindingRead(
            **{k: v for k, v in f.__dict__.items() if not k.startswith("_")},
            asset_name=asset_name,
            sla_due_at=sla.due_at if sla else None,
            sla_breached=sla.breached if sla else None,
        ))

    return FindingListResponse(total=total, page=page, page_size=page_size, findings=finding_reads)


@router.get("/findings/{finding_id}", response_model=FindingRead)
async def get_finding(
    finding_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VulnerabilityFinding).where(
            VulnerabilityFinding.id == finding_id,
            VulnerabilityFinding.organization_id == user.organization_id,
        )
    )
    finding = result.scalar_one_or_none()
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    return FindingRead(**{k: v for k, v in finding.__dict__.items() if not k.startswith("_")})


@router.patch("/findings/{finding_id}/status", response_model=FindingRead)
async def update_finding_status(
    finding_id: uuid.UUID,
    body: FindingStatusUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(VulnerabilityFinding).where(
            VulnerabilityFinding.id == finding_id,
            VulnerabilityFinding.organization_id == user.organization_id,
        )
    )
    finding = result.scalar_one_or_none()
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    allowed = ALLOWED_STATUS_TRANSITIONS.get(finding.status, set())
    if body.status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot transition from '{finding.status}' to '{body.status}'",
        )
    finding.status = body.status
    await db.commit()
    await db.refresh(finding)
    return FindingRead(**{k: v for k, v in finding.__dict__.items() if not k.startswith("_")})


@router.post("/findings/{finding_id}/generate-change-request", status_code=201)
async def manual_generate_cr(
    finding_id: uuid.UUID,
    body: GenerateCRRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.vuln_remediation_engine import generate_change_request_for_finding
    result = await db.execute(
        select(VulnerabilityFinding).where(
            VulnerabilityFinding.id == finding_id,
            VulnerabilityFinding.organization_id == user.organization_id,
        )
    )
    finding = result.scalar_one_or_none()
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")

    # Build a synthetic policy from the request overrides
    override_policy = None
    if body.action_type:
        override_policy = RemediationPolicy(
            organization_id=user.organization_id,
            name="manual",
            action_type=body.action_type,
            action_params=body.action_params or {},
            approval_level="require_approval",
            priority=0,
            enabled=True,
        )

    cr = await generate_change_request_for_finding(finding, override_policy, db)
    await db.commit()
    return {"change_request_id": str(cr.id), "status": cr.status}


@router.get("/policies", response_model=list[PolicyRead])
async def list_policies(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RemediationPolicy).where(
            RemediationPolicy.organization_id == user.organization_id
        ).order_by(RemediationPolicy.priority.desc())
    )
    return result.scalars().all()


@router.post("/policies", response_model=PolicyRead, status_code=201)
async def create_policy(
    body: PolicyCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    policy = RemediationPolicy(organization_id=user.organization_id, **body.model_dump())
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.patch("/policies/{policy_id}", response_model=PolicyRead)
async def update_policy(
    policy_id: uuid.UUID,
    body: PolicyUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    policy = await db.get(RemediationPolicy, policy_id)
    if not policy or policy.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Policy not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(policy, field, value)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.delete("/policies/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    policy = await db.get(RemediationPolicy, policy_id)
    if not policy or policy.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Policy not found")
    await db.delete(policy)
    await db.commit()


@router.get("/sla/dashboard", response_model=SLADashboardResponse)
async def sla_dashboard(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    soon = now + timedelta(hours=24)
    result: dict[str, SLASeverityStats] = {}
    for severity in ("critical", "high", "medium"):
        total_r = await db.execute(
            select(sqlfunc.count(RemediationSLA.id)).where(
                RemediationSLA.organization_id == user.organization_id,
                RemediationSLA.severity == severity,
            )
        )
        overdue_r = await db.execute(
            select(sqlfunc.count(RemediationSLA.id)).where(
                RemediationSLA.organization_id == user.organization_id,
                RemediationSLA.severity == severity,
                RemediationSLA.breached == True,
            )
        )
        due_soon_r = await db.execute(
            select(sqlfunc.count(RemediationSLA.id)).where(
                RemediationSLA.organization_id == user.organization_id,
                RemediationSLA.severity == severity,
                RemediationSLA.due_at <= soon,
                RemediationSLA.due_at > now,
                RemediationSLA.breached == False,
            )
        )
        result[severity] = SLASeverityStats(
            total=total_r.scalar_one(),
            overdue=overdue_r.scalar_one(),
            due_soon=due_soon_r.scalar_one(),
        )
    return SLADashboardResponse(**result)


@router.get("/cve/{cve_id}/blast-radius", response_model=BlastRadiusResponse)
async def cve_blast_radius(
    cve_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.vuln_asset_matcher import blast_radius_query
    affected, fixed_version = await blast_radius_query(db, user.organization_id, cve_id)
    return BlastRadiusResponse(
        cve_id=cve_id,
        affected_assets=affected,
        total_affected=len(affected),
        known_fixed_version=fixed_version,
    )


@router.post("/cve/{cve_id}/patch-campaign", status_code=201)
async def create_patch_campaign(
    cve_id: str,
    body: PatchCampaignRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.change_request import ChangeRequest
    cr = ChangeRequest(
        organization_id=user.organization_id,
        requester_id=user.id,
        title=f"Patch {cve_id} — {len(body.target_asset_ids)} assets",
        description=f"Auto-generated patch campaign for {cve_id}",
        change_type="patch_packages",
        target_asset_ids=[str(a) for a in body.target_asset_ids],
        desired_outcome={
            "cve_id": cve_id,
            "batch_size": body.batch_size,
            "rollout_strategy": body.rollout_strategy,
        },
        status="draft",
        source="auto_remediation",
    )
    db.add(cr)
    await db.commit()
    await db.refresh(cr)
    return {"change_request_id": str(cr.id), "status": cr.status}
```

### Step 2.4: Register the router in main.py

In `backend/app/main.py`, add after the existing router imports:

```python
from app.routers import vulnerability as vulnerability_router
```

And after the existing `app.include_router(agent_router.router)` line:

```python
app.include_router(vulnerability_router.router)
```

- [ ] **Step 2.5: Run API tests — expect pass**

```bash
cd backend && python -m pytest tests/test_vulnerability_api.py -v
```

Expected: all tests pass.

- [ ] **Step 2.6: Run full backend test suite for regressions**

```bash
cd backend && python -m pytest --tb=short -q
```

Expected: all existing tests still pass.

- [ ] **Step 2.7: Commit**

```bash
git add backend/app/routers/vulnerability.py backend/app/main.py backend/tests/test_vulnerability_api.py
git commit -m "feat(vuln): add vulnerability API router — findings CRUD, webhook ingest, policies, SLA dashboard, blast-radius"
```

---

## Task 3: Asset Matching Service

**Files:**
- Create: `backend/app/services/vuln_asset_matcher.py`
- Create: `backend/tests/test_vuln_asset_matcher.py`

### Step 3.1: Write failing tests first

Create `backend/tests/test_vuln_asset_matcher.py`:

```python
import uuid
import pytest
from sqlalchemy import insert

from app.models.asset import Asset
from app.services.vuln_asset_matcher import match_asset, blast_radius_query


@pytest.mark.asyncio
async def test_match_asset_by_ip(db_session, test_org, test_asset_with_ip):
    """Asset with matching IP should be returned."""
    matched_id = await match_asset(
        db_session,
        test_org.id,
        ip="10.0.1.50",
        hostname=None,
    )
    assert matched_id == test_asset_with_ip.id


@pytest.mark.asyncio
async def test_match_asset_by_hostname(db_session, test_org, test_asset_with_hostname):
    matched_id = await match_asset(
        db_session,
        test_org.id,
        ip=None,
        hostname="web-prod-01",
    )
    assert matched_id == test_asset_with_hostname.id


@pytest.mark.asyncio
async def test_match_asset_ip_takes_precedence(db_session, test_org, test_asset_with_ip):
    """IP match wins when both IP and hostname provided."""
    matched_id = await match_asset(
        db_session,
        test_org.id,
        ip="10.0.1.50",
        hostname="no-such-host",
    )
    assert matched_id == test_asset_with_ip.id


@pytest.mark.asyncio
async def test_match_asset_no_match_returns_none(db_session, test_org):
    matched_id = await match_asset(
        db_session,
        test_org.id,
        ip="192.168.99.99",
        hostname="unknown-host",
    )
    assert matched_id is None


@pytest.mark.asyncio
async def test_blast_radius_query_finds_affected_assets(db_session, test_org, test_asset_with_software_metadata):
    """Assets with matching package/version in asset_metadata should appear."""
    affected, fixed_version = await blast_radius_query(
        db_session,
        test_org.id,
        cve_id="CVE-2024-1234",
        package="openssl",
        affected_version="3.0.2",
    )
    assert len(affected) >= 1
    assert any(a.asset_id == test_asset_with_software_metadata.id for a in affected)


@pytest.mark.asyncio
async def test_blast_radius_returns_empty_for_unknown_cve(db_session, test_org):
    affected, fixed_version = await blast_radius_query(
        db_session,
        test_org.id,
        cve_id="CVE-0000-0000",
    )
    assert affected == []
    assert fixed_version is None
```

- [ ] **Step 3.2: Run tests — expect import failure**

```bash
cd backend && python -m pytest tests/test_vuln_asset_matcher.py -v 2>&1 | head -20
```

### Step 3.3: Implement the matcher service

Create `backend/app/services/vuln_asset_matcher.py`:

```python
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
    else:
        fixed_version = None

    # JSONB array elements query — asset_metadata->>'software' is a JSON array
    # each element: {"package": "openssl", "installed_version": "3.0.2", "os": "Ubuntu 22.04"}
    stmt = text("""
        SELECT a.id AS asset_id,
               a.metadata->>'hostname'   AS hostname,
               a.metadata->>'ip_address' AS ip_address,
               meta.value->>'package'             AS package,
               meta.value->>'installed_version'   AS installed_version,
               meta.value->>'os'                  AS os
        FROM   assets a,
               jsonb_array_elements(
                   COALESCE(a.metadata->'software', '[]'::jsonb)
               ) AS meta(value)
        WHERE  a.organization_id = :org_id
        AND    meta.value->>'package'           = :package
        AND    meta.value->>'installed_version' = :version
    """)
    result = await db.execute(stmt, {
        "org_id": str(organization_id),
        "package": package,
        "version": affected_version,
    })
    rows = result.mappings().all()

    affected = [
        AffectedAsset(
            asset_id=r["asset_id"],
            hostname=r["hostname"],
            ip_address=r["ip_address"],
            package=r["package"],
            installed_version=r["installed_version"],
            os=r["os"],
        )
        for r in rows
    ]
    return affected, fixed_version
```

- [ ] **Step 3.4: Run tests — expect pass**

```bash
cd backend && python -m pytest tests/test_vuln_asset_matcher.py -v
```

- [ ] **Step 3.5: Commit**

```bash
git add backend/app/services/vuln_asset_matcher.py backend/tests/test_vuln_asset_matcher.py
git commit -m "feat(vuln): add asset matcher service — IP/hostname match, CVE blast-radius JSONB query"
```

---

## Task 4: Change Request Auto-Generation

**Files:**
- Create: `backend/app/services/vuln_remediation_engine.py`
- Create: `backend/tests/test_vuln_remediation_engine.py`

### Step 4.1: Write failing tests first

Create `backend/tests/test_vuln_remediation_engine.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from app.models.vulnerability import VulnerabilityFinding, RemediationPolicy
from app.services.vuln_remediation_engine import (
    match_policy,
    _default_action,
    generate_change_request_for_finding,
)


def make_finding(**kwargs) -> VulnerabilityFinding:
    defaults = dict(
        scanner="qualys",
        source="webhook",
        finding_type="cve",
        severity="critical",
        title="Test finding",
    )
    defaults.update(kwargs)
    f = VulnerabilityFinding.__new__(VulnerabilityFinding)
    for k, v in defaults.items():
        setattr(f, k, v)
    return f


def make_policy(**kwargs) -> RemediationPolicy:
    defaults = dict(
        match_scanner=None,
        match_finding_type=None,
        match_severity=None,
        match_resource_type=None,
        action_type="patch_packages",
        action_params=None,
        approval_level="require_approval",
        priority=0,
        enabled=True,
    )
    defaults.update(kwargs)
    p = RemediationPolicy.__new__(RemediationPolicy)
    for k, v in defaults.items():
        setattr(p, k, v)
    return p


class TestMatchPolicy:
    def test_no_policies_returns_none(self):
        f = make_finding()
        assert match_policy(f, []) is None

    def test_disabled_policy_not_matched(self):
        f = make_finding()
        p = make_policy(enabled=False)
        assert match_policy(f, [p]) is None

    def test_wildcard_policy_matches_anything(self):
        f = make_finding(scanner="tenable", finding_type="cve", severity="high")
        p = make_policy(priority=5)
        assert match_policy(f, [p]) is p

    def test_scanner_filter(self):
        f = make_finding(scanner="qualys")
        p_qualys = make_policy(match_scanner="qualys", priority=10)
        p_tenable = make_policy(match_scanner="tenable", priority=20)
        result = match_policy(f, [p_qualys, p_tenable])
        assert result is p_qualys

    def test_severity_filter(self):
        f = make_finding(severity="medium")
        p_critical = make_policy(match_severity=["critical", "high"], priority=10)
        p_all = make_policy(match_severity=None, priority=5)
        result = match_policy(f, [p_critical, p_all])
        assert result is p_all

    def test_highest_priority_wins(self):
        f = make_finding()
        p_low = make_policy(priority=1, action_type="notify_only")
        p_high = make_policy(priority=99, action_type="patch_packages")
        result = match_policy(f, [p_low, p_high])
        assert result is p_high


class TestDefaultAction:
    def test_cve_maps_to_patch_packages(self):
        f = make_finding(finding_type="cve")
        assert _default_action(f) == "patch_packages"

    def test_s3_public_access(self):
        f = make_finding(finding_type="misconfiguration", resource_type="s3_public_access")
        assert _default_action(f) == "s3_block_public_access"

    def test_security_group_open(self):
        f = make_finding(finding_type="misconfiguration", resource_type="security_group_open")
        assert _default_action(f) == "security_group_update"

    def test_iam_no_mfa(self):
        f = make_finding(finding_type="misconfiguration", resource_type="iam_no_mfa")
        assert _default_action(f) == "iam_enforce_mfa"

    def test_unknown_resource_type_falls_back(self):
        f = make_finding(finding_type="misconfiguration", resource_type="unknown_thing")
        assert _default_action(f) == "generic_remediation"


@pytest.mark.asyncio
async def test_generate_change_request_creates_draft(db_session, test_org, test_finding):
    """generate_change_request_for_finding must create a DRAFT CR and link it to the finding."""
    with patch(
        "app.services.vuln_remediation_engine.ai_generate_plan",
        new_callable=AsyncMock,
        return_value={"steps": []},
    ):
        cr = await generate_change_request_for_finding(test_finding, None, db_session)

    assert cr.status == "draft"
    assert cr.source == "auto_remediation"
    assert test_finding.change_request_id == cr.id
    assert test_finding.status == "change_request_generated"


@pytest.mark.asyncio
async def test_generate_change_request_uses_policy_action(db_session, test_org, test_finding):
    policy = make_policy(action_type="security_group_update", action_params={"port": 22})
    with patch(
        "app.services.vuln_remediation_engine.ai_generate_plan",
        new_callable=AsyncMock,
        return_value={"steps": []},
    ):
        cr = await generate_change_request_for_finding(test_finding, policy, db_session)

    assert cr.change_type == "security_group_update"
```

- [ ] **Step 4.2: Run tests — expect import failure**

```bash
cd backend && python -m pytest tests/test_vuln_remediation_engine.py -v 2>&1 | head -20
```

### Step 4.3: Implement the remediation engine

Create `backend/app/services/vuln_remediation_engine.py`:

```python
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
        logger.warning(f"AI plan generation failed for finding {finding.id}: {e}")
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
    from app.models.change_request import ChangeRequest

    action_type = policy.action_type if policy else _default_action(finding)
    action_params = (policy.action_params or {}) if policy else {}

    cve_suffix = f" ({finding.cve_id})" if getattr(finding, "cve_id", None) else ""
    title = f"Remediate: {finding.title}{cve_suffix}"

    ai_plan = await ai_generate_plan(finding, action_type)

    cr = ChangeRequest(
        organization_id=finding.organization_id,
        change_type=action_type,
        title=title,
        description=getattr(finding, "description", "") or "",
        target_asset_ids=[str(finding.asset_id)] if finding.asset_id else [],
        desired_outcome=action_params,
        status="draft",
        source="auto_remediation",
        finding_id=finding.id,
    )
    db.add(cr)
    await db.flush()

    finding.change_request_id = cr.id
    finding.status = "change_request_generated"

    logger.info(f"Generated DRAFT CR {cr.id} for finding {finding.id} (action={action_type})")
    return cr
```

- [ ] **Step 4.4: Run tests — expect pass**

```bash
cd backend && python -m pytest tests/test_vuln_remediation_engine.py -v
```

- [ ] **Step 4.5: Commit**

```bash
git add backend/app/services/vuln_remediation_engine.py backend/tests/test_vuln_remediation_engine.py
git commit -m "feat(vuln): add remediation engine — match_policy, default action mapping, generate_change_request_for_finding"
```

---

## Task 5: Background Jobs (APScheduler)

**Files:**
- Create: `backend/app/jobs/scanner_poll.py`
- Create: `backend/app/jobs/sla_enforcement.py`
- Create: `backend/app/jobs/finding_asset_match.py`
- Modify: `backend/app/services/scheduler_service.py`
- Create: `backend/tests/test_vuln_jobs.py`

### Step 5.1: Write failing tests first

Create `backend/tests/test_vuln_jobs.py`:

```python
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from app.models.vulnerability import VulnerabilityFinding, RemediationSLA


@pytest.mark.asyncio
async def test_sla_enforcement_marks_breached(db_session, test_org, test_finding_with_overdue_sla):
    from app.jobs.sla_enforcement import enforce_slas
    await enforce_slas(db_session)
    await db_session.refresh(test_finding_with_overdue_sla["sla"])
    assert test_finding_with_overdue_sla["sla"].breached is True


@pytest.mark.asyncio
async def test_sla_enforcement_generates_cr_for_open_finding(
    db_session, test_org, test_finding_with_overdue_sla
):
    from app.jobs.sla_enforcement import enforce_slas
    finding = test_finding_with_overdue_sla["finding"]
    with patch(
        "app.jobs.sla_enforcement.generate_change_request_for_finding",
        new_callable=AsyncMock,
    ) as mock_gen:
        mock_gen.return_value = MagicMock(id=uuid.uuid4(), status="draft")
        await enforce_slas(db_session)
    mock_gen.assert_called_once()


@pytest.mark.asyncio
async def test_sla_enforcement_skips_non_overdue(db_session, test_org, test_finding):
    """Findings with future SLA must not be marked breached."""
    from app.jobs.sla_enforcement import enforce_slas
    sla = RemediationSLA(
        organization_id=test_org.id,
        finding_id=test_finding.id,
        severity="critical",
        sla_hours=72,
        due_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    db_session.add(sla)
    await db_session.flush()
    await enforce_slas(db_session)
    await db_session.refresh(sla)
    assert sla.breached is False


@pytest.mark.asyncio
async def test_finding_asset_match_job_resolves_unmatched(db_session, test_org, test_unmatched_finding, test_asset_with_ip):
    from app.jobs.finding_asset_match import retry_asset_matching
    await retry_asset_matching(db_session)
    await db_session.refresh(test_unmatched_finding)
    assert test_unmatched_finding.asset_id == test_asset_with_ip.id


@pytest.mark.asyncio
async def test_scanner_poll_job_calls_ingest(db_session, test_org):
    """Scanner poll job should call the CrowdStrike client and ingest results."""
    from app.jobs.scanner_poll import poll_crowdstrike
    with patch(
        "app.jobs.scanner_poll.fetch_crowdstrike_findings",
        new_callable=AsyncMock,
        return_value=[],
    ) as mock_fetch:
        await poll_crowdstrike(db_session, test_org.id)
    mock_fetch.assert_called_once()
```

- [ ] **Step 5.2: Run tests — expect import failure**

```bash
cd backend && python -m pytest tests/test_vuln_jobs.py -v 2>&1 | head -20
```

### Step 5.3: Implement job files

Create `backend/app/jobs/sla_enforcement.py`:

```python
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
```

Create `backend/app/jobs/finding_asset_match.py`:

```python
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
```

Create `backend/app/jobs/scanner_poll.py`:

```python
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
    """
    # TODO: implement CrowdStrike Spotlight API client using credential["api_key"]
    # and credential["base_url"]. Paginate using after cursor.
    return []


async def poll_crowdstrike(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Pull vulnerability findings from CrowdStrike Spotlight for one org."""
    from app.models.connector_credential import ConnectorCredential
    from sqlalchemy import select

    cred_result = await db.execute(
        select(ConnectorCredential).where(
            ConnectorCredential.organization_id == org_id,
            ConnectorCredential.connector_type == "crowdstrike",
        )
    )
    cred = cred_result.scalar_one_or_none()
    if not cred:
        logger.debug(f"No CrowdStrike credential for org {org_id}, skipping")
        return

    raw_findings = await fetch_crowdstrike_findings(org_id, cred.credentials or {})

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
```

### Step 5.4: Register jobs in scheduler_service.py

In `backend/app/services/scheduler_service.py`, after the existing `scheduler.start()` call inside `start()`, add:

```python
    # Register vulnerability background jobs
    scheduler.add_job(
        _run_sla_enforcement,
        trigger="interval",
        minutes=15,
        id="sla_enforcement",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_finding_asset_match,
        trigger="interval",
        minutes=5,
        id="finding_asset_match",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_scanner_poll,
        trigger="interval",
        hours=6,
        id="scanner_poll",
        replace_existing=True,
    )
```

Add the three job runner functions at the bottom of `scheduler_service.py`:

```python
async def _run_sla_enforcement():
    if _db_factory is None:
        return
    async with _db_factory() as db:
        from app.jobs.sla_enforcement import enforce_slas
        await enforce_slas(db)


async def _run_finding_asset_match():
    if _db_factory is None:
        return
    async with _db_factory() as db:
        from app.jobs.finding_asset_match import retry_asset_matching
        await retry_asset_matching(db)


async def _run_scanner_poll():
    if _db_factory is None:
        return
    from app.models.organization import Organization
    from sqlalchemy import select
    async with _db_factory() as db:
        from app.jobs.scanner_poll import poll_crowdstrike
        result = await db.execute(select(Organization.id))
        org_ids = [row[0] for row in result]
    for org_id in org_ids:
        async with _db_factory() as db:
            await poll_crowdstrike(db, org_id)
```

- [ ] **Step 5.5: Run job tests — expect pass**

```bash
cd backend && python -m pytest tests/test_vuln_jobs.py -v
```

- [ ] **Step 5.6: Run full test suite**

```bash
cd backend && python -m pytest --tb=short -q
```

- [ ] **Step 5.7: Commit**

```bash
git add backend/app/jobs/ backend/app/services/scheduler_service.py backend/tests/test_vuln_jobs.py
git commit -m "feat(vuln): add scanner poll, SLA enforcement, and asset re-match background jobs"
```

---

## Task 6: CVE Blast Radius UI and Finding Queue

**Files:**
- Create: `frontend/src/pages/VulnerabilityRemediation.tsx`
- Create: `frontend/src/components/FindingQueue.tsx`
- Modify: `frontend/src/App.tsx`

### Step 6.1: Create VulnerabilityRemediation page

Create `frontend/src/pages/VulnerabilityRemediation.tsx`:

```tsx
import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import apiClient from "../api/client";
import FindingQueue from "../components/FindingQueue";

interface AffectedAsset {
  asset_id: string;
  hostname: string | null;
  ip_address: string | null;
  package: string | null;
  installed_version: string | null;
  os: string | null;
}

interface BlastRadiusResponse {
  cve_id: string;
  affected_assets: AffectedAsset[];
  total_affected: number;
  known_fixed_version: string | null;
}

export default function VulnerabilityRemediation() {
  const [cveInput, setCveInput] = useState("");
  const [searchedCve, setSearchedCve] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data: blastRadius, isFetching: blastLoading } = useQuery<BlastRadiusResponse>({
    queryKey: ["blast-radius", searchedCve],
    queryFn: () =>
      apiClient
        .get<BlastRadiusResponse>(`/api/v1/vulnerability/cve/${searchedCve}/blast-radius`)
        .then((r) => r.data),
    enabled: !!searchedCve,
  });

  const patchCampaignMutation = useMutation({
    mutationFn: (targetAssetIds: string[]) =>
      apiClient.post(`/api/v1/vulnerability/cve/${searchedCve}/patch-campaign`, {
        target_asset_ids: targetAssetIds,
        batch_size: 10,
        rollout_strategy: "rolling",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["findings"] });
      alert("Patch campaign created as DRAFT change request");
    },
  });

  const handleSearch = () => {
    const trimmed = cveInput.trim().toUpperCase();
    if (trimmed) setSearchedCve(trimmed);
  };

  const handleGenerateCampaign = () => {
    if (!blastRadius) return;
    const ids = blastRadius.affected_assets.map((a) => a.asset_id);
    patchCampaignMutation.mutate(ids);
  };

  return (
    <div className="p-6 space-y-8">
      <h1 className="text-2xl font-bold">Vulnerability Remediation</h1>

      {/* CVE Blast Radius */}
      <section className="bg-white rounded-lg border p-6 space-y-4">
        <h2 className="text-lg font-semibold">CVE Blast Radius</h2>
        <div className="flex gap-2">
          <input
            className="border rounded px-3 py-2 flex-1 font-mono"
            placeholder="CVE-2024-1234"
            value={cveInput}
            onChange={(e) => setCveInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
          <button
            className="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700"
            onClick={handleSearch}
            disabled={blastLoading}
          >
            {blastLoading ? "Searching..." : "Search"}
          </button>
        </div>

        {blastRadius && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <p className="font-semibold">{blastRadius.cve_id}</p>
                <p className="text-sm text-gray-600">
                  {blastRadius.total_affected} assets affected
                  {blastRadius.known_fixed_version && (
                    <> · Fixed in: <span className="font-mono">{blastRadius.known_fixed_version}</span></>
                  )}
                </p>
              </div>
              <button
                className="bg-orange-600 text-white px-4 py-2 rounded hover:bg-orange-700 text-sm"
                onClick={handleGenerateCampaign}
                disabled={patchCampaignMutation.isPending || blastRadius.total_affected === 0}
              >
                {patchCampaignMutation.isPending ? "Creating..." : "Generate Patch Campaign"}
              </button>
            </div>

            {blastRadius.affected_assets.length > 0 && (
              <div className="border rounded overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50">
                    <tr>
                      <th className="text-left p-2 font-medium">Hostname</th>
                      <th className="text-left p-2 font-medium">IP</th>
                      <th className="text-left p-2 font-medium">Package</th>
                      <th className="text-left p-2 font-medium">Version</th>
                      <th className="text-left p-2 font-medium">OS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {blastRadius.affected_assets.map((asset) => (
                      <tr key={asset.asset_id} className="border-t">
                        <td className="p-2 font-mono text-xs">{asset.hostname ?? "—"}</td>
                        <td className="p-2 font-mono text-xs">{asset.ip_address ?? "—"}</td>
                        <td className="p-2 font-mono text-xs">{asset.package ?? "—"}</td>
                        <td className="p-2 font-mono text-xs">{asset.installed_version ?? "—"}</td>
                        <td className="p-2 text-xs">{asset.os ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      {/* Pending Remediation Queue */}
      <FindingQueue />
    </div>
  );
}
```

### Step 6.2: Create FindingQueue component

Create `frontend/src/components/FindingQueue.tsx`:

```tsx
import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import apiClient from "../api/client";

interface Finding {
  id: string;
  scanner: string;
  finding_type: string;
  severity: string;
  cve_id: string | null;
  title: string;
  asset_id: string | null;
  asset_name: string | null;
  status: string;
  change_request_id: string | null;
  ingested_at: string;
  sla_due_at: string | null;
  sla_breached: boolean | null;
}

interface FindingListResponse {
  total: number;
  page: number;
  page_size: number;
  findings: Finding[];
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: "bg-red-100 text-red-800 border-red-200",
  high: "bg-orange-100 text-orange-800 border-orange-200",
  medium: "bg-yellow-100 text-yellow-800 border-yellow-200",
  low: "bg-green-100 text-green-800 border-green-200",
  informational: "bg-gray-100 text-gray-800 border-gray-200",
};

function SLABadge({ dueAt, breached }: { dueAt: string | null; breached: boolean | null }) {
  if (!dueAt) return null;
  if (breached) return <span className="text-red-600 font-semibold text-xs">OVERDUE</span>;
  const hoursLeft = Math.max(0, (new Date(dueAt).getTime() - Date.now()) / 3600000);
  const label =
    hoursLeft < 24
      ? `${Math.round(hoursLeft)}h remaining`
      : `${Math.round(hoursLeft / 24)}d remaining`;
  return <span className="text-gray-600 text-xs">{label}</span>;
}

export default function FindingQueue() {
  const [severityFilter, setSeverityFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("open");
  const [page, setPage] = useState(1);
  const queryClient = useQueryClient();

  const params = new URLSearchParams({ page: String(page), page_size: "25" });
  if (severityFilter) params.set("severity", severityFilter);
  if (statusFilter) params.set("status", statusFilter);

  const { data, isLoading } = useQuery<FindingListResponse>({
    queryKey: ["findings", severityFilter, statusFilter, page],
    queryFn: () =>
      apiClient.get<FindingListResponse>(`/api/v1/vulnerability/findings?${params}`).then((r) => r.data),
    refetchInterval: 60_000,
  });

  const suppressMutation = useMutation({
    mutationFn: (id: string) =>
      apiClient.patch(`/api/v1/vulnerability/findings/${id}/status`, {
        status: "accepted_risk",
        reason: "Suppressed from queue",
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["findings"] }),
  });

  const generateCRMutation = useMutation({
    mutationFn: (id: string) =>
      apiClient.post(`/api/v1/vulnerability/findings/${id}/generate-change-request`, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["findings"] }),
  });

  return (
    <section className="bg-white rounded-lg border">
      <div className="p-4 border-b flex items-center justify-between">
        <h2 className="text-lg font-semibold">Pending Remediation</h2>
        <div className="flex gap-2">
          <select
            className="border rounded px-2 py-1 text-sm"
            value={severityFilter}
            onChange={(e) => { setSeverityFilter(e.target.value); setPage(1); }}
          >
            <option value="">All severities</option>
            {["critical", "high", "medium", "low"].map((s) => (
              <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
            ))}
          </select>
          <select
            className="border rounded px-2 py-1 text-sm"
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          >
            <option value="open">Open</option>
            <option value="change_request_generated">CR Generated</option>
            <option value="accepted_risk">Accepted Risk</option>
            <option value="">All statuses</option>
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="p-8 text-center text-gray-500">Loading findings...</div>
      ) : !data?.findings.length ? (
        <div className="p-8 text-center text-gray-500">No findings match the current filters.</div>
      ) : (
        <>
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="text-left p-3 font-medium">Severity</th>
                <th className="text-left p-3 font-medium">Finding</th>
                <th className="text-left p-3 font-medium">Asset</th>
                <th className="text-left p-3 font-medium">SLA</th>
                <th className="text-left p-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.findings.map((f) => (
                <tr key={f.id} className="border-t hover:bg-gray-50">
                  <td className="p-3">
                    <span className={`px-2 py-0.5 rounded border text-xs font-semibold uppercase ${SEVERITY_COLORS[f.severity] ?? ""}`}>
                      {f.severity}
                    </span>
                  </td>
                  <td className="p-3">
                    <p className="font-medium text-sm">{f.cve_id ?? f.finding_type}</p>
                    <p className="text-xs text-gray-500 truncate max-w-xs">{f.title}</p>
                  </td>
                  <td className="p-3 text-xs text-gray-700">{f.asset_name ?? "Unmatched"}</td>
                  <td className="p-3">
                    <SLABadge dueAt={f.sla_due_at} breached={f.sla_breached} />
                  </td>
                  <td className="p-3 space-x-2">
                    {f.change_request_id ? (
                      <a
                        href={`/change-requests/${f.change_request_id}`}
                        className="text-blue-600 hover:underline text-xs"
                      >
                        Review CR
                      </a>
                    ) : (
                      <button
                        className="text-blue-600 hover:underline text-xs"
                        onClick={() => generateCRMutation.mutate(f.id)}
                        disabled={generateCRMutation.isPending}
                      >
                        Generate CR
                      </button>
                    )}
                    <button
                      className="text-gray-500 hover:text-red-600 text-xs"
                      onClick={() => suppressMutation.mutate(f.id)}
                      disabled={suppressMutation.isPending}
                    >
                      Suppress
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="p-3 border-t flex items-center justify-between text-sm text-gray-600">
            <span>Total: {data.total}</span>
            <div className="flex gap-2">
              <button
                className="px-2 py-1 border rounded disabled:opacity-50"
                onClick={() => setPage((p) => p - 1)}
                disabled={page === 1}
              >
                Prev
              </button>
              <span>Page {page}</span>
              <button
                className="px-2 py-1 border rounded disabled:opacity-50"
                onClick={() => setPage((p) => p + 1)}
                disabled={data.findings.length < 25}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
```

### Step 6.3: Add route to App.tsx

In `frontend/src/App.tsx`, add the import and route for the new page. Find the existing route list and add:

```tsx
import VulnerabilityRemediation from "./pages/VulnerabilityRemediation";

// Inside the <Routes> block, add:
<Route path="/remediation" element={<VulnerabilityRemediation />} />
```

- [ ] **Step 6.4: Start frontend and verify**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Open `http://localhost:3000/remediation`. Verify:
1. CVE search bar renders
2. FindingQueue renders with filters working
3. No TypeScript errors in browser console

- [ ] **Step 6.5: Commit**

```bash
git add frontend/src/pages/VulnerabilityRemediation.tsx frontend/src/components/FindingQueue.tsx frontend/src/App.tsx
git commit -m "feat(vuln): add VulnerabilityRemediation page, FindingQueue component, /remediation route"
```

---

## Task 7: Remediation Policy Settings

**Files:**
- Create: `frontend/src/components/RemediationPolicyEditor.tsx`

### Step 7.1: Create RemediationPolicyEditor component

Create `frontend/src/components/RemediationPolicyEditor.tsx`:

```tsx
import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import apiClient from "../api/client";

interface Policy {
  id: string;
  name: string;
  match_scanner: string | null;
  match_finding_type: string | null;
  match_severity: string[] | null;
  match_resource_type: string | null;
  action_type: string;
  approval_level: string;
  priority: number;
  enabled: boolean;
  created_at: string;
}

const ACTION_TYPES = [
  "patch_packages",
  "s3_block_public_access",
  "security_group_update",
  "iam_enforce_mfa",
  "generic_remediation",
  "notify_only",
  "suppress",
];

const SEVERITIES = ["critical", "high", "medium", "low", "informational"];
const SCANNERS = ["qualys", "tenable", "wiz", "snyk", "crowdstrike"];
const FINDING_TYPES = ["cve", "misconfiguration", "secret", "iac"];

interface PolicyFormState {
  name: string;
  match_scanner: string;
  match_finding_type: string;
  match_severity: string[];
  match_resource_type: string;
  action_type: string;
  approval_level: string;
  priority: number;
  enabled: boolean;
}

const defaultForm = (): PolicyFormState => ({
  name: "",
  match_scanner: "",
  match_finding_type: "",
  match_severity: [],
  match_resource_type: "",
  action_type: "patch_packages",
  approval_level: "require_approval",
  priority: 0,
  enabled: true,
});

export default function RemediationPolicyEditor() {
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<PolicyFormState>(defaultForm());
  const queryClient = useQueryClient();

  const { data: policies = [], isLoading } = useQuery<Policy[]>({
    queryKey: ["remediation-policies"],
    queryFn: () =>
      apiClient.get<Policy[]>("/api/v1/vulnerability/policies").then((r) => r.data),
  });

  const createMutation = useMutation({
    mutationFn: (body: object) =>
      apiClient.post("/api/v1/vulnerability/policies", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["remediation-policies"] });
      setShowModal(false);
      setForm(defaultForm());
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      apiClient.patch(`/api/v1/vulnerability/policies/${id}`, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["remediation-policies"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiClient.delete(`/api/v1/vulnerability/policies/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["remediation-policies"] }),
  });

  const handleCreate = () => {
    const payload = {
      ...form,
      match_scanner: form.match_scanner || null,
      match_finding_type: form.match_finding_type || null,
      match_severity: form.match_severity.length ? form.match_severity : null,
      match_resource_type: form.match_resource_type || null,
    };
    createMutation.mutate(payload);
  };

  const toggleSeverity = (sev: string) => {
    setForm((f) => ({
      ...f,
      match_severity: f.match_severity.includes(sev)
        ? f.match_severity.filter((s) => s !== sev)
        : [...f.match_severity, sev],
    }));
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Remediation Policies</h2>
        <button
          className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm hover:bg-blue-700"
          onClick={() => setShowModal(true)}
        >
          + New Rule
        </button>
      </div>

      {isLoading ? (
        <p className="text-gray-500 text-sm">Loading policies...</p>
      ) : policies.length === 0 ? (
        <p className="text-gray-500 text-sm">
          No remediation policies configured. Create one to start auto-generating change requests from findings.
        </p>
      ) : (
        <table className="w-full text-sm border rounded overflow-hidden">
          <thead className="bg-gray-50">
            <tr>
              <th className="text-left p-3 font-medium">Name</th>
              <th className="text-left p-3 font-medium">Matches</th>
              <th className="text-left p-3 font-medium">Action</th>
              <th className="text-left p-3 font-medium">Approval</th>
              <th className="text-left p-3 font-medium">Priority</th>
              <th className="text-left p-3 font-medium">Enabled</th>
              <th className="p-3"></th>
            </tr>
          </thead>
          <tbody>
            {[...policies].sort((a, b) => b.priority - a.priority).map((p) => (
              <tr key={p.id} className={`border-t ${p.enabled ? "" : "opacity-50"}`}>
                <td className="p-3 font-medium">{p.name}</td>
                <td className="p-3 text-xs text-gray-600 space-y-0.5">
                  {p.match_scanner && <div>Scanner: {p.match_scanner}</div>}
                  {p.match_finding_type && <div>Type: {p.match_finding_type}</div>}
                  {p.match_severity?.length && <div>Severity: {p.match_severity.join(", ")}</div>}
                  {!p.match_scanner && !p.match_finding_type && !p.match_severity && (
                    <div className="text-gray-400">Any finding</div>
                  )}
                </td>
                <td className="p-3">
                  <code className="text-xs bg-gray-100 px-1 rounded">{p.action_type}</code>
                </td>
                <td className="p-3 text-xs">
                  {p.approval_level === "auto" ? (
                    <span className="text-green-700 font-medium">Auto</span>
                  ) : (
                    <span className="text-gray-600">Require approval</span>
                  )}
                </td>
                <td className="p-3 text-center">{p.priority}</td>
                <td className="p-3">
                  <button
                    className={`w-10 h-5 rounded-full transition-colors ${p.enabled ? "bg-blue-600" : "bg-gray-300"}`}
                    onClick={() => toggleMutation.mutate({ id: p.id, enabled: !p.enabled })}
                  />
                </td>
                <td className="p-3">
                  <button
                    className="text-red-500 hover:text-red-700 text-xs"
                    onClick={() => {
                      if (confirm(`Delete policy "${p.name}"?`)) deleteMutation.mutate(p.id);
                    }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Create Policy Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-lg space-y-4">
            <h3 className="text-lg font-semibold">New Remediation Policy</h3>

            <div>
              <label className="block text-sm font-medium mb-1">Rule Name</label>
              <input
                className="border rounded px-3 py-2 w-full text-sm"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Auto-patch critical CVEs"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Match Scanner</label>
                <select
                  className="border rounded px-2 py-1.5 w-full text-sm"
                  value={form.match_scanner}
                  onChange={(e) => setForm((f) => ({ ...f, match_scanner: e.target.value }))}
                >
                  <option value="">Any</option>
                  {SCANNERS.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Match Finding Type</label>
                <select
                  className="border rounded px-2 py-1.5 w-full text-sm"
                  value={form.match_finding_type}
                  onChange={(e) => setForm((f) => ({ ...f, match_finding_type: e.target.value }))}
                >
                  <option value="">Any</option>
                  {FINDING_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Match Severity (leave empty for any)</label>
              <div className="flex gap-2 flex-wrap">
                {SEVERITIES.map((sev) => (
                  <button
                    key={sev}
                    type="button"
                    className={`px-2 py-0.5 rounded border text-xs ${
                      form.match_severity.includes(sev)
                        ? "bg-blue-600 text-white border-blue-600"
                        : "border-gray-300 text-gray-700"
                    }`}
                    onClick={() => toggleSeverity(sev)}
                  >
                    {sev}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Action Type</label>
                <select
                  className="border rounded px-2 py-1.5 w-full text-sm"
                  value={form.action_type}
                  onChange={(e) => setForm((f) => ({ ...f, action_type: e.target.value }))}
                >
                  {ACTION_TYPES.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Approval Level</label>
                <select
                  className="border rounded px-2 py-1.5 w-full text-sm"
                  value={form.approval_level}
                  onChange={(e) => setForm((f) => ({ ...f, approval_level: e.target.value }))}
                >
                  <option value="require_approval">Require Approval</option>
                  <option value="auto">Auto (low-risk only)</option>
                </select>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Priority (higher wins)</label>
              <input
                type="number"
                className="border rounded px-3 py-2 w-24 text-sm"
                value={form.priority}
                onChange={(e) => setForm((f) => ({ ...f, priority: Number(e.target.value) }))}
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button
                className="px-4 py-2 border rounded text-sm hover:bg-gray-50"
                onClick={() => { setShowModal(false); setForm(defaultForm()); }}
              >
                Cancel
              </button>
              <button
                className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700 disabled:opacity-50"
                onClick={handleCreate}
                disabled={!form.name || createMutation.isPending}
              >
                {createMutation.isPending ? "Creating..." : "Create Rule"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
```

### Step 7.2: Add to Settings page

In `frontend/src/pages/Settings.tsx`, add a "Remediation Policies" tab:

1. Import: `import RemediationPolicyEditor from "../components/RemediationPolicyEditor";`
2. Add `"Remediation Policies"` to the tabs array.
3. Add a tab panel that renders `<RemediationPolicyEditor />`.

- [ ] **Step 7.3: Start frontend and verify**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Open `http://localhost:3000/settings`. Navigate to the "Remediation Policies" tab. Verify:
1. Empty state message renders
2. "+ New Rule" button opens modal
3. Severity multi-select toggles work
4. Submit creates a policy and it appears in the table
5. Enable/disable toggle works
6. Delete button removes the rule

- [ ] **Step 7.4: Commit**

```bash
git add frontend/src/components/RemediationPolicyEditor.tsx frontend/src/pages/Settings.tsx
git commit -m "feat(vuln): add RemediationPolicyEditor — per-severity policy CRUD with auto-approve and action type selection"
```

---

## Self-Review Checklist

**Spec coverage:**
- Section 1 (Data Models): Task 1 — all three models, schemas, Alembic migration
- Section 2 (API Endpoints): Task 2 — all endpoints from spec including webhook, findings CRUD, policies, SLA dashboard, blast-radius, patch campaign
- Section 3 (Webhook Format): Task 2 — HMAC verification, `WebhookFindingsPayload` schema
- Section 4 (Background Jobs): Task 5 — scanner poll (CrowdStrike), SLA enforcement, asset re-match
- Section 5 (Change Request Auto-Generation): Task 4 — `generate_change_request_for_finding`, `match_policy`, `_default_action`
- Section 6 (Frontend): Tasks 6 and 7 — Remediation page, FindingQueue, CVE blast radius, SLA widget, PolicyEditor

**TDD compliance:** Every task writes failing tests before implementation code.

**DRAFT guarantee:** `generate_change_request_for_finding` always sets `status="draft"`. Auto-approval only applies when an explicit policy with `approval_level="auto"` is matched.

**No placeholder code:** All service stubs (`fetch_crowdstrike_findings`, `ai_generate_plan`) have documented TODO comments and graceful fallbacks — no silent failures.

**Existing pattern adherence:**
- Models use `Mapped`/`mapped_column` style matching `asset.py`
- Routers use `Depends(current_user)` and `Depends(get_db)` matching `assets.py`
- Jobs are registered in `scheduler_service.py` using `scheduler.add_job` with `interval` trigger matching existing pattern
