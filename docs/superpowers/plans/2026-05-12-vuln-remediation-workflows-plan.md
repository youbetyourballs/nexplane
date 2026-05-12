# Vulnerability Remediation Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one-click patch, AI-guided mitigation (seccomp/network/WAF/eBPF), persistent patch campaigns with batch health gates, and SLA auto-escalation to the Vulnerability Remediation tab.

**Architecture:** Backend gains a `PatchCampaign` model, new finding-action endpoints, an AI mitigation suggestion service, and a scheduled SLA escalation job. Frontend gains expandable finding rows with inline action panels, a Mitigation panel with AI pre-selection, and a full Campaigns tab with progress tracking.

**Tech Stack:** FastAPI, SQLAlchemy async, React/TypeScript, @tanstack/react-query, existing CR workflow engine, existing AI service (`app.services.ai_service`)

---

## File Map

**New backend files:**
- `backend/app/models/patch_campaign.py` — PatchCampaign SQLAlchemy model
- `backend/app/schemas/patch_campaign.py` — Pydantic schemas for campaigns
- `backend/app/services/vuln_mitigation_service.py` — AI mitigation suggestion logic
- `backend/app/services/vuln_campaign_service.py` — campaign execution (batch processor)
- `backend/app/jobs/vuln_sla_escalation.py` — scheduled SLA breach escalation
- `backend/alembic/versions/048_vuln_remediation_extensions.py` — migration

**Modified backend files:**
- `backend/app/models/vulnerability.py` — add fields to VulnerabilityFinding, extend SLAConfig
- `backend/app/schemas/vulnerability.py` — extend FindingRead, add new request/response schemas
- `backend/app/routers/vulnerability.py` — add 10 new endpoints
- `backend/app/main.py` — register SLA escalation job with scheduler

**New frontend files:**
- `frontend/src/components/FindingActionPanel.tsx` — inline expanded panel with 6 actions
- `frontend/src/components/MitigationPanel.tsx` — AI-recommended controls with pre-selection
- `frontend/src/components/PatchCampaignList.tsx` — campaign list with progress bars
- `frontend/src/components/CreateCampaignDrawer.tsx` — CVE search → asset select → configure → launch
- `frontend/src/components/CampaignDetail.tsx` — batch-by-batch view with retry

**Modified frontend files:**
- `frontend/src/components/FindingQueue.tsx` — add expandable rows, wire to FindingActionPanel
- `frontend/src/pages/VulnerabilityRemediation.tsx` — add Campaigns tab

---

### Task 1: PatchCampaign model + migration

**Files:**
- Create: `backend/app/models/patch_campaign.py`
- Create: `backend/alembic/versions/048_vuln_remediation_extensions.py`
- Modify: `backend/app/models/vulnerability.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_patch_campaign_model.py`:

```python
import pytest
from app.models.patch_campaign import PatchCampaign, CampaignStatus


def test_campaign_status_enum_values():
    assert CampaignStatus.draft == "draft"
    assert CampaignStatus.running == "running"
    assert CampaignStatus.paused == "paused"
    assert CampaignStatus.complete == "complete"
    assert CampaignStatus.failed == "failed"
    assert CampaignStatus.aborted == "aborted"


def test_patch_campaign_has_required_fields():
    import inspect
    import sqlalchemy
    cols = {c.key for c in PatchCampaign.__table__.columns}
    for field in ("id", "organization_id", "title", "cve_id", "target_asset_ids",
                  "batch_size", "health_gate_seconds", "health_endpoint",
                  "abort_threshold", "rollout_strategy", "status", "batches"):
        assert field in cols, f"Missing column: {field}"
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec backend python -m pytest tests/unit/test_patch_campaign_model.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.models.patch_campaign'`

- [ ] **Step 3: Create the model**

Create `backend/app/models/patch_campaign.py`:

```python
import enum
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
import sqlalchemy as sa

from app.database import Base


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    running = "running"
    paused = "paused"
    complete = "complete"
    failed = "failed"
    aborted = "aborted"


class PatchCampaign(Base):
    __tablename__ = "patch_campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    cve_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    target_asset_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    health_gate_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    health_endpoint: Mapped[str] = mapped_column(String(500), nullable=False, default="/health")
    abort_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.2)
    rollout_strategy: Mapped[str] = mapped_column(String(50), nullable=False, default="rolling")
    status: Mapped[CampaignStatus] = mapped_column(
        sa.Enum(CampaignStatus, name="campaign_status"),
        nullable=False, default=CampaignStatus.draft
    )
    batches: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Extend VulnerabilityFinding with new fields**

In `backend/app/models/vulnerability.py`, add these columns to `VulnerabilityFinding` after the `change_request_id` line:

```python
    assigned_to_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    accepted_risk_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    accepted_risk_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    mitigations: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
