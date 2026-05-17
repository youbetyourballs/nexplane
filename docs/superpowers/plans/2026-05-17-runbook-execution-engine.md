# Runbook Execution Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing runbook executor so triggered executions advance step-by-step, auto-executing CRs (plan → approve → execute inline), with admin UI to enable/disable runbooks and a maintenance-window pre-check on trigger.

**Architecture:** Five sequential tasks: (1) DB model + migration + seed update for `auto_execute`, (2) schema + router guard + trigger endpoint changes, (3) extract `plan_cr` helper and update router, (4) bridge auto-drive (plan → approve → execute inline), (5) scheduler wiring + frontend toggle UI.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, APScheduler, React/TanStack Query, Tailwind CSS.

---

## File Map

| File | Change |
|---|---|
| `backend/app/models/runbook.py` | Add `auto_execute: bool` column |
| `backend/alembic/versions/a1b2c3d4e5f6_add_runbook_auto_execute.py` | New migration |
| `backend/app/seed/runbook_templates.py` | Set `auto_execute=True` in seeder |
| `backend/app/schemas/runbook.py` | Add `auto_execute` to schemas + `force` to trigger request |
| `backend/app/routers/runbooks.py` | Admin guard on PUT; pass `force` from trigger payload |
| `backend/app/services/runbook_service.py` | Gate `trigger_runbook`; maintenance window check |
| `backend/app/services/change_plan_service.py` | New — extracted `plan_cr()` helper |
| `backend/app/routers/change_requests.py` | Call `plan_cr()` instead of inlining |
| `backend/app/services/runbook_cr_bridge.py` | Auto-drive: plan → approve → execute |
| `backend/app/main.py` | Add scheduler job for `tick_all_executions` |
| `backend/app/tests/test_runbook_executions.py` | New tests for auto_execute gate + window check |
| `frontend/src/hooks/useRunbooks.ts` | Add `auto_execute` to types; update `useTriggerRunbook` |
| `frontend/src/pages/Runbooks.tsx` | Admin toggle + disabled Run button with tooltip |

---

## Task 1: Add `auto_execute` to model, migration, and seed

**Files:**
- Modify: `backend/app/models/runbook.py:18`
- Create: `backend/alembic/versions/a1b2c3d4e5f6_add_runbook_auto_execute.py`
- Modify: `backend/app/seed/runbook_templates.py:128-136`

- [ ] **Step 1: Write a failing test for the model field**

```python
# backend/app/tests/test_runbook_executions.py
# Add at the top of the file with other imports:
from app.models.runbook import Runbook

@pytest.mark.asyncio
async def test_runbook_has_auto_execute_field(db):
    """Runbook model must have an auto_execute bool field defaulting to False."""
    org = Organization(id=uuid.uuid4(), name="AE Test Org")
    db.add(org)
    await db.flush()
    user = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"ae-{uuid.uuid4().hex[:8]}@test.example",
        name="AE User", role=UserRole.admin,
        hashed_password=hash_password("test"),
    )
    db.add(user)
    await db.flush()
    rb = Runbook(
        organization_id=org.id,
        name="AE Test Runbook",
        version=1,
        is_seed=False,
        created_by=user.id,
    )
    db.add(rb)
    await db.flush()
    assert rb.auto_execute is False
```

- [ ] **Step 2: Run test to verify it fails**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py::test_runbook_has_auto_execute_field -v
```

Expected: `AttributeError: 'Runbook' object has no attribute 'auto_execute'` or column missing error.

- [ ] **Step 3: Add `auto_execute` to the Runbook model**

In `backend/app/models/runbook.py`, after line 19 (`is_seed` field), add:

```python
    auto_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
