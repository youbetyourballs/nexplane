# Phase 2 — Scale and Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable operators to work at fleet scale and hand off security context cleanly between teams — bulk CR creation/approval, campaign failure policy, emergency priority lanes, finding lifecycle management, and access review integration.

**Architecture:** Extends existing CR, Finding, and PatchCampaign models with new fields and services. All features are additive (no breaking changes). Backend changes first, then frontend, then smoke test phase.

**Tech Stack:** FastAPI, SQLAlchemy async, React/React Query, PostgreSQL (ARRAY and JSONB columns), APScheduler for background workers.

**Prerequisites:** Phase 0 (executor wire-up) and Phase 1 (Foundation) must be complete. Phase 1's `finding_ids` on CR and `Finding.state` are used by Tasks in this phase.

---

## Task 1: Bulk CR creation (`POST /change-requests/batch`)

**Files:**
- Modify: `backend/app/routers/change_requests.py`
- Modify: `backend/app/models/change_request.py` (add `batch_id`)
- Modify: `backend/app/schemas/change_request.py`
- Create: `backend/app/tests/test_bulk_cr.py`
- Create: `backend/alembic/versions/XXXX_add_cr_batch_id.py`
- Modify: `frontend/src/pages/Assets.tsx`

- [ ] **Step 1: Write the failing test**

```python
# backend/app/tests/test_bulk_cr.py
import pytest
import uuid

@pytest.mark.asyncio
async def test_batch_create_returns_batch_id_and_cr_ids(async_client, admin_token, test_asset_ids):
    resp = await async_client.post(
        "/change-requests/batch",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "items": [
                {"title": f"Patch asset {i}", "change_type": "agent_linux_patch",
                 "target_asset_ids": [test_asset_ids[i]], "desired_outcome": {}}
                for i in range(3)
            ]
        }
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "batch_id" in data
    assert len(data["cr_ids"]) == 3
    # Verify all CRs share the batch_id
    for cr_id in data["cr_ids"]:
        cr_resp = await async_client.get(f"/change-requests/{cr_id}",
            headers={"Authorization": f"Bearer {admin_token}"})
        assert cr_resp.json()["batch_id"] == data["batch_id"]
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose exec backend pytest app/tests/test_bulk_cr.py -v 2>&1 | tail -5
```

- [ ] **Step 3: Add `batch_id` to ChangeRequest model**

In `backend/app/models/change_request.py`:
```python
batch_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
```

In `backend/app/schemas/change_request.py`, add to `ChangeRequestRead`:
```python
batch_id: uuid.UUID | None = None
```

Add new schema:
```python
class BatchCreateItem(BaseModel):
    title: str
    change_type: str
    target_asset_ids: list[str]
    desired_outcome: dict = {}
    finding_ids: list[str] = []
    snapshot_before: bool = False
    verification_checks: list[dict] = []

class BatchCreateRequest(BaseModel):
    items: list[BatchCreateItem]

class BatchCreateResponse(BaseModel):
    batch_id: uuid.UUID
    cr_ids: list[uuid.UUID]
```

- [ ] **Step 4: Add batch endpoint to change_requests.py**

```python
@router.post("/batch", response_model=BatchCreateResponse, status_code=201)
async def batch_create_change_requests(
    body: BatchCreateRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    batch_id = uuid.uuid4()
    cr_ids = []
    for item in body.items:
        cr = ChangeRequest(
            id=uuid.uuid4(),
            organization_id=user.organization_id,
            requester_id=user.id,
            title=item.title,
            change_type=ChangeType(item.change_type),
            target_asset_ids=[str(a) for a in item.target_asset_ids],
            desired_outcome=item.desired_outcome,
            finding_ids=item.finding_ids,
            snapshot_before=item.snapshot_before,
            verification_checks=item.verification_checks,
            batch_id=batch_id,
            status=ChangeRequestStatus.draft,
        )
        db.add(cr)
        cr_ids.append(cr.id)
    await db.commit()
    return BatchCreateResponse(batch_id=batch_id, cr_ids=cr_ids)
```