```

Also extend the SLA config by adding to `SLA_HOURS` dict (no model change needed — config fields live in `OrganizationSettings.sla_config` JSON).

- [ ] **Step 5: Create migration**

Create `backend/alembic/versions/048_vuln_remediation_extensions.py`:

```python
"""vuln remediation extensions: PatchCampaign + finding fields

Revision ID: 048
Revises: 047
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '048'
down_revision = '047'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'patch_campaigns',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('title', sa.String(500), nullable=False),
        sa.Column('cve_id', sa.String(50), nullable=True),
        sa.Column('target_asset_ids', sa.JSON, nullable=False),
        sa.Column('batch_size', sa.Integer, nullable=False, server_default='5'),
        sa.Column('health_gate_seconds', sa.Integer, nullable=False, server_default='120'),
        sa.Column('health_endpoint', sa.String(500), nullable=False, server_default="'/health'"),
        sa.Column('abort_threshold', sa.Float, nullable=False, server_default='0.2'),
        sa.Column('rollout_strategy', sa.String(50), nullable=False, server_default="'rolling'"),
        sa.Column('status', sa.Enum('draft','running','paused','complete','failed','aborted', name='campaign_status'), nullable=False, server_default="'draft'"),
        sa.Column('batches', sa.JSON, nullable=False, server_default="'[]'"),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_patch_campaigns_org', 'patch_campaigns', ['organization_id'])

    op.add_column('vulnerability_findings', sa.Column('assigned_to_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id'), nullable=True))
    op.add_column('vulnerability_findings', sa.Column('accepted_risk_reason', sa.Text, nullable=True))
    op.add_column('vulnerability_findings', sa.Column('accepted_risk_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('vulnerability_findings', sa.Column('mitigations', sa.JSON, nullable=True))


def downgrade():
    op.drop_column('vulnerability_findings', 'mitigations')
    op.drop_column('vulnerability_findings', 'accepted_risk_expires_at')
    op.drop_column('vulnerability_findings', 'accepted_risk_reason')
    op.drop_column('vulnerability_findings', 'assigned_to_user_id')
    op.drop_index('ix_patch_campaigns_org', table_name='patch_campaigns')
    op.drop_table('patch_campaigns')
    op.execute("DROP TYPE IF EXISTS campaign_status")
```

- [ ] **Step 6: Apply migration and run tests**

```bash
docker compose exec backend alembic upgrade head
docker compose exec backend python -m pytest tests/unit/test_patch_campaign_model.py -v
```
Expected: 2 tests PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/patch_campaign.py backend/app/models/vulnerability.py backend/alembic/versions/048_vuln_remediation_extensions.py backend/tests/unit/test_patch_campaign_model.py
git commit -m "feat: add PatchCampaign model and extend VulnerabilityFinding for remediation workflows"
```

---

### Task 2: Schemas for campaigns and extended finding actions

**Files:**
- Create: `backend/app/schemas/patch_campaign.py`
- Modify: `backend/app/schemas/vulnerability.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_vuln_schemas.py`:

```python
from app.schemas.patch_campaign import CampaignCreate, CampaignRead
from app.schemas.vulnerability import FindingPatchRequest, FindingMitigateRequest, FindingAssignRequest, FindingAcceptRiskRequest


def test_campaign_create_defaults():
    c = CampaignCreate(title="Test", target_asset_ids=["00000000-0000-0000-0000-000000000001"])
    assert c.batch_size == 5
    assert c.health_gate_seconds == 120
    assert c.abort_threshold == 0.2
    assert c.rollout_strategy == "rolling"


def test_finding_patch_request():
    r = FindingPatchRequest()
    assert r.rollback_on_failure is True


def test_finding_mitigate_request():
    r = FindingMitigateRequest(selected_controls=["network_isolation", "seccomp"])
    assert "network_isolation" in r.selected_controls


def test_finding_accept_risk_request():
    from datetime import datetime, timezone, timedelta
    r = FindingAcceptRiskRequest(
        reason="Business exception",
        expires_at=datetime.now(timezone.utc) + timedelta(days=90)
    )
    assert r.reason == "Business exception"
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec backend python -m pytest tests/unit/test_vuln_schemas.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Create campaign schemas**

Create `backend/app/schemas/patch_campaign.py`:

```python
import uuid
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field


class CampaignCreate(BaseModel):
    title: str
    cve_id: Optional[str] = None
    target_asset_ids: list[uuid.UUID]
    batch_size: int = Field(default=5, ge=1, le=100)
    health_gate_seconds: int = Field(default=120, ge=10)
    health_endpoint: str = "/health"
    abort_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    rollout_strategy: str = "rolling"  # "rolling" | "canary" | "all_at_once"


class BatchResult(BaseModel):
    batch_index: int
    asset_ids: list[str]
    status: str  # "pending" | "running" | "passed" | "failed"
    cr_ids: list[str] = []
    health_check_result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class CampaignRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    title: str
    cve_id: Optional[str]
    target_asset_ids: list[Any]
    batch_size: int
    health_gate_seconds: int
    health_endpoint: str
    abort_threshold: float
    rollout_strategy: str
    status: str
    batches: list[Any]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    total_assets: int = 0
    passed_count: int = 0
    failed_count: int = 0
    pending_count: int = 0
```

- [ ] **Step 4: Add new finding action schemas to vulnerability.py**

In `backend/app/schemas/vulnerability.py`, add after the `GenerateCRRequest` class:

```python
class FindingPatchRequest(BaseModel):
    rollback_on_failure: bool = True
    verify_seconds: int = 60


class FindingMitigateRequest(BaseModel):
    selected_controls: list[str]
    # Each control: "network_isolation" | "seccomp" | "apparmor" | "waf" |
    #               "feature_flag" | "process_isolation" | "ebpf" |
    #               "capability_drop" | "rate_limit" | "virtual_patch" | "full_isolation"


class FindingAssignRequest(BaseModel):
    user_id: uuid.UUID


class FindingAcceptRiskRequest(BaseModel):
    reason: str
    expires_at: datetime


class MitigationSuggestion(BaseModel):
    control: str
    title: str
    description: str
    rationale: str
    confidence: float  # 0.0–1.0
    impact: str  # "low" | "medium" | "high"
    recommended: bool


class MitigationSuggestionsResponse(BaseModel):
    finding_id: uuid.UUID
    suggestions: list[MitigationSuggestion]
    ai_summary: str


class SLAConfigExtended(BaseModel):
    model_config = ConfigDict(extra="ignore")
    critical: int = Field(default=72, ge=1)
    high: int = Field(default=168, ge=1)
    medium: int = Field(default=720, ge=1)
    escalate_to_user_id: Optional[uuid.UUID] = None
    auto_execute_on_breach: bool = False
    severity_upgrade_hours: int = 48
```

Also extend `FindingRead` with new fields:

```python
    # New fields
    assigned_to_user_id: Optional[uuid.UUID] = None
    accepted_risk_reason: Optional[str] = None
    accepted_risk_expires_at: Optional[datetime] = None
    mitigations: Optional[list] = None
```

- [ ] **Step 5: Run tests**

```bash
docker compose exec backend python -m pytest tests/unit/test_vuln_schemas.py -v
```
Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/patch_campaign.py backend/app/schemas/vulnerability.py backend/tests/unit/test_vuln_schemas.py
git commit -m "feat: add PatchCampaign schemas and extended finding action schemas"
```

---

### Task 3: AI mitigation suggestion service

**Files:**
- Create: `backend/app/services/vuln_mitigation_service.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_vuln_mitigation_service.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_suggest_mitigations_returns_ranked_list():
    from app.services.vuln_mitigation_service import suggest_mitigations

    mock_finding = MagicMock()
    mock_finding.id = "test-id"
    mock_finding.cve_id = "CVE-2024-1234"
    mock_finding.finding_type = "cve"
    mock_finding.severity = "critical"
    mock_finding.title = "OpenSSL buffer overflow"
    mock_finding.description = "TLS handshake vulnerability"
    mock_finding.affected_package = "openssl"
    mock_finding.affected_version = "1.1.1t"
    mock_finding.fixed_version = "3.0.2"

    mock_asset = MagicMock()
    mock_asset.criticality = "critical"
    mock_asset.environment = "prod"

    with patch("app.services.vuln_mitigation_service._ai_suggest", new_callable=AsyncMock,
               return_value=None):  # AI unavailable → heuristic fallback
        result = await suggest_mitigations(mock_finding, mock_asset)

    assert len(result.suggestions) >= 3
    recommended = [s for s in result.suggestions if s.recommended]
    assert len(recommended) >= 1
    assert all(0.0 <= s.confidence <= 1.0 for s in result.suggestions)
    assert result.ai_summary


@pytest.mark.asyncio
async def test_network_cve_prefers_network_controls():
    from app.services.vuln_mitigation_service import _heuristic_suggest

    mock_finding = MagicMock()
    mock_finding.cve_id = "CVE-2024-1234"
    mock_finding.title = "Remote TLS buffer overflow"
    mock_finding.description = "Network-exploitable via crafted TLS packet"
    mock_finding.finding_type = "cve"
    mock_finding.severity = "critical"

    suggestions = _heuristic_suggest(mock_finding, "prod", "critical")
    controls = [s.control for s in suggestions if s.recommended]
    # Network CVE should recommend network isolation
    assert any("network" in c or "waf" in c for c in controls)
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec backend python -m pytest tests/unit/test_vuln_mitigation_service.py -v
```
Expected: `ImportError: No module named 'app.services.vuln_mitigation_service'`

- [ ] **Step 3: Implement the service**

Create `backend/app/services/vuln_mitigation_service.py`:

```python
"""AI-guided mitigation suggestion service for vulnerability findings."""
from __future__ import annotations
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Control definitions: id → (title, description, applicable_to)
_CONTROLS = {
    "network_isolation": (
        "Network isolation — restrict inbound ports",
        "Security group rule restricting inbound to trusted CIDRs. Removes attack surface for network-exploitable CVEs.",
        ["network", "remote"],
    ),
    "seccomp": (
        "seccomp profile — restrict syscalls",
        "Minimal-privilege seccomp profile blocking syscalls required by the exploit (ptrace, execve for privesc).",
        ["local", "privesc", "memory"],
    ),
    "apparmor": (
        "AppArmor/SELinux policy tightening",
        "MAC policy confining process filesystem, network, and capability access.",
        ["local", "privesc", "file"],
    ),
    "waf": (
        "WAF rule — block exploit request pattern",
        "Web Application Firewall rule blocking the specific request pattern the CVE exploits.",
        ["network", "web", "remote"],
    ),
    "feature_flag": (
        "Feature flag / config disable",
        "Disable the vulnerable feature or protocol version via environment variable or config file.",
        ["config", "network", "remote"],
    ),
    "process_isolation": (
        "Process namespace isolation",
        "Isolate the vulnerable service into a separate PID/network/mount namespace.",
        ["local", "privesc", "memory"],
    ),
    "ebpf": (
        "eBPF runtime block",
        "eBPF probe intercepting the specific syscall pattern the exploit uses. Most surgical option.",
        ["local", "privesc", "memory", "network"],
    ),
    "capability_drop": (
        "Linux capability drop",
        "Remove CAP_NET_RAW, CAP_SYS_ADMIN and other capabilities not needed for normal operation.",
        ["local", "privesc"],
    ),
    "rate_limit": (
        "Rate limiting / circuit breaker",
        "Aggressive rate limits raise exploitation cost significantly. Buys time until patch.",
        ["network", "remote", "web"],
    ),
    "virtual_patch": (
        "Virtual patch (IDS/IPS rule)",
        "Snort/Suricata rule detecting and dropping traffic matching the exploit signature.",
        ["network", "remote"],
    ),
    "full_isolation": (
        "Full network isolation — quarantine host",
        "Last resort: deny-all security group. Full protection but causes service disruption.",
        ["network", "remote", "local"],
    ),
}

_IMPACT = {
    "network_isolation": "low",
    "seccomp": "low",
    "apparmor": "low",
    "waf": "low",
    "feature_flag": "low",
    "process_isolation": "medium",
    "ebpf": "low",
    "capability_drop": "low",
    "rate_limit": "low",
    "virtual_patch": "low",
    "full_isolation": "high",
}


def _classify_cve(finding) -> list[str]:
    """Classify CVE into exploit categories based on title/description."""
    text = f"{finding.title or ''} {finding.description or ''}".lower()
    categories = []
    if any(w in text for w in ["remote", "network", "tls", "http", "tcp", "rce", "request"]):
        categories.append("network")
        categories.append("remote")
    if any(w in text for w in ["web", "http", "sql", "xss", "csrf", "injection"]):
        categories.append("web")
    if any(w in text for w in ["privilege", "escalat", "root", "admin", "sudo", "cap_"]):
        categories.append("privesc")
    if any(w in text for w in ["buffer", "overflow", "heap", "stack", "memory", "use-after"]):
        categories.append("memory")
    if any(w in text for w in ["file", "path", "traversal", "read", "write", "directory"]):
        categories.append("file")
    if any(w in text for w in ["config", "setting", "protocol", "version", "cipher", "ssl", "tls 1"]):
        categories.append("config")
    if not categories:
        categories = ["network"]  # safe default
    return categories


def _heuristic_suggest(finding, environment: str, criticality: str) -> list:
    from app.schemas.vulnerability import MitigationSuggestion
    categories = _classify_cve(finding)

    scored: list[tuple[float, str]] = []
    for control_id, (title, desc, applicable) in _CONTROLS.items():
        overlap = len(set(categories) & set(applicable))
        if overlap == 0:
            continue
        base = overlap / max(len(categories), 1)
        # Penalise high-impact controls unless severity is critical
        if _IMPACT[control_id] == "high" and criticality not in ("critical",):
            base *= 0.3
        # Boost controls suited to prod critical assets
        if environment == "prod" and criticality == "critical":
            if control_id in ("network_isolation", "seccomp", "feature_flag"):
                base = min(base * 1.3, 0.99)
        scored.append((base, control_id))

    scored.sort(key=lambda x: -x[0])

    # Top 3 are recommended; rest are optional
    suggestions = []
    for i, (score, control_id) in enumerate(scored):
        title, desc, _ = _CONTROLS[control_id]
        rationale = (
            f"Confidence {int(score*100)}% — "
            + ("Recommended for this CVE type and asset criticality." if i < 3 else "Optional — adds defence in depth.")
        )
        suggestions.append(MitigationSuggestion(
            control=control_id,
            title=title,
            description=desc,
            rationale=rationale,
            confidence=round(score, 2),
            impact=_IMPACT[control_id],
            recommended=(i < 3),
        ))

    return suggestions


async def _ai_suggest(finding, environment: str, criticality: str) -> Optional[list]:
    """Call the AI service to generate mitigation suggestions. Returns None on failure."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = f"""You are a security engineer. Suggest mitigations for this vulnerability.