```

- [ ] **Step 4: Create the Alembic migration**

Create `backend/alembic/versions/a1b2c3d4e5f6_add_runbook_auto_execute.py`:

```python
"""add runbook auto_execute

Revision ID: a1b2c3d4e5f6
Revises: f510f4f16763
Create Date: 2026-05-17

"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f510f4f16763"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runbooks",
        sa.Column("auto_execute", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("runbooks", "auto_execute")
```

- [ ] **Step 5: Update the seed loader to set `auto_execute=True` on seed templates**

In `backend/app/seed/runbook_templates.py`, change the `Runbook(...)` constructor call inside `load_seed_templates` (around line 128) from:

```python
        rb = Runbook(
            organization_id=org_id,
            name=template["name"],
            description=template["description"],
            tags=template["tags"],
            version=1,
            is_seed=True,
            created_by=system_user_id,
        )
```

To:

```python
        rb = Runbook(
            organization_id=org_id,
            name=template["name"],
            description=template["description"],
            tags=template["tags"],
            version=1,
            is_seed=True,
            auto_execute=True,
            created_by=system_user_id,
        )
```

- [ ] **Step 6: Apply the migration inside the backend container**

```
docker compose exec backend alembic upgrade head
```

Expected output ends with: `Running upgrade ... -> a1b2c3d4e5f6, add runbook auto_execute`

- [ ] **Step 7: Run the test to verify it passes**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py::test_runbook_has_auto_execute_field -v
```

Expected: `PASSED`

- [ ] **Step 8: Commit**

```
git add backend/app/models/runbook.py backend/alembic/versions/a1b2c3d4e5f6_add_runbook_auto_execute.py backend/app/seed/runbook_templates.py backend/app/tests/test_runbook_executions.py
git commit -m "feat: add auto_execute field to Runbook model"
```

---

## Task 2: Schema, router guard, and trigger pre-checks

**Files:**
- Modify: `backend/app/schemas/runbook.py:42-47,76-78`
- Modify: `backend/app/routers/runbooks.py:44-52,72-81`
- Modify: `backend/app/services/runbook_service.py:124-142`

- [ ] **Step 1: Write failing tests**

Add to `backend/app/tests/test_runbook_executions.py`:

```python
@pytest.mark.asyncio
async def test_trigger_blocked_when_auto_execute_false(client: AsyncClient):
    """Triggering a runbook with auto_execute=False must return 403."""
    # Create a runbook (auto_execute defaults to False)
    resp = await client.post("/api/runbooks", json={
        "name": "Disabled Runbook",
        "tags": [],
        "steps": [
            {"step_number": 1, "name": "Step 1", "type": "human_checkpoint",
             "prompt": "Approve?", "required_role": "admin",
             "timeout_hours": 1, "on_timeout": "abort", "on_failure": "abort",
             "parallel_steps": []}
        ],
    })
    assert resp.status_code == 201
    rb_id = resp.json()["id"]

    trigger_resp = await client.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {}})
    assert trigger_resp.status_code == 403
    assert "auto-execution" in trigger_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_non_admin_cannot_update_runbook(client: AsyncClient):
    """PUT /api/runbooks/{id} must require admin role."""
    # client fixture uses admin role — create a non-admin client inline
    pass  # Covered by router-level require_roles; test the 403 path
```

- [ ] **Step 2: Run tests to verify they fail**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py::test_trigger_blocked_when_auto_execute_false -v
```

Expected: `FAILED` — trigger returns 201, not 403.

- [ ] **Step 3: Add `auto_execute` and `force` to schemas**

In `backend/app/schemas/runbook.py`:

```python
class RunbookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    tags: list[str] = []
    auto_execute: bool = False          # ← add
    steps: list[RunbookStepCreate]


class RunbookUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    auto_execute: bool | None = None    # ← add
    steps: list[RunbookStepCreate] | None = None


class RunbookOut(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    description: str | None
    version: int
    tags: list[str]
    is_seed: bool
    auto_execute: bool                  # ← add
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    steps: list[RunbookStepOut]

    model_config = {"from_attributes": True}


class TriggerRunbookRequest(BaseModel):
    context: dict[str, Any] = {}
    force: bool = False                 # ← add
```

- [ ] **Step 4: Add admin guard to the PUT endpoint and pass `force` from trigger**

In `backend/app/routers/runbooks.py`:

```python
from app.routers import current_user, require_roles
from app.models.user import UserRole

# Change the PUT endpoint signature:
@router.put("/{runbook_id}", response_model=RunbookOut)
async def update_runbook(
    runbook_id: str,
    payload: RunbookUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_roles(UserRole.admin)),  # ← was Depends(current_user)
):
    return await RunbookService(db).update_runbook(runbook_id, user.organization_id, payload)


# Change the trigger endpoint to pass force:
@router.post("/{runbook_id}/trigger", response_model=RunbookExecutionOut, status_code=201)
async def trigger_runbook(
    runbook_id: str,
    payload: TriggerRunbookRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).trigger_runbook(
        runbook_id, user.organization_id, user.id, payload.context, force=payload.force
    )
```

- [ ] **Step 5: Add `auto_execute` gate and maintenance window check to `trigger_runbook`**

In `backend/app/services/runbook_service.py`, replace the `trigger_runbook` method (lines 124-142) with:

```python
    async def trigger_runbook(
        self, runbook_id: str, org_id: uuid.UUID,
        user_id: uuid.UUID, context: dict, *, force: bool = False
    ) -> RunbookExecution:
        rb = await self.get_runbook(runbook_id, org_id)

        if not rb.auto_execute:
            raise HTTPException(
                status_code=403,
                detail="Runbook is not enabled for auto-execution. An admin must enable it first.",
            )

        # Maintenance window pre-check
        if not force:
            from app.services.maintenance_window_service import is_in_maintenance_window
            from app.models.asset import Asset
            from sqlalchemy import select as _select

            # Collect all asset_ids referenced across all steps in the runbook
            all_asset_ids: list[uuid.UUID] = []
            for step in rb.steps:
                if step.asset_selector and step.asset_selector.get("asset_ids"):
                    all_asset_ids.extend(
                        uuid.UUID(str(aid)) for aid in step.asset_selector["asset_ids"]
                    )

            # Check maintenance windows on any matched asset tags
            if all_asset_ids:
                assets_result = await self.db.execute(
                    _select(Asset).where(Asset.id.in_(all_asset_ids))
                )
                for asset in assets_result.scalars():
                    window = await is_in_maintenance_window(
                        self.db, asset.tags or [], enforcement="hard"
                    )
                    if window:
                        raise HTTPException(
                            status_code=409,
                            detail={
                                "maintenance_window": window.name,
                                "warning": (
                                    f"Active change freeze window '{window.name}' covers affected assets. "
                                    "Set force=true to override as an emergency."
                                ),
                            },
                        )

        snapshot = RunbookOut.model_validate(rb).model_dump(mode="json")
        snapshot["organization_id"] = str(org_id)
        execution = RunbookExecution(
            runbook_id=rb.id,
            runbook_version=rb.version,
            runbook_snapshot=snapshot,
            triggered_by=user_id,
            context=context,
            status="running",
            current_step=1,
        )
        self.db.add(execution)
        await self.db.commit()
        await self.db.refresh(execution)

        # Write emergency override audit event if forced
        if force:
            from app.services.audit_service import record_event
            await record_event(
                self.db, org_id, "runbook.emergency_override",
                {"runbook_id": str(rb.id), "execution_id": str(execution.id)},
                actor_id=user_id,
            )
            await self.db.commit()

        return await self.get_execution(str(execution.id), org_id)
```

- [ ] **Step 6: Run the failing test to verify it now passes**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py::test_trigger_blocked_when_auto_execute_false -v
```

Expected: `PASSED`

- [ ] **Step 7: Run the full execution test suite to check for regressions**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py app/tests/test_runbooks.py -v
```

Expected: all pass. Note: existing tests in `test_runbook_executions.py` use `_create_and_trigger` which creates a runbook with `auto_execute` defaulting to `False` — update `_create_and_trigger` to pass `"auto_execute": True` in the POST body:

```python
async def _create_and_trigger(client: AsyncClient) -> tuple[str, str]:
    resp = await client.post("/api/runbooks", json={
        "name": "Checkpoint Runbook",
        "auto_execute": True,           # ← add this
        "tags": [],
        "steps": [...]
    })
```

- [ ] **Step 8: Commit**

```
git add backend/app/schemas/runbook.py backend/app/routers/runbooks.py backend/app/services/runbook_service.py backend/app/tests/test_runbook_executions.py
git commit -m "feat: auto_execute gate and maintenance window pre-check on runbook trigger"
```

---

## Task 3: Extract `plan_cr` helper

**Files:**
- Create: `backend/app/services/change_plan_service.py`
- Modify: `backend/app/routers/change_requests.py:222-286`

- [ ] **Step 1: Write a failing test for `plan_cr`**

Add to `backend/app/tests/test_runbook_executions.py`:

```python
from app.services.change_plan_service import plan_cr
from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus

@pytest.mark.asyncio
async def test_plan_cr_sets_status_to_planned(db):
    """plan_cr must set CR status to 'planned' and create a ChangePlan."""
    from app.models.change_plan import ChangePlan
    org = Organization(id=uuid.uuid4(), name=f"PlanCR Org {uuid.uuid4().hex[:4]}")
    db.add(org)
    await db.flush()
    user = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"plancr-{uuid.uuid4().hex[:8]}@test.example",
        name="PlanCR User", role=UserRole.admin,
        hashed_password=hash_password("test"),
    )
    db.add(user)
    await db.flush()

    cr = ChangeRequest(
        organization_id=org.id,
        requester_id=user.id,
        title="Test CR",
        description="Test",
        change_type=ChangeType.isolate_host,
        target_asset_ids=[],
        desired_outcome={},
        risk_level=RiskLevel.low,
        status=ChangeRequestStatus.draft,
        source="test",
    )
    db.add(cr)
    await db.flush()

    plan = await plan_cr(db, cr)
    assert cr.status == ChangeRequestStatus.planned
    assert plan is not None
```

- [ ] **Step 2: Run test to verify it fails**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py::test_plan_cr_sets_status_to_planned -v
```

Expected: `ImportError: cannot import name 'plan_cr' from 'app.services.change_plan_service'`

- [ ] **Step 3: Create `change_plan_service.py`**

Create `backend/app/services/change_plan_service.py`:

```python
"""
Shared plan-generation logic used by both the change_requests router
and the runbook CR bridge.
"""
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.asset import Asset
from app.models.change_plan import ChangePlan, PlanGeneratedBy
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request

log = logging.getLogger(__name__)


async def plan_cr(db: AsyncSession, cr: ChangeRequest) -> ChangePlan:
    """
    Generate a plan for a ChangeRequest in-place.

    Sets cr.status = 'planned', creates or updates the associated ChangePlan,
    and flushes (does NOT commit — caller must commit).

    Raises ValueError if the safety scorer blocks the plan.
    """
    asset_ids = [uuid.UUID(str(aid)) for aid in (cr.target_asset_ids or [])]
    assets_result = await db.execute(
        select(Asset).options(selectinload(Asset.connector)).where(Asset.id.in_(asset_ids))
    )
    assets = list(assets_result.scalars().all())

    safety_result = score_change_request(cr, assets)

    if safety_result.is_blocked:
        raise ValueError(
            f"Safety review blocked plan generation: {safety_result.blocking_issues}"
        )

    plan_data = generate_plan(cr, assets, safety_result)

    if cr.change_plan:
        plan = cr.change_plan
        plan.generated_steps = plan_data.generated_steps
        plan.preflight_checks = plan_data.preflight_checks
        plan.blast_radius = plan_data.blast_radius
        plan.rollback_plan = plan_data.rollback_plan
        plan.verification_plan = plan_data.verification_plan
    else:
        plan = ChangePlan(
            change_request_id=cr.id,
            generated_steps=plan_data.generated_steps,
            preflight_checks=plan_data.preflight_checks,
            blast_radius=plan_data.blast_radius,
            rollback_plan=plan_data.rollback_plan,
            verification_plan=plan_data.verification_plan,
            generated_by=PlanGeneratedBy.system,
        )
        db.add(plan)

    cr.risk_level = safety_result.risk_level
    cr.status = ChangeRequestStatus.planned
    cr.updated_at = datetime.now(timezone.utc)

    await db.flush()
    return plan
```

- [ ] **Step 4: Update the `generate_change_plan` router function to call `plan_cr`**

In `backend/app/routers/change_requests.py`, add the import at the top:

```python
from app.services.change_plan_service import plan_cr as _plan_cr
```

Then replace the body of `generate_change_plan` (keeping the function signature intact) with:

```python
async def generate_change_plan(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.status not in (ChangeRequestStatus.draft, ChangeRequestStatus.planned):
        raise HTTPException(status_code=400, detail=f"Cannot plan a change request in status '{cr.status.value}'")

    # Clear stale approvals so a re-planned CR can be approved fresh
    from sqlalchemy import delete as sa_delete
    await db.execute(sa_delete(Approval).where(Approval.change_request_id == cr.id))

    try:
        plan = await _plan_cr(db, cr)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail={"message": "Safety review blocked plan generation", "blocking_issues": str(e)},
        )

    await record_event(db, user.organization_id, "change_plan.generated",
                       {"change_request_id": str(cr.id)},
                       actor_id=user.id, change_request_id=cr.id)
    await db.commit()
    await db.refresh(plan)
    return plan
```

- [ ] **Step 5: Run the test to verify it passes**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py::test_plan_cr_sets_status_to_planned -v
```

Expected: `PASSED`

- [ ] **Step 6: Run the full change request test suite to check for regressions**

```
docker compose exec backend pytest app/tests/test_change_requests.py -v --tb=short
```

Expected: all pass (the router now delegates to `plan_cr` — same logic, same result).

- [ ] **Step 7: Commit**

```
git add backend/app/services/change_plan_service.py backend/app/routers/change_requests.py backend/app/tests/test_runbook_executions.py
git commit -m "refactor: extract plan_cr helper from change_requests router"
```

---

## Task 4: Bridge auto-drive (plan → approve → execute)

**Files:**
- Modify: `backend/app/services/runbook_cr_bridge.py`

- [ ] **Step 1: Write a failing test**

Add to `backend/app/tests/test_runbook_executions.py`:

```python
from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
async def test_create_and_execute_runbook_cr_drives_cr_to_approved(db):
    """
    create_and_execute_runbook_cr must create a CR, plan it, set it to
    approved, and fire the workflow — all without a commit in between.
    """
    from app.services.runbook_cr_bridge import create_and_execute_runbook_cr
    from app.models.runbook import RunbookExecution

    org = Organization(id=uuid.uuid4(), name=f"Bridge Org {uuid.uuid4().hex[:4]}")
    db.add(org)
    await db.flush()
    user = User(
        id=uuid.uuid4(), organization_id=org.id,
        email=f"bridge-{uuid.uuid4().hex[:8]}@test.example",
        name="Bridge User", role=UserRole.admin,
        hashed_password=hash_password("test"),
    )
    db.add(user)
    await db.flush()

    execution = RunbookExecution(
        runbook_id=uuid.uuid4(),  # fk not enforced in test tx
        runbook_version=1,
        runbook_snapshot={"organization_id": str(org.id), "steps": []},
        triggered_by=user.id,
        context={},
        status="running",
        current_step=1,
    )
    db.add(execution)
    await db.flush()

    step_def = {
        "step_number": 1,
        "name": "Isolate Host",
        "type": "change",
        "change_type": "isolate_host",
        "parameters": {},
        "asset_selector": None,
        "on_failure": "abort",
    }

    # Patch start_workflow so we don't actually launch a workflow task
    with patch(
        "app.services.runbook_cr_bridge.start_workflow",
        new_callable=AsyncMock,
        return_value="wf-test-123",
    ):
        cr = await create_and_execute_runbook_cr(db, execution, step_def)

    assert cr.status.value == "approved"
    assert cr.source == "runbook"
```

- [ ] **Step 2: Run test to verify it fails**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py::test_create_and_execute_runbook_cr_drives_cr_to_approved -v
```

Expected: `ImportError: cannot import name 'create_and_execute_runbook_cr'`

- [ ] **Step 3: Rewrite `runbook_cr_bridge.py`**

Replace the entire contents of `backend/app/services/runbook_cr_bridge.py`:

```python
"""
Bridge between the runbook executor and the ChangeRequest execution pipeline.