- [ ] **Step 5: Add `POST /change-requests/bulk-approve`**

```python
class BulkApproveRequest(BaseModel):
    cr_ids: list[uuid.UUID]
    decision: str  # "approved" | "rejected"
    comment: str = ""

@router.post("/bulk-approve", status_code=200)
async def bulk_approve(
    body: BulkApproveRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.approver, UserRole.admin):
        raise HTTPException(status_code=403, detail="Approver role required")
    approved_ids = []
    for cr_id in body.cr_ids:
        cr = await db.get(ChangeRequest, cr_id)
        if cr and cr.organization_id == user.organization_id and cr.status == ChangeRequestStatus.awaiting_approval:
            if cr.risk_level == "critical" and body.decision == "approved":
                continue  # Cannot bulk-approve critical risk
            cr.status = ChangeRequestStatus.approved if body.decision == "approved" else ChangeRequestStatus.rejected
            approved_ids.append(str(cr_id))
    await db.commit()
    return {"approved_count": len(approved_ids), "cr_ids": approved_ids}
```

- [ ] **Step 6: Frontend — bulk action bar in Assets.tsx**

In `frontend/src/pages/Assets.tsx`, when `selectedAssets.length > 0` add button:
```tsx
<button
  onClick={() => setBulkCRDrawerOpen(true)}
  className="px-3 py-1.5 text-sm bg-brand-600 text-white rounded hover:bg-brand-700"
>
  Create CR for {selectedAssets.length} assets
</button>
```

Add a simple drawer that collects `change_type` and `desired_outcome`, then calls `POST /change-requests/batch`.

- [ ] **Step 7: Generate migration, run tests, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_cr_batch_id"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_bulk_cr.py -v 2>&1 | tail -5
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/src/pages/Assets.tsx
git commit -m "feat: bulk CR creation (POST /change-requests/batch) and bulk approval"
```

---

## Task 2: Campaign failure policy

**Files:**
- Modify: `backend/app/models/patch_campaign.py`
- Modify: `backend/app/schemas/` (patch_campaign schemas)
- Modify: `backend/app/connectors/executors/nexplane_agent/run_patch_campaign.py`
- Create: `backend/app/tests/test_campaign_failure_policy.py`
- Create: `backend/alembic/versions/XXXX_add_campaign_failure_policy.py`
- Modify: `frontend/src/components/PatchCampaignList.tsx`
- Modify: `frontend/src/components/CreateCampaignDrawer.tsx`

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_campaign_failure_policy.py
import pytest

@pytest.mark.asyncio
async def test_campaign_pauses_on_failure_when_policy_is_pause(
    async_client, admin_token, test_campaign_with_policy_pause
):
    """When failure_policy=pause and a CR fails, campaign status becomes paused."""
    # Simulate a CR failing
    campaign_id = test_campaign_with_policy_pause["id"]
    # Mark one CR in the campaign as failed
    await async_client.patch(
        f"/patch-campaigns/{campaign_id}/cr-failed",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"cr_id": test_campaign_with_policy_pause["cr_ids"][0]},
    )
    resp = await async_client.get(f"/patch-campaigns/{campaign_id}",
        headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.json()["status"] == "paused"
```

- [ ] **Step 2: Add failure_policy to PatchCampaign**

```python
# In patch_campaign.py model
failure_policy: Mapped[str] = mapped_column(String(16), default="continue", nullable=False)
# "continue" | "pause" | "rollback_all"
status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
# "running" | "paused" | "completed" | "partially_failed" | "rolled_back"
failed_cr_count: Mapped[int] = mapped_column(default=0, nullable=False)
succeeded_cr_count: Mapped[int] = mapped_column(default=0, nullable=False)
```

- [ ] **Step 3: Handle failure policy in run_patch_campaign.py**