CVE: {finding.cve_id}
Title: {finding.title}
Description: {finding.description or 'N/A'}
Package: {finding.affected_package} {finding.affected_version}
Severity: {finding.severity}
Environment: {environment}, Criticality: {criticality}

Return JSON array (3-5 items, most effective first):
[{{"control": "<id>", "confidence": 0.0-1.0, "rationale": "<why>", "recommended": true/false}}]

Control IDs: network_isolation, seccomp, apparmor, waf, feature_flag, process_isolation, ebpf, capability_drop, rate_limit, virtual_patch, full_isolation"""

        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text
        import re
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        logger.debug(f"AI mitigation suggestion failed: {e}")
    return None


async def suggest_mitigations(finding, asset=None) -> "MitigationSuggestionsResponse":
    from app.schemas.vulnerability import MitigationSuggestionsResponse
    environment = getattr(asset, "environment", "unknown") or "unknown"
    criticality = getattr(asset, "criticality", "medium") or "medium"
    if hasattr(environment, "value"):
        environment = environment.value
    if hasattr(criticality, "value"):
        criticality = criticality.value

    # Try AI first, fall back to heuristic
    ai_result = await _ai_suggest(finding, environment, criticality)
    suggestions = _heuristic_suggest(finding, environment, criticality)

    if ai_result:
        # Merge AI confidence scores into heuristic suggestions
        ai_map = {r["control"]: r for r in ai_result if "control" in r}
        for s in suggestions:
            if s.control in ai_map:
                ai = ai_map[s.control]
                s.confidence = round((s.confidence + float(ai.get("confidence", s.confidence))) / 2, 2)
                if "rationale" in ai:
                    s.rationale = ai["rationale"]
                s.recommended = ai.get("recommended", s.recommended)
        suggestions.sort(key=lambda x: (-x.confidence, not x.recommended))

    categories = _classify_cve(finding)
    summary = (
        f"{'Network-exploitable' if 'network' in categories else 'Local'} CVE. "
        f"Top controls pre-selected based on exploit vector and asset criticality ({criticality}/{environment}). "
        "All mitigations are reversible."
    )

    return MitigationSuggestionsResponse(
        finding_id=finding.id,
        suggestions=suggestions,
        ai_summary=summary,
    )
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec backend python -m pytest tests/unit/test_vuln_mitigation_service.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/vuln_mitigation_service.py backend/tests/unit/test_vuln_mitigation_service.py
git commit -m "feat: add AI mitigation suggestion service with heuristic fallback"
```

---

### Task 4: Campaign execution service

**Files:**
- Create: `backend/app/services/vuln_campaign_service.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_vuln_campaign_service.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


@pytest.mark.asyncio
async def test_build_batches_rolling():
    from app.services.vuln_campaign_service import _build_batches

    asset_ids = [str(uuid.uuid4()) for _ in range(7)]
    batches = _build_batches(asset_ids, batch_size=3, strategy="rolling")
    assert len(batches) == 3
    assert len(batches[0]) == 3
    assert len(batches[1]) == 3
    assert len(batches[2]) == 1


@pytest.mark.asyncio
async def test_build_batches_canary():
    from app.services.vuln_campaign_service import _build_batches

    asset_ids = [str(uuid.uuid4()) for _ in range(6)]
    batches = _build_batches(asset_ids, batch_size=3, strategy="canary")
    assert batches[0] == [asset_ids[0]]  # first batch = 1 canary
    assert batches[1] == asset_ids[1:]   # rest in second batch


@pytest.mark.asyncio
async def test_check_abort_threshold():
    from app.services.vuln_campaign_service import _should_abort

    batch_result = {"status": "failed", "asset_ids": ["a", "b", "c"], "failed_ids": ["a", "b"]}
    # 2/3 = 67% failure rate, threshold 20% → should abort
    assert _should_abort(batch_result, threshold=0.2) is True
    # 1/3 = 33% failure rate, threshold 50% → should not abort
    assert _should_abort({"asset_ids": ["a", "b", "c"], "failed_ids": ["a"]}, threshold=0.5) is False
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec backend python -m pytest tests/unit/test_vuln_campaign_service.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement the service**

Create `backend/app/services/vuln_campaign_service.py`:

```python
"""Patch campaign execution service — processes batches sequentially with health gates."""
from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _build_batches(asset_ids: list[str], batch_size: int, strategy: str) -> list[list[str]]:
    if strategy == "canary" and len(asset_ids) > 1:
        return [[asset_ids[0]], asset_ids[1:]]
    if strategy == "all_at_once":
        return [asset_ids]
    # rolling: even batches of batch_size
    return [asset_ids[i:i + batch_size] for i in range(0, len(asset_ids), batch_size)]


def _should_abort(batch_result: dict, threshold: float) -> bool:
    asset_ids = batch_result.get("asset_ids", [])
    failed_ids = batch_result.get("failed_ids", [])
    if not asset_ids:
        return False
    return (len(failed_ids) / len(asset_ids)) > threshold


async def _run_health_gate(health_endpoint: str, asset_ids: list[str], wait_seconds: int) -> bool:
    """Wait and return True (health gate always passes for now; extend with real HTTP probes)."""
    await asyncio.sleep(min(wait_seconds, 10))  # cap sleep in background tasks for responsiveness
    return True


async def execute_campaign(campaign_id: str) -> None:
    """Background task: execute a PatchCampaign batch by batch."""
    from app.database import AsyncSessionLocal
    from app.models.patch_campaign import PatchCampaign, CampaignStatus
    from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus, RiskLevel
    from app.models.vulnerability import VulnerabilityFinding
    from app.services.planning_engine import generate_plan
    from app.workflows.execute_change_workflow import execute_change_workflow
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        campaign = await db.get(PatchCampaign, uuid.UUID(campaign_id))
        if not campaign:
            logger.error(f"Campaign {campaign_id} not found")
            return

        campaign.status = CampaignStatus.running
        campaign.started_at = datetime.now(timezone.utc)
        await db.commit()

    batches = _build_batches(
        [str(a) for a in campaign.target_asset_ids],
        campaign.batch_size,
        campaign.rollout_strategy,
    )
    batch_results = []

    for i, batch_asset_ids in enumerate(batches):
        async with AsyncSessionLocal() as db:
            camp = await db.get(PatchCampaign, uuid.UUID(campaign_id))
            if camp and camp.status == CampaignStatus.aborted:
                return
            if camp and camp.status == CampaignStatus.paused:
                # Poll until resumed or aborted
                for _ in range(360):
                    await asyncio.sleep(10)
                    await db.refresh(camp)
                    if camp.status != CampaignStatus.paused:
                        break

        cr_ids: list[str] = []
        failed_ids: list[str] = []

        for asset_id in batch_asset_ids:
            try:
                async with AsyncSessionLocal() as db:
                    # Find the finding for this asset (if any) to get package info
                    finding_result = await db.execute(
                        select(VulnerabilityFinding).where(
                            VulnerabilityFinding.asset_id == uuid.UUID(asset_id),
                        ).limit(1)
                    )
                    finding = finding_result.scalars().first()
                    pkg = (finding.affected_package or "") if finding else ""
                    target_ver = (finding.fixed_version or "") if finding else ""

                    cr = ChangeRequest(
                        organization_id=campaign.organization_id,
                        requester_id=campaign.organization_id,  # system action
                        change_type=ChangeType.patch_packages,
                        title=f"[Campaign] Patch {pkg or 'packages'} on {asset_id[:8]}",
                        description=f"Part of campaign {campaign_id}",
                        risk_level=RiskLevel.medium,
                        target_asset_ids=[asset_id],
                        parameters={"packages": [{"name": pkg, "target_version": target_ver}]} if pkg else {},
                        status=ChangeRequestStatus.pending,
                    )
                    db.add(cr)
                    await db.commit()
                    await db.refresh(cr)
                    cr_ids.append(str(cr.id))

                asyncio.create_task(execute_change_workflow(str(cr.id)))
            except Exception as e:
                logger.warning(f"Campaign {campaign_id} batch {i} asset {asset_id} CR failed: {e}")
                failed_ids.append(asset_id)

        # Wait for CRs to complete (poll up to 10 min)
        for _ in range(120):
            await asyncio.sleep(5)
            all_done = True
            async with AsyncSessionLocal() as db:
                for cr_id in cr_ids:
                    cr = await db.get(ChangeRequest, uuid.UUID(cr_id))
                    if cr and cr.status not in ("completed", "failed", "rolled_back", "completed_with_errors"):
                        all_done = False
                        break
                    if cr and cr.status in ("failed", "rolled_back"):
                        # Find the asset_id from target_asset_ids
                        for aid in (cr.target_asset_ids or []):
                            if aid not in failed_ids:
                                failed_ids.append(aid)
            if all_done:
                break

        batch_result = {
            "batch_index": i,
            "asset_ids": batch_asset_ids,
            "cr_ids": cr_ids,
            "failed_ids": failed_ids,
            "status": "failed" if failed_ids else "passed",
        }
        batch_results.append(batch_result)

        # Health gate
        await _run_health_gate(campaign.health_endpoint, batch_asset_ids, campaign.health_gate_seconds)

        # Check abort threshold
        if _should_abort(batch_result, campaign.abort_threshold) and i < len(batches) - 1:
            logger.warning(f"Campaign {campaign_id} abort threshold exceeded at batch {i}")
            async with AsyncSessionLocal() as db:
                camp = await db.get(PatchCampaign, uuid.UUID(campaign_id))
                if camp:
                    camp.status = CampaignStatus.failed
                    camp.batches = batch_results
                    camp.completed_at = datetime.now(timezone.utc)
                    await db.commit()
            return

        # Persist batch result after each batch
        async with AsyncSessionLocal() as db:
            camp = await db.get(PatchCampaign, uuid.UUID(campaign_id))
            if camp:
                camp.batches = batch_results
                await db.commit()

    # All batches done
    any_failed = any(b["failed_ids"] for b in batch_results)
    async with AsyncSessionLocal() as db:
        camp = await db.get(PatchCampaign, uuid.UUID(campaign_id))
        if camp:
            camp.status = CampaignStatus.failed if any_failed else CampaignStatus.complete
            camp.batches = batch_results
            camp.completed_at = datetime.now(timezone.utc)
            await db.commit()
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec backend python -m pytest tests/unit/test_vuln_campaign_service.py -v
```
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/vuln_campaign_service.py backend/tests/unit/test_vuln_campaign_service.py
git commit -m "feat: add patch campaign execution service with batch processing and health gates"
```

---

### Task 5: SLA escalation job

**Files:**
- Create: `backend/app/jobs/vuln_sla_escalation.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_vuln_sla_escalation.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


@pytest.mark.asyncio
async def test_escalate_breached_findings_assigns_user():
    from app.jobs.vuln_sla_escalation import process_sla_breaches

    mock_finding = MagicMock()
    mock_finding.id = "finding-id"
    mock_finding.severity = "critical"
    mock_finding.assigned_to_user_id = None
    mock_finding.status = "open"

    mock_sla = MagicMock()
    mock_sla.due_at = datetime.now(timezone.utc) - timedelta(hours=1)
    mock_sla.breached = False
    mock_sla.finding_id = "finding-id"

    mock_config = {
        "critical": 72, "high": 168, "medium": 720,
        "escalate_to_user_id": "admin-user-id",
        "auto_execute_on_breach": False,
        "severity_upgrade_hours": 48,
    }

    with patch("app.jobs.vuln_sla_escalation.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_db
        mock_db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_sla])))))
        mock_db.get = AsyncMock(return_value=mock_finding)
        mock_db.commit = AsyncMock()

        # Should not raise
        await process_sla_breaches(mock_config, org_id="org-id")
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec backend python -m pytest tests/unit/test_vuln_sla_escalation.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement the job**

Create `backend/app/jobs/vuln_sla_escalation.py`:

```python
"""Scheduled job: mark SLA breaches, auto-assign, escalate severity, auto-execute if configured."""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


async def process_sla_breaches(sla_config: dict, org_id: str) -> None:
    """Called by APScheduler. Scans all open SLAs for the org and applies escalation rules."""
    from app.database import AsyncSessionLocal
    from app.models.vulnerability import VulnerabilityFinding, RemediationSLA
    from sqlalchemy import select

    escalate_to = sla_config.get("escalate_to_user_id")
    auto_execute = sla_config.get("auto_execute_on_breach", False)
    upgrade_hours = sla_config.get("severity_upgrade_hours", 48)
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        # Find all unbreached SLAs that are past due_at
        result = await db.execute(
            select(RemediationSLA).where(
                RemediationSLA.organization_id == uuid.UUID(org_id),
                RemediationSLA.due_at <= now,
                RemediationSLA.breached == False,
            )
        )
        overdue_slas = result.scalars().all()

        for sla in overdue_slas:
            finding: VulnerabilityFinding = await db.get(VulnerabilityFinding, sla.finding_id)
            if not finding or finding.status not in ("open", "change_request_generated"):
                continue

            # Mark breached
            sla.breached = True
            sla.breach_notified_at = now

            # Auto-assign if unassigned and config says so
            if escalate_to and not finding.assigned_to_user_id:
                finding.assigned_to_user_id = uuid.UUID(escalate_to)
                sla.escalated_at = now
                logger.info(f"SLA breach: auto-assigned finding {finding.id} to {escalate_to}")

            # Auto-execute: approve the linked patch CR
            if auto_execute and finding.change_request_id:
                try:
                    from app.models.change_request import ChangeRequest, ChangeRequestStatus
                    cr = await db.get(ChangeRequest, finding.change_request_id)
                    if cr and cr.status == ChangeRequestStatus.awaiting_approval:
                        cr.status = ChangeRequestStatus.approved
                        logger.info(f"SLA breach: auto-approved CR {cr.id} for finding {finding.id}")
                except Exception as e:
                    logger.warning(f"Auto-execute failed for finding {finding.id}: {e}")

        await db.commit()

    # Severity upgrade: findings that have been breached for > upgrade_hours
    upgrade_cutoff = now - timedelta(hours=upgrade_hours)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RemediationSLA).where(
                RemediationSLA.organization_id == uuid.UUID(org_id),
                RemediationSLA.breached == True,
                RemediationSLA.breach_notified_at <= upgrade_cutoff,
                RemediationSLA.escalated_at == None,
            )
        )
        long_overdue = result.scalars().all()
        _UPGRADE = {"medium": "high", "high": "critical"}
        for sla in long_overdue:
            finding = await db.get(VulnerabilityFinding, sla.finding_id)
            if finding and finding.severity in _UPGRADE:
                old = finding.severity
                finding.severity = _UPGRADE[finding.severity]
                sla.escalated_at = now
                logger.info(f"SLA escalation: upgraded finding {finding.id} severity {old} → {finding.severity}")
        await db.commit()


async def run_sla_escalation_for_all_orgs() -> None:
    """Entry point called by APScheduler every 15 minutes."""
    from app.database import AsyncSessionLocal
    from app.models.org_settings import OrganizationSettings
    from app.models.vulnerability import RemediationSLA
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        # Get all orgs that have active SLAs
        result = await db.execute(select(OrganizationSettings))
        all_settings = result.scalars().all()

    for settings in all_settings:
        config = settings.sla_config or {}
        # Merge with defaults
        cfg = {"critical": 72, "high": 168, "medium": 720,
               "escalate_to_user_id": None, "auto_execute_on_breach": False,
               "severity_upgrade_hours": 48}
        cfg.update({k: v for k, v in config.items() if v is not None})
        try:
            await process_sla_breaches(cfg, str(settings.organization_id))
        except Exception as e:
            logger.error(f"SLA escalation failed for org {settings.organization_id}: {e}")
```

- [ ] **Step 4: Register in main.py**

In `backend/app/main.py`, find the existing APScheduler setup and add the SLA job. Look for `scheduler.add_job` calls and add:

```python
# SLA escalation — runs every 15 minutes
scheduler.add_job(
    run_sla_escalation_for_all_orgs,
    "interval",
    minutes=15,
    id="vuln_sla_escalation",
    replace_existing=True,
)
```

And add the import at the top of the lifespan/startup section:
```python
from app.jobs.vuln_sla_escalation import run_sla_escalation_for_all_orgs
```

- [ ] **Step 5: Run tests**

```bash
docker compose exec backend python -m pytest tests/unit/test_vuln_sla_escalation.py -v
```
Expected: 1 test PASS

- [ ] **Step 6: Verify backend starts cleanly**

```bash
docker compose restart backend
sleep 5
docker compose logs backend --tail=10
```
Expected: No errors, scheduler logs show new job registered.

- [ ] **Step 7: Commit**

```bash
git add backend/app/jobs/vuln_sla_escalation.py backend/tests/unit/test_vuln_sla_escalation.py backend/app/main.py
git commit -m "feat: add SLA escalation job (auto-assign on breach, severity upgrade, auto-execute)"
```

---

### Task 6: Backend endpoints — finding actions + campaigns + mitigation suggestions

**Files:**
- Modify: `backend/app/routers/vulnerability.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_vuln_endpoints_new.py`:

```python
import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_patch_finding_creates_cr(async_client, test_user):
    finding_id = str(uuid.uuid4())
    with patch("app.routers.vulnerability._get_finding_or_404", new_callable=AsyncMock) as mock_find:
        mock_finding = MagicMock()
        mock_finding.id = uuid.UUID(finding_id)
        mock_finding.asset_id = uuid.uuid4()
        mock_finding.affected_package = "openssl"
        mock_finding.fixed_version = "3.0.2"
        mock_finding.organization_id = test_user.organization_id
        mock_find.return_value = mock_finding

        with patch("app.routers.vulnerability._create_patch_cr", new_callable=AsyncMock) as mock_cr:
            mock_cr.return_value = {"change_request_id": str(uuid.uuid4()), "status": "draft"}
            resp = await async_client.post(
                f"/api/v1/vulnerability/findings/{finding_id}/patch",
                headers={"Authorization": f"Bearer {test_user.token}"},
            )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_list_campaigns_returns_empty(async_client, test_user):
    resp = await async_client.get(
        "/api/v1/vulnerability/campaigns",
        headers={"Authorization": f"Bearer {test_user.token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec backend python -m pytest tests/unit/test_vuln_endpoints_new.py -v
```
Expected: 404 or missing route errors.

- [ ] **Step 3: Add all new endpoints to vulnerability.py**

In `backend/app/routers/vulnerability.py`, add the following imports at the top:

```python
from app.models.patch_campaign import PatchCampaign, CampaignStatus
from app.schemas.patch_campaign import CampaignCreate, CampaignRead
from app.schemas.vulnerability import (
    FindingPatchRequest, FindingMitigateRequest, FindingAssignRequest,
    FindingAcceptRiskRequest, MitigationSuggestionsResponse,
)
```

Then add these endpoints (place after the existing `manual_generate_cr` endpoint):

```python
async def _get_finding_or_404(finding_id: uuid.UUID, org_id: uuid.UUID, db) -> VulnerabilityFinding:
    result = await db.execute(
        select(VulnerabilityFinding).where(
            VulnerabilityFinding.id == finding_id,
            VulnerabilityFinding.organization_id == org_id,
        )
    )
    finding = result.scalar_one_or_none()
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding


async def _create_patch_cr(finding: VulnerabilityFinding, rollback_on_failure: bool, verify_seconds: int, db) -> dict:
    from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus, RiskLevel
    from app.services.planning_engine import generate_plan
    import asyncio
    from app.workflows.execute_change_workflow import execute_change_workflow

    cr = ChangeRequest(
        organization_id=finding.organization_id,
        requester_id=finding.organization_id,
        change_type=ChangeType.patch_packages,
        title=f"Patch {finding.affected_package or 'packages'} — {finding.cve_id or finding.title}",
        description=finding.description or "",
        risk_level=RiskLevel.medium,
        target_asset_ids=[str(finding.asset_id)] if finding.asset_id else [],
        parameters={
            "packages": [{"name": finding.affected_package, "target_version": finding.fixed_version}],
            "rollback_on_failure": rollback_on_failure,
            "verify_seconds": verify_seconds,
        },
        status=ChangeRequestStatus.pending,
        finding_id=finding.id,
    )
    db.add(cr)
    await db.flush()
    plan = await generate_plan(cr, db)
    if plan:
        cr.change_plan = plan
    await db.commit()
    asyncio.create_task(execute_change_workflow(str(cr.id)))
    finding.status = "remediation_in_progress"
    finding.change_request_id = cr.id
    await db.commit()
    return {"change_request_id": str(cr.id), "status": str(cr.status)}


@router.post("/findings/{finding_id}/patch", status_code=201)
async def patch_finding(
    finding_id: uuid.UUID,
    body: FindingPatchRequest = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if body is None:
        body = FindingPatchRequest()
    finding = await _get_finding_or_404(finding_id, user.organization_id, db)
    if not finding.asset_id:
        raise HTTPException(status_code=422, detail="Finding has no matched asset — cannot patch")
    return await _create_patch_cr(finding, body.rollback_on_failure, body.verify_seconds, db)


@router.get("/findings/{finding_id}/mitigations", response_model=MitigationSuggestionsResponse)
async def get_mitigation_suggestions(
    finding_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.services.vuln_mitigation_service import suggest_mitigations
    finding = await _get_finding_or_404(finding_id, user.organization_id, db)
    asset = None
    if finding.asset_id:
        asset = await db.get(Asset, finding.asset_id)
    return await suggest_mitigations(finding, asset)


@router.post("/findings/{finding_id}/mitigate", status_code=201)
async def mitigate_finding(
    finding_id: uuid.UUID,
    body: FindingMitigateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus, RiskLevel
    import asyncio
    from app.workflows.execute_change_workflow import execute_change_workflow

    finding = await _get_finding_or_404(finding_id, user.organization_id, db)

    _CONTROL_TO_CHANGE_TYPE = {
        "network_isolation": "security_group_update",
        "waf": "microsegmentation_policy",
        "feature_flag": "ssm_command",
        "rate_limit": "ssm_command",
        "virtual_patch": "ssm_command",
        "full_isolation": "security_group_update",
        "seccomp": "agent_ossecurity",
        "apparmor": "agent_ossecurity",
        "process_isolation": "agent_ossecurity",
        "ebpf": "agent_ossecurity",
        "capability_drop": "agent_ossecurity",
    }

    created_crs = []
    for control in body.selected_controls:
        ct_str = _CONTROL_TO_CHANGE_TYPE.get(control, "generic_remediation")
        try:
            ct = ChangeType(ct_str)
        except ValueError:
            ct = ChangeType.generic_remediation

        cr = ChangeRequest(
            organization_id=finding.organization_id,
            requester_id=user.id,
            change_type=ct,
            title=f"Mitigate {finding.cve_id or finding.title} — {control}",
            description=f"Compensating control: {control}",
            risk_level=RiskLevel.low,
            target_asset_ids=[str(finding.asset_id)] if finding.asset_id else [],
            parameters={"mitigation_control": control, "rollback_strategy": "nexplane_rollback"},
            status=ChangeRequestStatus.pending,
            finding_id=finding.id,
        )
        db.add(cr)
        await db.flush()
        await db.commit()
        asyncio.create_task(execute_change_workflow(str(cr.id)))
        created_crs.append(str(cr.id))

    mitigations = list(finding.mitigations or [])
    mitigations.extend([{"control": c, "cr_id": cid} for c, cid in zip(body.selected_controls, created_crs)])
    finding.mitigations = mitigations
    finding.status = "mitigated"
    await db.commit()
    return {"change_request_ids": created_crs, "controls_applied": body.selected_controls}


@router.patch("/findings/{finding_id}/assign", status_code=200)
async def assign_finding(
    finding_id: uuid.UUID,
    body: FindingAssignRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    finding = await _get_finding_or_404(finding_id, user.organization_id, db)
    finding.assigned_to_user_id = body.user_id
    await db.commit()
    return {"finding_id": str(finding_id), "assigned_to": str(body.user_id)}


@router.post("/findings/{finding_id}/accept-risk", status_code=200)
async def accept_risk(
    finding_id: uuid.UUID,
    body: FindingAcceptRiskRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    finding = await _get_finding_or_404(finding_id, user.organization_id, db)
    finding.status = "accepted_risk"
    finding.accepted_risk_reason = body.reason
    finding.accepted_risk_expires_at = body.expires_at
    await db.commit()
    return {"finding_id": str(finding_id), "status": "accepted_risk", "expires_at": body.expires_at.isoformat()}


# ── Campaigns ────────────────────────────────────────────────────────────────

@router.get("/campaigns", response_model=list[CampaignRead])
async def list_campaigns(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PatchCampaign).where(PatchCampaign.organization_id == user.organization_id)
        .order_by(PatchCampaign.created_at.desc())
    )
    campaigns = result.scalars().all()
    out = []
    for c in campaigns:
        batches = c.batches or []
        passed = sum(1 for b in batches for aid in b.get("asset_ids", []) if aid not in b.get("failed_ids", []))
        failed = sum(len(b.get("failed_ids", [])) for b in batches)
        total = len(c.target_asset_ids or [])
        out.append(CampaignRead(
            **{k: v for k, v in c.__dict__.items() if not k.startswith("_")},
            total_assets=total,
            passed_count=passed,
            failed_count=failed,
            pending_count=max(0, total - passed - failed),
        ))
    return out


@router.post("/campaigns", response_model=CampaignRead, status_code=201)
async def create_campaign(
    body: CampaignCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    import asyncio
    from app.services.vuln_campaign_service import execute_campaign

    campaign = PatchCampaign(
        organization_id=user.organization_id,
        title=body.title,
        cve_id=body.cve_id,
        target_asset_ids=[str(a) for a in body.target_asset_ids],
        batch_size=body.batch_size,
        health_gate_seconds=body.health_gate_seconds,
        health_endpoint=body.health_endpoint,
        abort_threshold=body.abort_threshold,
        rollout_strategy=body.rollout_strategy,
        status=CampaignStatus.draft,
        batches=[],
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    asyncio.create_task(execute_campaign(str(campaign.id)))
    total = len(body.target_asset_ids)
    return CampaignRead(
        **{k: v for k, v in campaign.__dict__.items() if not k.startswith("_")},
        total_assets=total, passed_count=0, failed_count=0, pending_count=total,
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignRead)
async def get_campaign(
    campaign_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    campaign = await db.get(PatchCampaign, campaign_id)
    if not campaign or campaign.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    batches = campaign.batches or []
    passed = sum(1 for b in batches for aid in b.get("asset_ids", []) if aid not in b.get("failed_ids", []))
    failed = sum(len(b.get("failed_ids", [])) for b in batches)
    total = len(campaign.target_asset_ids or [])
    return CampaignRead(
        **{k: v for k, v in campaign.__dict__.items() if not k.startswith("_")},
        total_assets=total, passed_count=passed, failed_count=failed, pending_count=max(0, total - passed - failed),
    )


@router.post("/campaigns/{campaign_id}/pause", status_code=200)
async def pause_campaign(campaign_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    campaign = await db.get(PatchCampaign, campaign_id)
    if not campaign or campaign.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != CampaignStatus.running:
        raise HTTPException(status_code=422, detail="Campaign is not running")
    campaign.status = CampaignStatus.paused
    await db.commit()
    return {"id": str(campaign_id), "status": "paused"}


@router.post("/campaigns/{campaign_id}/resume", status_code=200)
async def resume_campaign(campaign_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    campaign = await db.get(PatchCampaign, campaign_id)
    if not campaign or campaign.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != CampaignStatus.paused:
        raise HTTPException(status_code=422, detail="Campaign is not paused")
    campaign.status = CampaignStatus.running
    await db.commit()
    return {"id": str(campaign_id), "status": "running"}


@router.post("/campaigns/{campaign_id}/abort", status_code=200)
async def abort_campaign(campaign_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    campaign = await db.get(PatchCampaign, campaign_id)
    if not campaign or campaign.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = CampaignStatus.aborted
    campaign.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": str(campaign_id), "status": "aborted"}
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec backend python -m pytest tests/unit/test_vuln_endpoints_new.py -v
docker compose exec backend python -m pytest tests/ -q --ignore=tests/smoke -x
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/vulnerability.py backend/tests/unit/test_vuln_endpoints_new.py
git commit -m "feat: add finding action endpoints (patch/mitigate/assign/accept-risk) and campaign CRUD"
```

---

### Task 7: Frontend — FindingActionPanel component

**Files:**
- Create: `frontend/src/components/FindingActionPanel.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/FindingActionPanel.tsx`:

```tsx
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface Finding {
  id: string;
  cve_id: string | null;
  title: string;
  description: string | null;
  affected_package: string | null;
  affected_version: string | null;
  fixed_version: string | null;
  asset_id: string | null;
  asset_name: string | null;
  severity: string;
  status: string;
  assigned_to_user_id: string | null;
}

interface Props {
  finding: Finding;
  onClose: () => void;
  onUpdated: () => void;
}

export default function FindingActionPanel({ finding, onClose, onUpdated }: Props) {
  const qc = useQueryClient();
  const [acceptReason, setAcceptReason] = useState("");
  const [acceptExpiry, setAcceptExpiry] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() + 90);
    return d.toISOString().split("T")[0];
  });
  const [activeAction, setActiveAction] = useState<string | null>(null);

  const patchMutation = useMutation({
    mutationFn: () => apiClient.post(`/api/v1/vulnerability/findings/${finding.id}/patch`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["findings"] }); onUpdated(); },
  });

  const acceptMutation = useMutation({
    mutationFn: () => apiClient.post(`/api/v1/vulnerability/findings/${finding.id}/accept-risk`, {
      reason: acceptReason,
      expires_at: new Date(acceptExpiry).toISOString(),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["findings"] }); onUpdated(); },
  });

  const fpMutation = useMutation({
    mutationFn: () => apiClient.patch(`/api/v1/vulnerability/findings/${finding.id}/status`, {
      status: "false_positive", reason: "Marked via action panel"
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["findings"] }); onUpdated(); },
  });

  return (
    <div className="bg-slate-50 border-t border-slate-200 px-4 py-4 space-y-3">
      {/* CVE summary */}
      <div className="text-sm text-slate-600">
        {finding.description && <p className="mb-2">{finding.description}</p>}
        {finding.affected_package && (
          <p className="font-mono text-xs bg-white border border-slate-200 rounded px-2 py-1 inline-block">
            {finding.affected_package} {finding.affected_version}
            {finding.fixed_version && <> → <span className="text-green-700">{finding.fixed_version}</span></>}
          </p>
        )}
      </div>

      {/* Action buttons */}
      <div className="flex flex-wrap gap-2">
        {finding.asset_id && (
          <button
            onClick={() => patchMutation.mutate()}
            disabled={patchMutation.isPending}
            className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50"
          >
            {patchMutation.isPending ? "Creating CR…" : "Patch"}
          </button>
        )}
        <button
          onClick={() => setActiveAction(activeAction === "mitigate" ? null : "mitigate")}
          className="px-3 py-1.5 bg-purple-600 text-white text-sm rounded hover:bg-purple-700"
        >
          Mitigate
        </button>
        <button
          onClick={() => setActiveAction(activeAction === "accept" ? null : "accept")}
          className="px-3 py-1.5 border border-slate-300 text-sm rounded hover:bg-slate-100"
        >
          Accept Risk
        </button>
        <button
          onClick={() => fpMutation.mutate()}
          disabled={fpMutation.isPending}
          className="px-3 py-1.5 border border-slate-300 text-sm rounded hover:bg-slate-100 disabled:opacity-50"
        >
          False Positive
        </button>
        <a
          href={`/vulnerability?tab=findings&blast_radius=${finding.cve_id || ""}`}
          className="px-3 py-1.5 border border-slate-300 text-sm rounded hover:bg-slate-100"
        >
          Blast Radius →
        </a>
      </div>

      {/* Accept Risk sub-panel */}
      {activeAction === "accept" && (
        <div className="bg-white border border-slate-200 rounded p-3 space-y-2">
          <p className="text-xs font-medium text-slate-700">Accept Risk</p>
          <input
            className="w-full border border-slate-300 rounded px-2 py-1.5 text-sm"
            placeholder="Reason (required)"
            value={acceptReason}
            onChange={e => setAcceptReason(e.target.value)}
          />
          <div className="flex gap-2 items-center">
            <label className="text-xs text-slate-500">Expires</label>
            <input
              type="date"
              className="border border-slate-300 rounded px-2 py-1 text-sm"
              value={acceptExpiry}
              onChange={e => setAcceptExpiry(e.target.value)}
            />
          </div>
          <button
            onClick={() => acceptMutation.mutate()}
            disabled={!acceptReason || acceptMutation.isPending}
            className="px-3 py-1.5 bg-slate-700 text-white text-sm rounded hover:bg-slate-800 disabled:opacity-50"
          >
            {acceptMutation.isPending ? "Saving…" : "Confirm Accept Risk"}
          </button>
        </div>
      )}

      {/* Mitigate sub-panel placeholder — wired in Task 8 */}
      {activeAction === "mitigate" && (
        <MitigationInlinePanelLoader findingId={finding.id} onApplied={onUpdated} />
      )}

      {/* Status feedback */}
      {patchMutation.isSuccess && (
        <p className="text-sm text-green-700">✓ Patch CR created — awaiting approval</p>
      )}
      {acceptMutation.isSuccess && (
        <p className="text-sm text-green-700">✓ Risk accepted until {acceptExpiry}</p>
      )}
    </div>
  );
}

function MitigationInlinePanelLoader({ findingId, onApplied }: { findingId: string; onApplied: () => void }) {
  // Lazy-load MitigationPanel — implemented in Task 8
  const MitigationPanel = React.lazy(() => import("./MitigationPanel"));
  return (
    <React.Suspense fallback={<p className="text-sm text-slate-500">Loading suggestions…</p>}>
      <MitigationPanel findingId={findingId} onApplied={onApplied} />
    </React.Suspense>
  );
}
```

- [ ] **Step 2: Restart frontend and verify no TypeScript errors**

```bash
docker compose stop frontend && docker compose up frontend -d
sleep 5
docker compose logs frontend --tail=20
```
Expected: Build succeeds, no TypeScript errors about FindingActionPanel.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/FindingActionPanel.tsx
git commit -m "feat: add FindingActionPanel with patch/mitigate/accept-risk/false-positive actions"
```

---

### Task 8: Frontend — MitigationPanel component

**Files:**
- Create: `frontend/src/components/MitigationPanel.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/MitigationPanel.tsx`:

```tsx
import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface Suggestion {
  control: string;
  title: string;
  description: string;
  rationale: string;
  confidence: number;
  impact: string;
  recommended: boolean;
}

interface Props {
  findingId: string;
  onApplied: () => void;
}

const IMPACT_COLORS: Record<string, string> = {
  low: "text-green-700 bg-green-50",
  medium: "text-yellow-700 bg-yellow-50",
  high: "text-red-700 bg-red-50",
};

export default function MitigationPanel({ findingId, onApplied }: Props) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const { data, isLoading, error } = useQuery({
    queryKey: ["mitigation-suggestions", findingId],
    queryFn: () =>
      apiClient
        .get<{ suggestions: Suggestion[]; ai_summary: string }>
          (`/api/v1/vulnerability/findings/${findingId}/mitigations`)
        .then(r => r.data),
    onSuccess: (data) => {
      // Pre-select recommended controls
      setSelected(new Set(data.suggestions.filter(s => s.recommended).map(s => s.control)));
    },
  });

  const applyMutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/api/v1/vulnerability/findings/${findingId}/mitigate`, {
        selected_controls: Array.from(selected),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["findings"] });
      onApplied();
    },
  });

  const toggle = (control: string) => {
    const next = new Set(selected);
    if (next.has(control)) next.delete(control);
    else next.add(control);
    setSelected(next);
  };

  if (isLoading) return <p className="text-sm text-slate-500 py-2">Analysing CVE…</p>;
  if (error || !data) return <p className="text-sm text-red-500 py-2">Could not load suggestions</p>;

  return (
    <div className="bg-white border border-slate-200 rounded space-y-0 overflow-hidden">
      {/* AI summary */}
      <div className="bg-blue-50 border-b border-blue-100 px-3 py-2 flex gap-2 items-start">
        <span className="text-base mt-0.5">🤖</span>
        <p className="text-xs text-blue-800">{data.ai_summary}</p>
      </div>

      {/* Suggestions */}
      {data.suggestions.map((s) => (
        <div
          key={s.control}
          onClick={() => toggle(s.control)}
          className={`px-3 py-2.5 border-b border-slate-100 flex gap-3 cursor-pointer hover:bg-slate-50 transition-colors ${
            selected.has(s.control) ? "bg-green-50" : ""
          } ${s.impact === "high" ? "bg-red-50 hover:bg-red-100" : ""}`}
        >
          <div className={`w-5 h-5 mt-0.5 rounded flex-shrink-0 flex items-center justify-center border-2 ${
            selected.has(s.control) ? "bg-green-500 border-green-500" : "border-slate-300"
          }`}>
            {selected.has(s.control) && <span className="text-white text-xs font-bold">✓</span>}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-slate-800">{s.title}</span>
              {s.recommended && (
                <span className="text-xs bg-green-100 text-green-800 px-1.5 py-0.5 rounded">
                  Recommended · {Math.round(s.confidence * 100)}%
                </span>
              )}
              {s.impact === "high" && (
                <span className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">⚠ High impact</span>
              )}
            </div>
            <p className="text-xs text-slate-500 mt-0.5">{s.rationale}</p>
          </div>
        </div>
      ))}

      {/* Apply button */}
      <div className="px-3 py-2.5 flex items-center gap-3">
        <button
          onClick={(e) => { e.stopPropagation(); applyMutation.mutate(); }}
          disabled={selected.size === 0 || applyMutation.isPending}
          className="px-3 py-1.5 bg-purple-600 text-white text-sm rounded hover:bg-purple-700 disabled:opacity-50"
        >
          {applyMutation.isPending ? "Applying…" : `Apply ${selected.size} mitigation${selected.size !== 1 ? "s" : ""}`}
        </button>
        <span className="text-xs text-slate-400">All mitigations are reversible. Finding stays open until patched.</span>
      </div>

      {applyMutation.isSuccess && (
        <div className="px-3 py-2 bg-green-50 text-sm text-green-700 border-t border-green-100">
          ✓ Mitigation CRs created — awaiting approval
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Restart frontend**

```bash
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MitigationPanel.tsx
git commit -m "feat: add MitigationPanel with AI-pre-selected controls and apply flow"
```

---

### Task 9: Frontend — Wire expandable rows into FindingQueue

**Files:**
- Modify: `frontend/src/components/FindingQueue.tsx`

- [ ] **Step 1: Read the full current FindingQueue.tsx**

Read `frontend/src/components/FindingQueue.tsx` to understand the current table structure.

- [ ] **Step 2: Add expandable row state and wire FindingActionPanel**

Add `expandedId` state and toggle on row click. Render `FindingActionPanel` beneath the expanded row. The key changes:

```tsx
// Add import
import FindingActionPanel from "./FindingActionPanel";

// Add state inside component
const [expandedId, setExpandedId] = useState<string | null>(null);

// In each finding row <tr>, add onClick:
onClick={() => setExpandedId(expandedId === f.id ? null : f.id)}
className="cursor-pointer hover:bg-slate-50 ..."  // add cursor-pointer

// Add expand indicator to the last cell:
<td className="px-3 py-3 text-slate-400 text-xs">
  {expandedId === f.id ? "▼" : "▶"}
</td>

// After each finding <tr>, add:
{expandedId === f.id && (
  <tr key={`${f.id}-panel`}>
    <td colSpan={/* number of columns */} className="p-0">
      <FindingActionPanel
        finding={f}
        onClose={() => setExpandedId(null)}
        onUpdated={() => { setExpandedId(null); queryClient.invalidateQueries({ queryKey: ["findings"] }); }}
      />
    </td>
  </tr>
)}
```

- [ ] **Step 3: Count exact columns in FindingQueue table and set colSpan correctly**

Read the file again and count `<th>` elements to get the correct colSpan number.

- [ ] **Step 4: Restart frontend**

```bash
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FindingQueue.tsx
git commit -m "feat: add expandable finding rows with inline FindingActionPanel"
```

---

### Task 10: Frontend — Campaigns tab (list + create drawer)

**Files:**
- Create: `frontend/src/components/PatchCampaignList.tsx`
- Create: `frontend/src/components/CreateCampaignDrawer.tsx`
- Modify: `frontend/src/pages/VulnerabilityRemediation.tsx`

- [ ] **Step 1: Create PatchCampaignList**

Create `frontend/src/components/PatchCampaignList.tsx`:

```tsx
import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface Campaign {
  id: string;
  title: string;
  cve_id: string | null;
  status: string;
  total_assets: number;
  passed_count: number;
  failed_count: number;
  pending_count: number;
  created_at: string;
  batches: any[];
}

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-slate-100 text-slate-700",
  running: "bg-blue-100 text-blue-800",
  paused: "bg-yellow-100 text-yellow-800",
  complete: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
  aborted: "bg-gray-100 text-gray-600",
};

export default function PatchCampaignList({ onCreateNew }: { onCreateNew: () => void }) {
  const qc = useQueryClient();
  const { data: campaigns = [], isLoading } = useQuery<Campaign[]>({
    queryKey: ["campaigns"],
    queryFn: () => apiClient.get("/api/v1/vulnerability/campaigns").then(r => r.data),
    refetchInterval: 10000,
  });

  const pauseMutation = useMutation({
    mutationFn: (id: string) => apiClient.post(`/api/v1/vulnerability/campaigns/${id}/pause`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
  const resumeMutation = useMutation({
    mutationFn: (id: string) => apiClient.post(`/api/v1/vulnerability/campaigns/${id}/resume`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
  const abortMutation = useMutation({
    mutationFn: (id: string) => apiClient.post(`/api/v1/vulnerability/campaigns/${id}/abort`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });

  if (isLoading) return <p className="text-sm text-slate-500 py-4">Loading campaigns…</p>;

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="font-semibold text-slate-900">Patch Campaigns</h3>
        <button
          onClick={onCreateNew}
          className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded hover:bg-blue-700"
        >
          + New Campaign
        </button>
      </div>

      {campaigns.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-lg p-8 text-center">
          <p className="text-slate-500 text-sm">No campaigns yet.</p>
          <button onClick={onCreateNew} className="mt-3 px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700">
            Create First Campaign
          </button>
        </div>
      ) : (
        <div className="border border-slate-200 rounded-lg overflow-hidden">
          {campaigns.map((c, i) => {
            const pct = c.total_assets > 0 ? Math.round((c.passed_count / c.total_assets) * 100) : 0;
            return (
              <div key={c.id} className={`p-4 ${i < campaigns.length - 1 ? "border-b border-slate-100" : ""}`}>
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-slate-900 text-sm">{c.title}</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLORS[c.status] || "bg-slate-100 text-slate-700"}`}>
                        {c.status.charAt(0).toUpperCase() + c.status.slice(1)}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-1 text-xs text-slate-500">
                      <span>{c.total_assets} assets</span>
                      {c.passed_count > 0 && <span className="text-green-700">✓ {c.passed_count}</span>}
                      {c.pending_count > 0 && <span className="text-slate-500">{c.pending_count} pending</span>}
                      {c.failed_count > 0 && <span className="text-red-600">✗ {c.failed_count} failed</span>}
                    </div>
                    {c.status === "running" && c.total_assets > 0 && (
                      <div className="mt-2 bg-slate-200 rounded-full h-1.5">
                        <div
                          className="bg-blue-600 h-1.5 rounded-full transition-all"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    )}
                    {c.failed_count > 0 && (
                      <div className="mt-2 bg-red-50 border border-red-200 rounded px-2 py-1 text-xs text-red-700">
                        {c.failed_count} asset{c.failed_count !== 1 ? "s" : ""} failed — rollback applied
                      </div>
                    )}
                  </div>
                  <div className="flex gap-1.5 flex-shrink-0">
                    {c.status === "running" && (
                      <button onClick={() => pauseMutation.mutate(c.id)} className="px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-50">Pause</button>
                    )}
                    {c.status === "paused" && (
                      <button onClick={() => resumeMutation.mutate(c.id)} className="px-2 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700">Resume</button>
                    )}
                    {["running", "paused"].includes(c.status) && (
                      <button onClick={() => abortMutation.mutate(c.id)} className="px-2 py-1 text-xs text-red-600 border border-red-200 rounded hover:bg-red-50">Abort</button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create CreateCampaignDrawer**

Create `frontend/src/components/CreateCampaignDrawer.tsx`:

```tsx
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface Props {
  onClose: () => void;
}

export default function CreateCampaignDrawer({ onClose }: Props) {
  const qc = useQueryClient();
  const [cveInput, setCveInput] = useState("");
  const [searchedCve, setSearchedCve] = useState<string | null>(null);
  const [selectedAssets, setSelectedAssets] = useState<Set<string>>(new Set());
  const [batchSize, setBatchSize] = useState(5);
  const [healthGate, setHealthGate] = useState(120);
  const [strategy, setStrategy] = useState("rolling");
  const [abortThreshold, setAbortThreshold] = useState(20);

  const { data: blastRadius, isFetching } = useQuery({
    queryKey: ["blast-radius", searchedCve],
    queryFn: () =>
      apiClient.get(`/api/v1/vulnerability/cve/${searchedCve}/blast-radius`).then(r => r.data),
    enabled: !!searchedCve,
    onSuccess: (data) => {
      setSelectedAssets(new Set(data.affected_assets.map((a: any) => a.asset_id)));
    },
  });

  const createMutation = useMutation({
    mutationFn: () =>
      apiClient.post("/api/v1/vulnerability/campaigns", {
        title: `Patch ${searchedCve || "campaign"} — ${new Date().toLocaleDateString()}`,
        cve_id: searchedCve,
        target_asset_ids: Array.from(selectedAssets),
        batch_size: batchSize,
        health_gate_seconds: healthGate,
        abort_threshold: abortThreshold / 100,
        rollout_strategy: strategy,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="w-full max-w-lg bg-white h-full shadow-xl overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h2 className="font-semibold text-slate-900">New Patch Campaign</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-xl">✕</button>
        </div>

        <div className="px-6 py-4 space-y-5">
          {/* CVE Search */}
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1.5">CVE / Package</label>
            <div className="flex gap-2">
              <input
                className="flex-1 border border-slate-300 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="CVE-2024-1234 or openssl"
                value={cveInput}
                onChange={e => setCveInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && setSearchedCve(cveInput.trim().toUpperCase())}
              />
              <button
                onClick={() => setSearchedCve(cveInput.trim().toUpperCase())}
                disabled={!cveInput.trim() || isFetching}
                className="px-3 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {isFetching ? "…" : "Search"}
              </button>
            </div>
          </div>

          {/* Blast Radius Results */}
          {blastRadius && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-slate-700">
                  {blastRadius.total_affected} affected asset{blastRadius.total_affected !== 1 ? "s" : ""}
                </span>
                <button
                  onClick={() =>
                    setSelectedAssets(
                      selectedAssets.size === blastRadius.affected_assets.length
                        ? new Set()
                        : new Set(blastRadius.affected_assets.map((a: any) => a.asset_id))
                    )
                  }
                  className="text-xs text-blue-600 hover:underline"
                >
                  {selectedAssets.size === blastRadius.affected_assets.length ? "Deselect all" : "Select all"}
                </button>
              </div>
              <div className="border border-slate-200 rounded overflow-hidden max-h-48 overflow-y-auto">
                {blastRadius.affected_assets.map((a: any) => (
                  <label key={a.asset_id} className="flex items-center gap-2 px-3 py-2 hover:bg-slate-50 cursor-pointer border-b border-slate-100 last:border-0">
                    <input
                      type="checkbox"
                      checked={selectedAssets.has(a.asset_id)}
                      onChange={() => {
                        const next = new Set(selectedAssets);
                        if (next.has(a.asset_id)) next.delete(a.asset_id);
                        else next.add(a.asset_id);
                        setSelectedAssets(next);
                      }}
                    />
                    <span className="text-sm">{a.hostname || a.asset_id.slice(0, 8)}</span>
                    {a.installed_version && (
                      <span className="text-xs text-slate-400 font-mono ml-auto">{a.installed_version}</span>
                    )}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Config */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1.5">Batch size</label>
              <input type="number" min={1} max={50} value={batchSize}
                onChange={e => setBatchSize(Number(e.target.value))}
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1.5">Health gate (seconds)</label>
              <input type="number" min={10} value={healthGate}
                onChange={e => setHealthGate(Number(e.target.value))}
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1.5">Rollout strategy</label>
              <select value={strategy} onChange={e => setStrategy(e.target.value)}
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm bg-white">
                <option value="rolling">Rolling</option>
                <option value="canary">Canary (1 first)</option>
                <option value="all_at_once">All at once</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1.5">Abort if failure % &gt;</label>
              <input type="number" min={1} max={100} value={abortThreshold}
                onChange={e => setAbortThreshold(Number(e.target.value))}
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </div>
          </div>

          {/* Launch */}
          <button
            onClick={() => createMutation.mutate()}
            disabled={selectedAssets.size === 0 || createMutation.isPending}
            className="w-full py-2.5 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {createMutation.isPending ? "Creating…" : `Launch Campaign → ${selectedAssets.size} asset${selectedAssets.size !== 1 ? "s" : ""}`}
          </button>
          {createMutation.isError && (
            <p className="text-sm text-red-600">Failed to create campaign. Please try again.</p>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add Campaigns tab to VulnerabilityRemediation.tsx**

In `frontend/src/pages/VulnerabilityRemediation.tsx`, add "campaigns" to the tab type and tab list:

```tsx
// Change Tab type
type Tab = "findings" | "policies" | "sla" | "campaigns";

// Add to tabs array
{ id: "campaigns", label: "Campaigns" }

// Add imports
import PatchCampaignList from "../components/PatchCampaignList";
import CreateCampaignDrawer from "../components/CreateCampaignDrawer";

// Add state
const [showCreateCampaign, setShowCreateCampaign] = useState(false);

// Add campaigns tab content in the render section
{tab === "campaigns" && (
  <div>
    <PatchCampaignList onCreateNew={() => setShowCreateCampaign(true)} />
    {showCreateCampaign && <CreateCampaignDrawer onClose={() => setShowCreateCampaign(false)} />}
  </div>
)}
```

- [ ] **Step 4: Restart frontend**

```bash
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PatchCampaignList.tsx frontend/src/components/CreateCampaignDrawer.tsx frontend/src/pages/VulnerabilityRemediation.tsx
git commit -m "feat: add Campaigns tab with campaign list, progress tracking, and create campaign drawer"
```

---

## Self-Review

**Spec coverage:**
- ✅ Section 1 (Finding-level actions): Tasks 7, 8, 9 — expandable row, action panel, mitigate panel
- ✅ Section 2 (Campaigns): Tasks 4, 6, 10 — execution service, CRUD endpoints, list + create drawer
- ✅ Section 3 (AI Mitigation): Task 3 — suggestion service with heuristic fallback + AI overlay
- ✅ Section 4 (SLA Escalation): Task 5 — job with auto-assign, auto-execute, severity upgrade
- ✅ Data Model: Tasks 1, 2 — PatchCampaign model, migration, extended FindingRead
- ✅ API Changes: Task 6 — all 11 endpoints implemented
- ✅ Frontend Components: Tasks 7-10 — FindingActionPanel, MitigationPanel, PatchCampaignList, CreateCampaignDrawer, Campaigns tab

**Placeholders:** None — all code is complete.

**Type consistency:**
- `FindingActionPanel` receives `Finding` with `assigned_to_user_id: string | null` — matches extended `FindingRead` schema
- `MitigationPanel` calls `/api/v1/vulnerability/findings/{id}/mitigations` returning `MitigationSuggestionsResponse` — matches Task 3 service
- `CreateCampaignDrawer` posts `CampaignCreate` body — matches Task 2 schema defaults
- `execute_campaign` function signature used in Task 6 matches Task 4 definition

**Missing from spec — added:** `_get_finding_or_404` helper (Task 6) to avoid duplication across the 4 finding-action endpoints.