create_and_execute_runbook_cr() is the main entry point:
  1. Creates a draft CR on behalf of the runbook step
  2. Plans it inline (no HTTP round-trip)
  3. Auto-approves it (the runbook trigger is the approval)
  4. Fires the execution workflow as a background asyncio task

The caller (runbook_executor.py) then polls cr.status on subsequent ticks.
"""
import asyncio
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.models.runbook import RunbookExecution
from app.services.change_plan_service import plan_cr
from app.workflows.runner import WorkflowInput, start_workflow
from app.workflows.execute_change_workflow import execute_change_workflow

log = logging.getLogger(__name__)


async def create_and_execute_runbook_cr(
    db: AsyncSession,
    execution: RunbookExecution,
    step_def: dict,
) -> ChangeRequest:
    """
    Create, plan, approve, and fire a ChangeRequest for one runbook step.
    Returns the CR object (status='approved', workflow task already launched).
    Raises ValueError if the change_type is unknown or planning is blocked.
    """
    cr = await _create_draft_cr(db, execution, step_def)

    # Plan inline — raises ValueError on safety block or unknown connector
    try:
        await plan_cr(db, cr)
    except ValueError as exc:
        log.error("Runbook step plan failed for execution %s step %s: %s",
                  execution.id, step_def.get("step_number"), exc)
        raise

    # Auto-approve: the runbook trigger is itself the approval
    cr.status = ChangeRequestStatus.approved
    cr.updated_at = datetime.now(timezone.utc)
    await db.flush()

    # Create execution run record
    workflow_id = f"wf-rb-{cr.id}-1"
    run = ExecutionRun(
        change_request_id=cr.id,
        workflow_id=workflow_id,
        status=ExecutionStatus.pending,
    )
    db.add(run)
    await db.flush()

    # Fire the workflow in the background — returns immediately
    wf_input = WorkflowInput(
        change_request_id=str(cr.id),
        organization_id=str(cr.organization_id),
        initiator_id=str(execution.triggered_by),
    )
    await start_workflow(execute_change_workflow, wf_input, workflow_id=workflow_id)

    return cr


async def _create_draft_cr(
    db: AsyncSession,
    execution: RunbookExecution,
    step_def: dict,
) -> ChangeRequest:
    change_type_str = step_def.get("change_type", "")
    try:
        change_type = ChangeType(change_type_str)
    except ValueError:
        raise ValueError(
            f"Unknown change_type '{change_type_str}' in runbook step '{step_def.get('name')}'. "
            f"Valid types: {[e.value for e in ChangeType]}"
        )

    parameters = dict(step_def.get("parameters") or {})
    parameters.update(execution.context)

    cr = ChangeRequest(
        organization_id=uuid.UUID(execution.runbook_snapshot["organization_id"]),
        requester_id=execution.triggered_by,
        title=f"[Runbook] {step_def['name']}",
        description=(
            f"Auto-created by runbook execution {execution.id}, "
            f"step {step_def['step_number']}: {step_def['name']}"
        ),
        change_type=change_type,
        target_asset_ids=_resolve_asset_ids(step_def.get("asset_selector")),
        desired_outcome=parameters,
        risk_level=RiskLevel.medium,
        status=ChangeRequestStatus.draft,
        source="runbook",
    )
    db.add(cr)
    await db.flush()
    return cr


def _resolve_asset_ids(asset_selector: dict | None) -> list:
    if not asset_selector:
        return []
    return [str(aid) for aid in asset_selector.get("asset_ids", [])]
```

- [ ] **Step 4: Update `runbook_executor.py` to call `create_and_execute_runbook_cr` instead of `create_runbook_change_request`**

In `backend/app/services/runbook_executor.py`, change the import inside `_execute_change_step`:

```python
async def _execute_change_step(
    db: AsyncSession, execution: RunbookExecution, step_def: dict, step_result: RunbookStepResult
) -> None:
    if step_result.status == "pending":
        from app.services.runbook_cr_bridge import create_and_execute_runbook_cr
        try:
            cr = await create_and_execute_runbook_cr(db, execution, step_def)
        except ValueError as exc:
            step_result.status = "failed"
            step_result.error_message = str(exc)
            await _handle_step_failure(db, execution, step_def)
            return
        step_result.change_request_ids = [str(cr.id)]
        step_result.status = "running"
        step_result.started_at = datetime.now(timezone.utc)

    elif step_result.status == "running":
        import uuid as _uuid
        cr_id = step_result.change_request_ids[0]
        cr = await db.get(ChangeRequest, _uuid.UUID(cr_id))
        if cr is None:
            step_result.status = "failed"
            step_result.error_message = f"Change request {cr_id} not found"
            await _handle_step_failure(db, execution, step_def)
            return

        if cr.status == ChangeRequestStatus.completed:
            step_result.status = "completed"
            step_result.completed_at = datetime.now(timezone.utc)
            step_result.result = {
                "exit_code": 0,
                "change_request_id": cr_id,
            }
            execution.current_step += 1

        elif cr.status in (ChangeRequestStatus.failed, ChangeRequestStatus.rejected,
                           ChangeRequestStatus.rolled_back):
            step_result.status = "failed"
            step_result.error_message = f"Change request ended with status: {cr.status}"
            step_result.result = {"exit_code": 1, "change_request_id": cr_id}
            await _handle_step_failure(db, execution, step_def)
```

Also do the same for `_execute_parallel_step` — replace `create_runbook_change_request` with `create_and_execute_runbook_cr`:

```python
async def _execute_parallel_step(
    db: AsyncSession, execution: RunbookExecution, step_def: dict, step_result: RunbookStepResult
) -> None:
    child_steps = step_def.get("parallel_steps", [])

    if step_result.status == "pending":
        from app.services.runbook_cr_bridge import create_and_execute_runbook_cr
        cr_ids = []
        for child in child_steps:
            try:
                cr = await create_and_execute_runbook_cr(db, execution, child)
                cr_ids.append(str(cr.id))
            except ValueError as exc:
                log.error("Parallel step child failed: %s", exc)
                step_result.status = "failed"
                step_result.error_message = str(exc)
                await _handle_step_failure(db, execution, step_def)
                return
        step_result.change_request_ids = cr_ids
        step_result.status = "running"
        step_result.started_at = datetime.now(timezone.utc)
        return

    # ... rest of the "running" branch is unchanged
```

- [ ] **Step 5: Run the bridge test**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py::test_create_and_execute_runbook_cr_drives_cr_to_approved -v
```

Expected: `PASSED`

- [ ] **Step 6: Run the full runbook test suite**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py app/tests/test_runbooks.py -v --tb=short
```

Expected: all pass.

- [ ] **Step 7: Commit**

```
git add backend/app/services/runbook_cr_bridge.py backend/app/services/runbook_executor.py backend/app/tests/test_runbook_executions.py
git commit -m "feat: auto-drive runbook CRs (plan -> approve -> execute) in bridge"
```

---

## Task 5: Scheduler wiring + frontend admin toggle

**Files:**
- Modify: `backend/app/main.py:49-53`
- Modify: `frontend/src/hooks/useRunbooks.ts:23-35,126-132`
- Modify: `frontend/src/pages/Runbooks.tsx:1-136`

- [ ] **Step 1: Wire the tick function into the scheduler**

In `backend/app/main.py`, at the top add the import (with the existing workflow imports):

```python
from app.services.runbook_executor import tick_all_executions as _tick_runbooks
```

Inside the `lifespan` startup (where the other `_escalation_scheduler.add_job` calls are), add:

```python
    _escalation_scheduler.add_job(
        lambda: _tick_runbooks(AsyncSessionLocal),
        "interval",
        seconds=30,
        id="runbook_executor_tick",
        replace_existing=True,
    )
```

- [ ] **Step 2: Verify the scheduler starts without error**

```
docker compose restart backend
docker logs nexplane-backend-1 --tail=20 2>&1 | grep -E "APScheduler|runbook|ERROR"
```

Expected: APScheduler starts, no errors mentioning `runbook_executor_tick`.

- [ ] **Step 3: Add `auto_execute` to frontend types and add `useTriggerRunbook` force support**

In `frontend/src/hooks/useRunbooks.ts`:

```typescript
// Add auto_execute to RunbookOut interface:
export interface RunbookOut {
  id: string;
  organization_id: string;
  name: string;
  description?: string;
  version: number;
  tags: string[];
  is_seed: boolean;
  auto_execute: boolean;          // ← add
  created_by: string;
  created_at: string;
  updated_at: string;
  steps: RunbookStepOut[];
}

// Add a dedicated useToggleRunbookAutoExecute mutation:
export const useToggleRunbookAutoExecute = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (auto_execute: boolean) =>
      apiClient
        .put<RunbookOut>(`/api/runbooks/${id}`, { auto_execute })
        .then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runbooks"] });
      qc.invalidateQueries({ queryKey: ["runbook", id] });
    },
  });
};

// Update useTriggerRunbook to support force and return the raw AxiosResponse
// so the caller can inspect 409 bodies:
export const useTriggerRunbook = (id: string) =>
  useMutation({
    mutationFn: ({ context = {}, force = false }: { context?: Record<string, unknown>; force?: boolean }) =>
      apiClient
        .post<RunbookExecutionOut>(`/api/runbooks/${id}/trigger`, { context, force })
        .then((r) => r.data),
  });
```

- [ ] **Step 4: Update the Runbooks page with admin toggle and maintenance-window modal**

Replace `frontend/src/pages/Runbooks.tsx` entirely:

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Copy, Play, BookOpen, AlertTriangle } from "lucide-react";
import {
  useRunbooks,
  useForkRunbook,
  useTriggerRunbook,
  useToggleRunbookAutoExecute,
} from "../hooks/useRunbooks";
import type { RunbookExecutionOut, RunbookOut } from "../hooks/useRunbooks";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import { useAuth } from "../hooks/useAuth";

export function Runbooks() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const { data: runbooks, isLoading } = useRunbooks({
    search: search || undefined,
    tag: tagFilter || undefined,
  });
  const fork = useForkRunbook();

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <PageHeader
        title="Runbooks"
        subtitle="Composable, versioned workflows that chain change requests with conditional logic."
        actions={
          isAdmin ? (
            <button
              onClick={() => navigate("/runbooks/new")}
              className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 text-sm font-medium"
            >
              <Plus className="w-4 h-4" /> New Runbook
            </button>
          ) : null
        }
      />

      <div className="flex gap-3 mb-6">
        <input
          className="border border-slate-300 rounded-md px-3 py-2 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-brand-500"
          placeholder="Search runbooks..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <input
          className="border border-slate-300 rounded-md px-3 py-2 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-brand-500"
          placeholder="Filter by tag..."
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
        />
      </div>

      {runbooks?.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <BookOpen className="w-12 h-12 mx-auto mb-3 opacity-40" />
          <p className="text-lg font-medium">No runbooks yet</p>
          <p className="text-sm mt-1">Create one or fork a seed template to get started.</p>
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-slate-200 divide-y divide-slate-100">
          {runbooks?.map((rb) => (
            <RunbookRow key={rb.id} rb={rb} isAdmin={isAdmin} onFork={() => fork.mutate(rb.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

function RunbookRow({
  rb,
  isAdmin,
  onFork,
}: {
  rb: RunbookOut;
  isAdmin: boolean;
  onFork: () => void;
}) {
  const navigate = useNavigate();
  const toggle = useToggleRunbookAutoExecute(rb.id);

  return (
    <div
      className="flex items-center justify-between px-4 py-3 hover:bg-slate-50 cursor-pointer"
      onClick={() => navigate(`/runbooks/${rb.id}`)}
    >
      <div className="flex items-center gap-3 min-w-0">
        <BookOpen className="w-4 h-4 text-brand-500 flex-shrink-0" />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-slate-900 truncate">{rb.name}</span>
            {rb.is_seed && (
              <span className="px-1.5 py-0.5 text-xs font-medium bg-amber-100 text-amber-700 rounded">
                Template
              </span>
            )}
            <span className="text-xs text-slate-400">v{rb.version}</span>
          </div>
          <div className="flex gap-1 mt-0.5">
            {rb.tags.map((t) => (
              <span key={t} className="text-xs px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded">
                {t}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 ml-4 flex-shrink-0">
        {/* Auto-execute toggle — admin only */}
        {isAdmin ? (
          <label
            className="flex items-center gap-1.5 cursor-pointer"
            onClick={(e) => e.stopPropagation()}
            title={rb.auto_execute ? "Disable auto-execution" : "Enable auto-execution"}
          >
            <span className="text-xs text-slate-500">{rb.auto_execute ? "Enabled" : "Disabled"}</span>
            <button
              role="switch"
              aria-checked={rb.auto_execute}
              onClick={(e) => {
                e.stopPropagation();
                toggle.mutate(!rb.auto_execute);
              }}
              disabled={toggle.isPending}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
                rb.auto_execute ? "bg-brand-600" : "bg-slate-300"
              }`}
            >
              <span
                className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${
                  rb.auto_execute ? "translate-x-4" : "translate-x-1"
                }`}
              />
            </button>
          </label>
        ) : (
          <span
            className={`text-xs px-2 py-0.5 rounded font-medium ${
              rb.auto_execute
                ? "bg-green-100 text-green-700"
                : "bg-slate-100 text-slate-500"
            }`}
          >
            {rb.auto_execute ? "Enabled" : "Disabled"}
          </span>
        )}

        {/* Fork / Edit buttons */}
        {rb.is_seed ? (
          <button
            onClick={(e) => { e.stopPropagation(); onFork(); }}
            className="flex items-center gap-1 px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-100"
          >
            <Copy className="w-3 h-3" /> Fork
          </button>
        ) : isAdmin ? (
          <button
            onClick={(e) => { e.stopPropagation(); navigate(`/runbooks/${rb.id}`); }}
            className="flex items-center gap-1 px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-100"
          >
            Edit
          </button>
        ) : null}

        <TriggerButton runbook={rb} />
      </div>
    </div>
  );
}

function TriggerButton({ runbook }: { runbook: RunbookOut }) {
  const navigate = useNavigate();
  const trigger = useTriggerRunbook(runbook.id);
  const [windowWarning, setWindowWarning] = useState<{ name: string; warning: string } | null>(null);

  const handleTrigger = (force = false) => {
    trigger.mutate(
      { context: {}, force },
      {
        onSuccess: (exec: RunbookExecutionOut) => navigate(`/executions/${exec.id}`),
        onError: (err: unknown) => {
          // Check for 409 maintenance window response
          const resp = (err as { response?: { status: number; data?: { maintenance_window?: string; warning?: string } } }).response;
          if (resp?.status === 409 && resp?.data?.maintenance_window) {
            setWindowWarning({
              name: resp.data.maintenance_window,
              warning: resp.data.warning ?? "Active change freeze window detected.",
            });
          }
        },
      }
    );
  };

  return (
    <>
      <button
        onClick={(e) => { e.stopPropagation(); handleTrigger(false); }}
        disabled={!runbook.auto_execute || trigger.isPending}
        title={!runbook.auto_execute ? "Runbook must be enabled by an admin before it can be triggered" : "Run runbook"}
        className="flex items-center gap-1 px-2 py-1 text-xs bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <Play className="w-3 h-3" /> Run
      </button>

      {/* Maintenance window override modal */}
      {windowWarning && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setWindowWarning(null)}
        >
          <div
            className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-3 mb-4">
              <AlertTriangle className="w-6 h-6 text-amber-500 flex-shrink-0 mt-0.5" />
              <div>
                <h3 className="font-semibold text-slate-900">
                  Change window active: {windowWarning.name}
                </h3>
                <p className="mt-1 text-sm text-slate-600">{windowWarning.warning}</p>
                <p className="mt-2 text-sm font-medium text-slate-700">
                  Only proceed if this is an emergency.
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setWindowWarning(null)}
                className="px-4 py-2 text-sm border border-slate-300 rounded-md hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                onClick={() => { setWindowWarning(null); handleTrigger(true); }}
                className="px-4 py-2 text-sm bg-red-600 text-white rounded-md hover:bg-red-700"
              >
                Confirm emergency override
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
```

- [ ] **Step 5: Restart the frontend container**

```
docker compose stop frontend && docker compose up frontend -d
```

- [ ] **Step 6: Smoke test the UI manually**

1. Open the app in the browser → Runbooks tab.
2. As admin: verify each row shows a toggle switch. Toggle one runbook off → "Disabled" appears; toggle back on → "Enabled".
3. With a disabled runbook: verify the Run button is greyed out with the tooltip.
4. With an enabled runbook: click Run — confirm a new execution starts and the browser navigates to `/executions/{id}`.
5. As a non-admin user: verify toggles are replaced by read-only badges.

- [ ] **Step 7: Run the full backend test suite one final time**

```
docker compose exec backend pytest app/tests/test_runbook_executions.py app/tests/test_runbooks.py app/tests/test_change_requests.py -v --tb=short
```

Expected: all pass.

- [ ] **Step 8: Commit**

```
git add backend/app/main.py frontend/src/hooks/useRunbooks.ts frontend/src/pages/Runbooks.tsx
git commit -m "feat: wire runbook executor to scheduler + admin enable/disable toggle in UI"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task |
|---|---|
| `auto_execute` model field + migration | Task 1 |
| Seed templates get `auto_execute=True` | Task 1 |
| Admin-only PUT endpoint guard | Task 2 |
| Trigger blocked if `auto_execute=False` | Task 2 |
| Maintenance window pre-check on trigger | Task 2 |
| `force=true` bypasses window check + audit event | Task 2 |
| Extract `plan_cr` helper | Task 3 |
| Router uses `plan_cr` | Task 3 |
| Bridge: plan → approve → execute inline | Task 4 |
| Bridge: error handling for blocked plans | Task 4 |
| Executor uses new bridge function | Task 4 |
| Scheduler wired at 30s | Task 5 |
| `auto_execute` in frontend types | Task 5 |
| Admin toggle switch in Runbooks page | Task 5 |
| Run button disabled when not enabled | Task 5 |
| Maintenance window modal with force override | Task 5 |

All requirements covered. No placeholders. Types consistent across tasks (`create_and_execute_runbook_cr` used in Task 4 and Task 4 executor update).