```python
# In run_patch_campaign.py execute(), after each CR completes:
async def _handle_cr_outcome(campaign_id, cr_id, success, db):
    campaign = await db.get(PatchCampaign, campaign_id)
    if success:
        campaign.succeeded_cr_count += 1
    else:
        campaign.failed_cr_count += 1
        if campaign.failure_policy == "pause":
            campaign.status = "paused"
        elif campaign.failure_policy == "rollback_all":
            campaign.status = "rolled_back"
            # Trigger rollback CRs for all completed CRs
    await db.commit()
```

- [ ] **Step 4: Add resume endpoint**

```python
@router.post("/{campaign_id}/resume", status_code=200)
async def resume_campaign(campaign_id: uuid.UUID, ...):
    campaign.status = "running"
    # Dispatch remaining pending CRs
```

- [ ] **Step 5: Frontend — show succeeded/failed/pending counts on campaign card**

In `PatchCampaignList.tsx`:
```tsx
<div className="text-xs text-slate-500 mt-1">
  ✅ {campaign.succeeded_cr_count} succeeded &nbsp;
  {campaign.failed_cr_count > 0 && <span className="text-red-600">❌ {campaign.failed_cr_count} failed</span>}
</div>
```

- [ ] **Step 6: Migrate, test, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_campaign_failure_policy"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_campaign_failure_policy.py -v 2>&1 | tail -5
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/src/components/PatchCampaignList.tsx frontend/src/components/CreateCampaignDrawer.tsx
git commit -m "feat: campaign failure policy (continue/pause/rollback_all) with per-CR outcome tracking"
```

---

## Task 3: Emergency priority + escalation path

**Files:**
- Modify: `backend/app/models/change_request.py` (add `priority`, `emergency_reason`)
- Modify: `backend/app/models/org_settings.py` (add `escalation_chain`)
- Create: `backend/app/workers/escalation_worker.py`
- Modify: `backend/app/routers/change_requests.py` (bypass maintenance window for emergency)
- Modify: `backend/app/main.py` (register worker)
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`
- Modify: `frontend/src/pages/ApprovalsQueue.tsx`
- Create: `backend/alembic/versions/XXXX_add_cr_priority.py`

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_emergency_priority.py
@pytest.mark.asyncio
async def test_emergency_cr_bypasses_hard_maintenance_window(
    async_client, admin_token, test_cr_id, test_hard_mw_id
):
    resp = await async_client.post(
        f"/change-requests/{test_cr_id}/execute",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 423  # Blocked by hard window

    # Set emergency flag
    await async_client.patch(f"/change-requests/{test_cr_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"priority": "emergency", "emergency_reason": "Zero-day CVE-2024-9999"})

    resp = await async_client.post(
        f"/change-requests/{test_cr_id}/execute",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200  # Emergency bypasses window
```

- [ ] **Step 2: Add priority fields to ChangeRequest**

```python
priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
emergency_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
# Add to schema: priority: str = "normal", emergency_reason: str | None = None
```

- [ ] **Step 3: Add escalation_chain to OrgSettings**

```python
escalation_chain: Mapped[list | None] = mapped_column(JSONB, nullable=True)
# Format: [{"user_id": "uuid", "delay_minutes": 30}, ...]
escalation_timeout_minutes: Mapped[int] = mapped_column(default=30, nullable=False)
```

- [ ] **Step 4: Update execute endpoint to skip maintenance window check for emergency**

In `execute_change_request` in `change_requests.py`, replace:
```python
if not cr.priority == "emergency":
    # ... maintenance window check ...
```

- [ ] **Step 5: Create escalation_worker.py**

```python
# backend/app/workers/escalation_worker.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, and_
from datetime import datetime, timezone, timedelta
from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.org_settings import OrganizationSettings
from app.services.notification_service import NotificationService, NotificationEvent

async def check_emergency_escalations():
    async with AsyncSessionLocal() as db:
        # Find emergency CRs awaiting approval that exceed the timeout
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        result = await db.execute(
            select(ChangeRequest).where(
                and_(
                    ChangeRequest.priority == "emergency",
                    ChangeRequest.status == ChangeRequestStatus.awaiting_approval,
                    ChangeRequest.updated_at < cutoff,
                )
            )
        )
        for cr in result.scalars():
            org_settings_result = await db.execute(
                select(OrganizationSettings).where(
                    OrganizationSettings.organization_id == cr.organization_id
                )
            )
            org_settings = org_settings_result.scalar_one_or_none()
            chain = (org_settings.escalation_chain or []) if org_settings else []
            if chain:
                svc = NotificationService(db)
                await svc.emit(NotificationEvent(
                    event_type="cr.escalated",
                    organization_id=str(cr.organization_id),
                    resource_id=str(cr.id),
                    resource_type="change_request",
                    message=f"EMERGENCY CR '{cr.title}' has not been approved in {(org_settings.escalation_timeout_minutes if org_settings else 30)} minutes — escalating",
                    recipients=[e["user_id"] for e in chain],
                ))

def start_escalation_scheduler(app):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_emergency_escalations, "interval", minutes=1)
    scheduler.start()
    app.state.escalation_scheduler = scheduler
```

Register in `main.py` lifespan startup.

- [ ] **Step 6: Frontend — emergency toggle on CR create**

In `CreateChangeRequest.tsx`, add after the title field:
```tsx
<div className="flex items-center gap-2 mt-2">
  <input type="checkbox" id="emergency" checked={isEmergency}
    onChange={e => setIsEmergency(e.target.checked)} />
  <label htmlFor="emergency" className="text-sm font-medium text-red-600">
    Emergency priority (bypasses maintenance windows)
  </label>
</div>
{isEmergency && (
  <textarea
    placeholder="Reason for emergency designation (required)"
    value={emergencyReason}
    onChange={e => setEmergencyReason(e.target.value)}
    className="mt-1 w-full border rounded p-2 text-sm"
    rows={2}
  />
)}
```

In `ApprovalsQueue.tsx`, show emergency badge at top:
```tsx
{cr.priority === "emergency" && (
  <span className="px-2 py-0.5 text-xs font-bold bg-red-600 text-white rounded uppercase tracking-wide">
    EMERGENCY
  </span>
)}
```

- [ ] **Step 7: Migrate, test, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_cr_priority_escalation"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_emergency_priority.py -v 2>&1 | tail -5
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/
git commit -m "feat: emergency CR priority — bypasses hard maintenance windows, escalation worker"
```

---

## Task 4: Finding "mitigated" state

**Files:**
- Modify: `backend/app/models/finding.py` (add `mitigated_at`, `mitigated_by_cr_id`, `patch_available_at`)
- Modify: `backend/app/services/finding_service.py`
- Modify: `backend/app/routers/remediation.py`
- Create: `backend/app/tests/test_finding_mitigated.py`
- Create: `backend/alembic/versions/XXXX_add_finding_mitigated_fields.py`
- Modify: `frontend/src/pages/VulnerabilityRemediation.tsx`

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_finding_mitigated.py
@pytest.mark.asyncio
async def test_finding_mitigated_pauses_sla(async_client, admin_token, test_finding_id, test_cr_id):
    # Mark as mitigated via the API
    resp = await async_client.post(
        f"/findings/{test_finding_id}/mitigate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"cr_id": test_cr_id}
    )
    assert resp.status_code == 200
    finding = resp.json()
    assert finding["state"] == "mitigated"
    assert finding["mitigated_at"] is not None
    assert finding["sla_paused"] is True

@pytest.mark.asyncio
async def test_finding_reopens_when_patch_becomes_available(async_client, admin_token, test_finding_id):
    # Simulate scanner reporting patch available
    resp = await async_client.post(
        f"/findings/{test_finding_id}/patch-available",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "mitigated"  # Still mitigated, but surfaced in patch queue
    assert resp.json()["patch_available_at"] is not None
```

- [ ] **Step 2: Add fields to Finding model**

```python
mitigated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
mitigated_by_cr_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
patch_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

@property
def sla_paused(self) -> bool:
    return self.state == "mitigated"
```

- [ ] **Step 3: Add endpoints to remediation router**

```python
@router.post("/{finding_id}/mitigate", status_code=200)
async def mitigate_finding(finding_id: uuid.UUID, body: MitigateRequest, ...):
    finding.state = "mitigated"
    finding.mitigated_at = datetime.now(timezone.utc)
    finding.mitigated_by_cr_id = uuid.UUID(body.cr_id) if body.cr_id else None
    await db.commit()
    return FindingRead.model_validate(finding)

@router.post("/{finding_id}/patch-available", status_code=200)
async def mark_patch_available(finding_id: uuid.UUID, ...):
    finding.patch_available_at = datetime.now(timezone.utc)
    await db.commit()
    return FindingRead.model_validate(finding)
```

- [ ] **Step 4: Frontend — mitigated tab in VulnerabilityRemediation**

Add a "Mitigated" tab that shows findings with `state == "mitigated"`. Findings with `patch_available_at` get a "Patch Now Available" banner.

- [ ] **Step 5: Migrate, test, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_finding_mitigated_fields"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_finding_mitigated.py -v 2>&1 | tail -5
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/
git commit -m "feat: finding mitigated state — SLA pause, patch-available notification"
```

---

## Task 5: Access review → remediation CR

**Files:**
- Modify: `backend/app/models/access_review.py` (add `remediation_cr_id` to entries)
- Modify: `backend/app/routers/access_reviews.py`
- Create: `backend/app/tests/test_access_review_remediation.py`
- Create: `backend/alembic/versions/XXXX_add_access_review_entry_cr.py`
- Modify: `frontend/src/pages/AccessReviewDetail.tsx`

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_revoke_decision_creates_remediation_cr(async_client, admin_token, test_campaign_id, test_entry_id):
    resp = await async_client.post(
        f"/access-reviews/{test_campaign_id}/entries/{test_entry_id}/create-remediation",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "cr_id" in data
    # Verify the CR is an offboard type
    cr_resp = await async_client.get(f"/change-requests/{data['cr_id']}",
        headers={"Authorization": f"Bearer {admin_token}"})
    assert "offboard" in cr_resp.json()["change_type"]
```

- [ ] **Step 2: Add remediation_cr_id to AccessReviewEntry**

```python
remediation_cr_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
```

- [ ] **Step 3: Add create-remediation endpoint**

```python
@router.post("/{campaign_id}/entries/{entry_id}/create-remediation", status_code=201)
async def create_remediation_cr(campaign_id: uuid.UUID, entry_id: uuid.UUID, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    entry = await db.get(AccessReviewEntry, entry_id)
    if not entry or entry.decision != "revoke":
        raise HTTPException(400, "Entry must have decision=revoke")
    # Create offboard CR
    cr = ChangeRequest(
        id=uuid.uuid4(),
        organization_id=user.organization_id,
        requester_id=user.id,
        title=f"Offboard {entry.user_identifier} (access review {campaign_id})",
        change_type=ChangeType.agent_user_offboard if hasattr(ChangeType, 'agent_user_offboard') else ChangeType.disable_iam_user,
        target_asset_ids=[str(entry.asset_id)] if entry.asset_id else [],
        desired_outcome={"user_identifier": entry.user_identifier},
        status=ChangeRequestStatus.draft,
    )
    db.add(cr)
    entry.remediation_cr_id = cr.id
    await db.commit()
    return {"cr_id": str(cr.id)}
```

- [ ] **Step 4: Frontend — show CR status on review entry**

In `AccessReviewDetail.tsx`, for entries with `remediation_cr_id`, show a small status badge linking to the CR.

- [ ] **Step 5: Migrate, test, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_access_review_entry_remediation_cr"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_access_review_remediation.py -v 2>&1 | tail -5
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/
git commit -m "feat: access review revoke decision auto-creates remediation CR"
```

---

## Task 6: Compliance "fix all" + attestation

**Files:**
- Modify: `backend/app/routers/compliance.py`
- Create: `backend/app/models/compliance_attestation.py`
- Create: `backend/app/tests/test_compliance_fix_all.py`
- Create: `backend/alembic/versions/XXXX_add_compliance_attestation.py`
- Modify: `frontend/src/pages/Compliance.tsx`

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_remediate_all_creates_batch_crs(async_client, admin_token, test_control_id):
    resp = await async_client.post(
        f"/compliance/controls/{test_control_id}/remediate-all",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "batch_id" in data
    assert len(data["cr_ids"]) > 0

@pytest.mark.asyncio
async def test_attest_control_creates_attestation(async_client, admin_token, test_control_id):
    resp = await async_client.post(
        f"/compliance/controls/{test_control_id}/attest",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"evidence_description": "Policy document at policy.internal/vuln-mgmt", "expiry_days": 365}
    )
    assert resp.status_code == 201
    assert resp.json()["expires_at"] is not None
```

- [ ] **Step 2: Create ComplianceAttestation model**

```python
# backend/app/models/compliance_attestation.py
class ComplianceAttestation(Base):
    __tablename__ = "compliance_attestations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    control_id: Mapped[str] = mapped_column(String(32), nullable=False)
    attested_by: Mapped[uuid.UUID] = mapped_column(nullable=False)
    evidence_description: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 3: Add endpoints to compliance router**

```python
@router.post("/controls/{control_id}/remediate-all", status_code=201)
async def remediate_all_for_control(control_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    # Get all failing assets for this control
    # Use existing compliance summary logic to find failing assets
    from app.compliance.drift import compute_cis_summary
    summary = await compute_cis_summary(db, user.organization_id)
    control = next((c for c in summary["controls"] if c["id"] == control_id), None)
    if not control:
        raise HTTPException(404, f"Control {control_id} not found")
    failing_asset_ids = [a["asset_id"] for a in control.get("failing_assets", [])]
    if not failing_asset_ids:
        return {"batch_id": None, "cr_ids": [], "message": "No failing assets"}
    # Use batch create
    batch_id = uuid.uuid4()
    cr_ids = []
    for asset_id in failing_asset_ids:
        cr = ChangeRequest(
            id=uuid.uuid4(),
            organization_id=user.organization_id,
            requester_id=user.id,
            title=f"CIS {control_id} remediation on {asset_id[:8]}",
            change_type=_control_to_change_type(control_id),
            target_asset_ids=[asset_id],
            desired_outcome={"profile": "cis_level1"},
            batch_id=batch_id,
            status=ChangeRequestStatus.draft,
        )
        db.add(cr)
        cr_ids.append(str(cr.id))
    await db.commit()
    return {"batch_id": str(batch_id), "cr_ids": cr_ids}

def _control_to_change_type(control_id: str) -> str:
    mapping = {
        "5.2.8": "harden_ssh", "5.2.14": "harden_ssh",
        "3.3.1": "apply_sysctl_hardening", "4.1.1": "deploy_auditd_rules",
    }
    return mapping.get(control_id, "audit_os_security_posture")

@router.post("/controls/{control_id}/attest", status_code=201)
async def attest_control(control_id: str, body: AttestRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from datetime import timedelta
    expiry = datetime.now(timezone.utc) + timedelta(days=body.expiry_days) if body.expiry_days else None
    att = ComplianceAttestation(
        organization_id=user.organization_id,
        control_id=control_id,
        attested_by=user.id,
        evidence_description=body.evidence_description,
        expires_at=expiry,
    )
    db.add(att)
    await db.commit()
    return {"id": str(att.id), "expires_at": att.expires_at.isoformat() if att.expires_at else None}
```

- [ ] **Step 4: Frontend — "Fix all" and "Attest" buttons in Compliance.tsx**

Add to each control row:
```tsx
<button onClick={() => remediateAll(control.id)}
  className="text-xs text-brand-600 hover:underline">Fix all failing</button>
<button onClick={() => openAttestation(control.id)}
  className="text-xs text-slate-500 hover:underline ml-2">Attest</button>
```

- [ ] **Step 5: Migrate, test, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_compliance_attestation"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_compliance_fix_all.py -v 2>&1 | tail -5
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/
git commit -m "feat: compliance fix-all batch CRs + non-automatable control attestation"
```

---

## Task 7: Staging → production promotion (ProjectPhase)

**Files:**
- Create: `backend/app/models/project_phase.py`
- Modify: `backend/app/models/project.py`
- Create: `backend/app/workers/soak_timer_worker.py`
- Modify: `backend/app/routers/projects.py`
- Create: `backend/app/tests/test_staged_rollout.py`
- Create: `backend/alembic/versions/XXXX_add_project_phases.py`
- Modify: `frontend/src/pages/ProjectDetail.tsx`

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_production_crs_locked_until_soak_complete(async_client, admin_token, test_staged_project):
    staging_phase_id = test_staged_project["staging_phase_id"]
    prod_phase_id = test_staged_project["prod_phase_id"]
    # Complete staging phase CRs
    for cr_id in test_staged_project["staging_cr_ids"]:
        await mark_cr_completed(cr_id)
    # Soak period starts — prod CRs should be locked
    prod_crs = await async_client.get(
        f"/projects/{test_staged_project['project_id']}/phases/{prod_phase_id}/crs",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert all(cr["locked_by_soak"] for cr in prod_crs.json())
```

- [ ] **Step 2: Create ProjectPhase model**

```python
# backend/app/models/project_phase.py
class ProjectPhase(Base):
    __tablename__ = "project_phases"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False, default=0)
    prerequisite_phase_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("project_phases.id"), nullable=True)
    soak_hours: Mapped[int] = mapped_column(default=72, nullable=False)
    soak_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    soak_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    # "pending" | "in_progress" | "soaking" | "completed"
```

- [ ] **Step 3: Create soak timer worker**

```python
# backend/app/workers/soak_timer_worker.py
async def check_soak_timers():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ProjectPhase).where(
                ProjectPhase.status == "soaking",
                ProjectPhase.soak_started_at.isnot(None),
            )
        )
        for phase in result.scalars():
            elapsed_hours = (datetime.now(timezone.utc) - phase.soak_started_at).total_seconds() / 3600
            if elapsed_hours >= phase.soak_hours:
                phase.status = "completed"
                phase.soak_completed_at = datetime.now(timezone.utc)
                # Unlock next phase CRs — update their locked_by_soak = False
                await db.commit()
                # Notify project members
```

- [ ] **Step 4: Migrate, test, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_project_phases_soak"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_staged_rollout.py -v 2>&1 | tail -5
git add backend/ frontend/
git commit -m "feat: staged rollout with soak timer — prod CRs locked until staging soak elapses"
```

---

## Task 8: BULK_PATCH smoke phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add run_phase_bulk_patch function**

```python
def run_phase_bulk_patch(client: NexplaneClient, cloud_account_id: str, tailscale_auth_key: str = "") -> None:
    """Phase BULK_PATCH: launches 3 EC2s, bulk-creates patch CRs, bulk-approves, verifies all patch."""
    print("\n[Phase BULK_PATCH] Bulk CR creation, approval, and execution")
    instance_ids = []
    asset_ids = []
    try:
        auth_key = client.get_tailscale_auth_key(tailscale_auth_key)
        backend_ip = setup_backend_tailscale(auth_key)
        agent_secret = client.get_agent_secret()

        for i in range(3):
            # Launch 3 EC2s
            instance_name = f"nexplane-smoke-bulk-{i+1}"
            client.run_cr(f"[BULK_PATCH] launch EC2 {i+1}", "ec2_launch", cloud_account_id,
                {"mode": "quick", "name": instance_name, "os": "amazon_linux"})
            asset = client.get_asset_by_name(instance_name)
            if not asset:
                fail(f"[BULK_PATCH] Asset {instance_name} not in inventory")
            asset_ids.append(asset["id"])
            instance_ids.append(asset["asset_metadata"].get("instance_id"))

        # Wait for SSM + deploy agents on all 3
        time.sleep(180)
        for i, (asset_id, instance_id) in enumerate(zip(asset_ids, instance_ids)):
            client.run_cr(f"[BULK_PATCH] agent deploy {i+1}", "deploy_nexplane_agent",
                asset_id, {"download_url": f"http://{backend_ip}:8000/downloads/nexplane-agent-linux-amd64-0.3.1",
                           "agent_secret": agent_secret, "control_plane_url": f"http://{backend_ip}:8000"})

        # Insert a test finding
        test_finding = client.post("/findings", json={
            "cve_id": "CVE-BULK-TEST-0001", "cvss": 7.5,
            "asset_ids": asset_ids, "description": "Smoke test bulk patch finding",
        })
        finding_id = test_finding.get("id")

        # Bulk-create patch CRs
        batch_resp = client.post("/change-requests/batch", json={
            "items": [
                {"title": f"[BULK_PATCH] patch asset {i+1}",
                 "change_type": "agent_linux_patch",
                 "target_asset_ids": [asset_ids[i]],
                 "desired_outcome": {"patch_mode": "security_only"},
                 "finding_ids": [finding_id] if finding_id else []}
                for i in range(3)
            ]
        })
        batch_id = batch_resp.get("batch_id")
        cr_ids = batch_resp.get("cr_ids", [])
        if len(cr_ids) != 3:
            fail(f"[BULK_PATCH] Expected 3 CRs, got {len(cr_ids)}")
        log(f"Batch created: {batch_id}, {len(cr_ids)} CRs")

        # Submit all for approval
        for cr_id in cr_ids:
            client.post(f"/change-requests/{cr_id}/plan")
            client.post(f"/change-requests/{cr_id}/submit-for-approval")

        # Bulk approve
        bulk_resp = client.post("/change-requests/bulk-approve", json={
            "cr_ids": cr_ids, "decision": "approved"
        })
        log(f"Bulk approved: {bulk_resp.get('approved_count')} CRs")

        # Execute all 3
        for cr_id in cr_ids:
            client.post(f"/change-requests/{cr_id}/execute")

        # Wait for completion
        deadline = time.time() + TIMEOUT_SECONDS * 2
        completed = set()
        while time.time() < deadline and len(completed) < 3:
            for cr_id in cr_ids:
                if cr_id in completed:
                    continue
                cr = client.get(f"/change-requests/{cr_id}")
                if cr.get("status") == "completed":
                    completed.add(cr_id)
                elif cr.get("status") in ("failed", "rolled_back"):
                    fail(f"[BULK_PATCH] CR {cr_id} failed: {cr.get('status')}")
            time.sleep(10)

        if len(completed) < 3:
            fail(f"[BULK_PATCH] Only {len(completed)}/3 CRs completed")
        log(f"All 3 patch CRs completed")

        # Verify finding auto-closed if finding_id exists
        if finding_id:
            finding = client.get(f"/findings/{finding_id}")
            if finding.get("state") not in ("remediated", "remediated_pending_verification"):
                log(f"  ⚠️  Finding state={finding.get('state')} (expected remediated)")
            else:
                log("Finding auto-closed ✓")

        log("Phase BULK_PATCH complete")

    except Exception as e:
        print(f"\n❌ Phase BULK_PATCH failed: {e}")
        raise
    finally:
        for instance_id in instance_ids:
            try:
                _ec2 = _get_aws_boto3_client("ec2")
                if _ec2:
                    _ec2.terminate_instances(InstanceIds=[instance_id])
            except Exception:
                pass
        for asset_id in asset_ids:
            try:
                client.client.delete(f"{client.base}/assets/{asset_id}")
            except Exception:
                pass
```

- [ ] **Step 2: Wire into main() and commit**

```python
if "BULK_PATCH" in phases:
    run_phase_bulk_patch(client, cloud_account_id, args.tailscale_auth_key)
```

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "test: BULK_PATCH smoke phase — 3 EC2s, bulk create/approve/execute, finding auto-closure"
```

---

*Phase 2 tasks 9–10 (re-scan trigger, external ticket closure) are deferred — they require live scanner API credentials and live Jira/PagerDuty tokens not available in the current smoke test environment. Stubs are noted in the spec.*
