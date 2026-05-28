# Recurring Job Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `RecurringJob` model, cron-based scheduler, REST API, and Scheduled Ops UI that fires CRs on a schedule with auto-approval.

**Architecture:** A `recurring_jobs` DB table is loaded into the existing APScheduler instance (`scheduler_service.py`) at startup. Each job fires `_fire_recurring_job(job_id)` which creates a CR, generates its plan, auto-approves it, and triggers execution via the existing `trigger_change_workflow`. CRUD endpoints keep APScheduler in sync without restarts. The Scheduled Ops page replaces the existing stub with a full recurring-jobs management UI.

**Tech Stack:** Python 3.12, SQLAlchemy async ORM, APScheduler 3.x, croniter, FastAPI, React 18, TypeScript, TanStack Query

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `backend/app/models/recurring_job.py` | Create | `RecurringJob` ORM model + `RecurringJobType` enum |
| `backend/alembic/versions/063_add_recurring_jobs.py` | Create | DB migration |
| `backend/app/schemas/recurring_job.py` | Create | Pydantic request/response schemas |
| `backend/app/services/recurring_job_service.py` | Create | Fire function, register/deregister, cron helpers |
| `backend/app/routers/recurring_jobs.py` | Create | REST CRUD + enable/disable/run-now endpoints |
| `backend/app/services/scheduler_service.py` | Modify | Load recurring jobs at startup |
| `backend/app/main.py` | Modify | Include recurring jobs router |
| `backend/app/models/__init__.py` | Modify | Export `RecurringJob` |
| `backend/app/tests/test_recurring_jobs.py` | Create | API integration tests |
| `frontend/src/api/endpoints.ts` | Modify | Add `recurringJobsApi` |
| `frontend/src/pages/ScheduledOperations.tsx` | Rewrite | Full recurring jobs management UI |

---

## Task 1: RecurringJob Model

**Files:**
- Create: `backend/app/models/recurring_job.py`
- Modify: `backend/app/models/__init__.py`

- [ ] **Step 1: Write the failing import test**

```python
# backend/app/tests/test_recurring_jobs.py
import pytest
from app.models.recurring_job import RecurringJob, RecurringJobType

def test_recurring_job_type_enum():
    assert RecurringJobType.backup == "backup"
    assert RecurringJobType.scheduled_restore == "scheduled_restore"
    assert RecurringJobType.scheduled_op == "scheduled_op"
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /home/ec2-user/nexplane && docker exec nexplane-backend-1 bash -c \
  "cd /app && python -m pytest app/tests/test_recurring_jobs.py::test_recurring_job_type_enum -v"
```
Expected: `ModuleNotFoundError: No module named 'app.models.recurring_job'`

- [ ] **Step 3: Create the model**

```python
# backend/app/models/recurring_job.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, JSON, ForeignKey, Enum as SAEnum, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from app.database import Base


class RecurringJobType(str, enum.Enum):
    backup = "backup"
    scheduled_restore = "scheduled_restore"
    scheduled_op = "scheduled_op"


class RecurringJob(Base):
    __tablename__ = "recurring_jobs"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    job_type: Mapped[RecurringJobType] = mapped_column(
        SAEnum(RecurringJobType, name="recurring_job_type"), nullable=False
    )
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True
    )
    action_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    target_description: Mapped[str] = mapped_column(Text, nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    schedule_preset: Mapped[str | None] = mapped_column(String(50), nullable=True)
    schedule_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_cr_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Export from `__init__.py`**

Open `backend/app/models/__init__.py`. Add at the end:

```python
from app.models.recurring_job import RecurringJob, RecurringJobType  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

```bash
docker exec nexplane-backend-1 bash -c \
  "cd /app && python -m pytest app/tests/test_recurring_jobs.py::test_recurring_job_type_enum -v"
```
Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/recurring_job.py backend/app/models/__init__.py \
        backend/app/tests/test_recurring_jobs.py
git commit -m "feat: add RecurringJob model"
```

---

## Task 2: Database Migration

**Files:**
- Create: `backend/alembic/versions/063_add_recurring_jobs.py`

- [ ] **Step 1: Write the migration**

```python
# backend/alembic/versions/063_add_recurring_jobs.py
"""add recurring_jobs table

Revision ID: 063_recurring_jobs
Revises: 062_oci_discover_types
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "063_recurring_jobs"
down_revision = "062_oci_discover_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE recurring_job_type AS ENUM ('backup', 'scheduled_restore', 'scheduled_op')")
    op.create_table(
        "recurring_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("job_type", sa.Enum("backup", "scheduled_restore", "scheduled_op",
                                       name="recurring_job_type", create_type=False), nullable=False),
        sa.Column("connector_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("connectors.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action_id", sa.String(255), nullable=False),
        sa.Column("parameters", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("target_description", sa.Text, nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("schedule_preset", sa.String(50), nullable=True),
        sa.Column("schedule_hour", sa.Integer, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_cr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_recurring_jobs_organization_id", "recurring_jobs", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_recurring_jobs_organization_id", "recurring_jobs")
    op.drop_table("recurring_jobs")
    op.execute("DROP TYPE recurring_job_type")
```

- [ ] **Step 2: Run the migration**

```bash
docker exec nexplane-backend-1 bash -c "cd /app && alembic upgrade head"
```
Expected: `Running upgrade 062_oci_discover_types -> 063_recurring_jobs, add recurring_jobs table`

- [ ] **Step 3: Verify table exists**

```bash
docker exec nexplane-backend-1 python3 -c "
import psycopg2
conn = psycopg2.connect(host='db', dbname='nexplane', user='nexplane', password='nexplane_dev')
cur = conn.cursor()
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='recurring_jobs' ORDER BY ordinal_position\")
print([r[0] for r in cur.fetchall()])
"
```
Expected: `['id', 'organization_id', 'name', 'job_type', 'connector_id', 'action_id', 'parameters', 'target_description', 'cron_expression', 'schedule_preset', 'schedule_hour', 'enabled', 'last_run_at', 'last_cr_id', 'next_run_at', 'created_by', 'created_at']`

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/063_add_recurring_jobs.py
git commit -m "feat: migration — add recurring_jobs table"
```

---

## Task 3: Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/recurring_job.py`

- [ ] **Step 1: Write the schema test**

Add to `backend/app/tests/test_recurring_jobs.py`:

```python
from app.schemas.recurring_job import RecurringJobCreate, RecurringJobRead, RecurringJobUpdate
from app.models.recurring_job import RecurringJobType
import uuid
from datetime import datetime, timezone

def test_recurring_job_create_schema():
    data = RecurringJobCreate(
        name="Daily DB Backup",
        job_type=RecurringJobType.backup,
        action_id="ssm_command",
        parameters={"instance_id": "i-123", "commands": ["echo hi"]},
        target_description="nexplane postgres DB",
        cron_expression="0 2 * * *",
        schedule_preset="daily",
        schedule_hour=2,
    )
    assert data.name == "Daily DB Backup"
    assert data.job_type == RecurringJobType.backup
    assert data.connector_id is None

def test_recurring_job_update_schema_partial():
    update = RecurringJobUpdate(enabled=False)
    dumped = update.model_dump(exclude_none=True)
    assert dumped == {"enabled": False}
```

- [ ] **Step 2: Run to verify it fails**

```bash
docker exec nexplane-backend-1 bash -c \
  "cd /app && python -m pytest app/tests/test_recurring_jobs.py::test_recurring_job_create_schema -v"
```
Expected: `ModuleNotFoundError: No module named 'app.schemas.recurring_job'`

- [ ] **Step 3: Create the schemas**

```python
# backend/app/schemas/recurring_job.py
import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.recurring_job import RecurringJobType


class RecurringJobCreate(BaseModel):
    name: str
    job_type: RecurringJobType
    connector_id: uuid.UUID | None = None
    action_id: str
    parameters: dict = {}
    target_description: str
    cron_expression: str
    schedule_preset: str | None = None
    schedule_hour: int | None = None


class RecurringJobUpdate(BaseModel):
    name: str | None = None
    connector_id: uuid.UUID | None = None
    action_id: str | None = None
    parameters: dict | None = None
    target_description: str | None = None
    cron_expression: str | None = None
    schedule_preset: str | None = None
    schedule_hour: int | None = None
    enabled: bool | None = None


class RecurringJobRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    job_type: RecurringJobType
    connector_id: uuid.UUID | None
    action_id: str
    parameters: dict
    target_description: str
    cron_expression: str
    schedule_preset: str | None
    schedule_hour: int | None
    enabled: bool
    last_run_at: datetime | None
    last_cr_id: uuid.UUID | None
    next_run_at: datetime | None
    created_by: uuid.UUID
    created_at: datetime
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 bash -c \
  "cd /app && python -m pytest app/tests/test_recurring_jobs.py::test_recurring_job_create_schema app/tests/test_recurring_jobs.py::test_recurring_job_update_schema_partial -v"
```
Expected: both `PASSED`

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/recurring_job.py backend/app/tests/test_recurring_jobs.py
git commit -m "feat: add RecurringJob pydantic schemas"
```

---

## Task 4: Recurring Job Service

**Files:**
- Create: `backend/app/services/recurring_job_service.py`

This service owns three things: cron math (`compute_next_run`), APScheduler registration (`register_job` / `deregister_job`), and the fire function (`_fire_recurring_job`) that creates + approves + executes the CR.

- [ ] **Step 1: Write the cron helper test**

Add to `backend/app/tests/test_recurring_jobs.py`:

```python
from app.services.recurring_job_service import compute_next_run
from datetime import datetime, timezone

def test_compute_next_run_returns_future():
    next_run = compute_next_run("0 2 * * *")
    assert next_run > datetime.now(tz=timezone.utc)

def test_compute_next_run_daily_2am():
    next_run = compute_next_run("0 2 * * *")
    assert next_run.hour == 2
    assert next_run.minute == 0
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker exec nexplane-backend-1 bash -c \
  "cd /app && python -m pytest app/tests/test_recurring_jobs.py::test_compute_next_run_returns_future app/tests/test_recurring_jobs.py::test_compute_next_run_daily_2am -v"
```
Expected: `ModuleNotFoundError: No module named 'app.services.recurring_job_service'`

- [ ] **Step 3: Create the service**

```python
# backend/app/services/recurring_job_service.py
from __future__ import annotations
import uuid
import logging
from datetime import datetime, timezone
from sqlalchemy import select

from croniter import croniter

logger = logging.getLogger(__name__)

# Set by scheduler_service.init_scheduler — the same APScheduler instance
_scheduler = None


def set_scheduler(scheduler_instance) -> None:
    global _scheduler
    _scheduler = scheduler_instance


def compute_next_run(cron_expression: str) -> datetime:
    it = croniter(cron_expression, datetime.now(tz=timezone.utc))
    return it.get_next(datetime)


def _apscheduler_id(job_id: str) -> str:
    return f"recurring_job_{job_id}"


def register_job(job) -> None:
    """Register a RecurringJob with APScheduler. Idempotent — replaces existing."""
    if _scheduler is None:
        return
    from apscheduler.triggers.cron import CronTrigger
    _scheduler.add_job(
        _fire_recurring_job,
        CronTrigger.from_crontab(job.cron_expression, timezone="UTC"),
        id=_apscheduler_id(str(job.id)),
        args=[str(job.id)],
        replace_existing=True,
    )
    logger.info(f"Registered recurring job {job.id} ({job.name}) with cron '{job.cron_expression}'")


def deregister_job(job_id: str) -> None:
    """Remove a RecurringJob from APScheduler. Safe to call if not registered."""
    if _scheduler is None:
        return
    apscheduler_id = _apscheduler_id(job_id)
    if _scheduler.get_job(apscheduler_id):
        _scheduler.remove_job(apscheduler_id)
        logger.info(f"Deregistered recurring job {job_id}")


async def fire_job_now(job) -> None:
    """Trigger a RecurringJob immediately, outside its schedule."""
    await _fire_recurring_job(str(job.id))


async def _fire_recurring_job(job_id: str) -> None:
    """
    Core fire function called by APScheduler on schedule (or fire_job_now).

    Flow:
    1. Load job from DB
    2. Create CR (draft)
    3. Generate plan via plan_cr → sets CR status = planned
    4. Create auto-Approval record + set CR status = approved
    5. Commit
    6. Trigger execution workflow
    7. Update job.last_run_at, last_cr_id, next_run_at
    """
    from app.database import AsyncSessionLocal
    from app.models.recurring_job import RecurringJob
    from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel
    from app.models.approval import Approval, ApprovalDecision
    from app.services.change_plan_service import plan_cr

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(RecurringJob).where(RecurringJob.id == uuid.UUID(job_id))
        )
        job = result.scalar_one_or_none()
        if not job or not job.enabled:
            logger.info(f"Recurring job {job_id} skipped (not found or disabled)")
            return

        # Derive change_type from action_id — action_id must match a ChangeType value
        try:
            change_type = ChangeType(job.action_id)
        except ValueError:
            logger.warning(
                f"Recurring job {job_id}: action_id '{job.action_id}' is not a valid ChangeType, "
                f"defaulting to ssm_command"
            )
            change_type = ChangeType.ssm_command

        # 1. Create CR in draft
        cr = ChangeRequest(
            organization_id=job.organization_id,
            requester_id=job.created_by,
            title=job.name,
            description=f"Auto-created by recurring job: {job.target_description}",
            change_type=change_type,
            risk_level=RiskLevel.low,
            desired_outcome=job.parameters,
            target_asset_ids=[],
            status=ChangeRequestStatus.draft,
            source="recurring_job",
        )
        db.add(cr)
        await db.flush()

        # 2. Generate plan (sets status = planned, creates ChangePlan row)
        try:
            await plan_cr(db, cr)
        except Exception as e:
            logger.warning(f"Recurring job {job_id}: plan generation failed: {e}. Continuing with no plan.")
            cr.status = ChangeRequestStatus.planned

        # 3. Auto-approve
        approval = Approval(
            change_request_id=cr.id,
            approver_id=job.created_by,
            decision=ApprovalDecision.approved,
            comment="Auto-approved by recurring job schedule",
        )
        db.add(approval)
        cr.status = ChangeRequestStatus.approved
        await db.flush()

        # 4. Update job fields
        job.last_run_at = datetime.now(tz=timezone.utc)
        job.last_cr_id = cr.id
        job.next_run_at = compute_next_run(job.cron_expression)

        await db.commit()

        # 5. Trigger execution workflow (outside transaction — workflow manages its own DB session)
        try:
            from app.workflows.execute_change_workflow import trigger_change_workflow
            await trigger_change_workflow(str(cr.id))
            logger.info(f"Recurring job {job_id} fired successfully, CR {cr.id} executing")
        except Exception as e:
            logger.error(f"Recurring job {job_id}: failed to trigger workflow for CR {cr.id}: {e}")
```

- [ ] **Step 4: Install `croniter` in backend container**

Check if already installed:
```bash
docker exec nexplane-backend-1 python3 -c "import croniter; print(croniter.__version__)"
```

If not installed:
```bash
docker exec nexplane-backend-1 pip install croniter
# Then add to backend/requirements.txt:
echo "croniter>=1.4.0" >> backend/requirements.txt
```

- [ ] **Step 5: Run the cron tests**

```bash
docker exec nexplane-backend-1 bash -c \
  "cd /app && python -m pytest app/tests/test_recurring_jobs.py::test_compute_next_run_returns_future app/tests/test_recurring_jobs.py::test_compute_next_run_daily_2am -v"
```
Expected: both `PASSED`

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/recurring_job_service.py backend/app/tests/test_recurring_jobs.py
git commit -m "feat: add recurring_job_service with fire function and cron helpers"
```

---

## Task 5: REST Router

**Files:**
- Create: `backend/app/routers/recurring_jobs.py`

- [ ] **Step 1: Write the API tests**

Add to `backend/app/tests/test_recurring_jobs.py`:

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_create_recurring_job(auth_client: AsyncClient):
    resp = await auth_client.post("/recurring-jobs", json={
        "name": "Daily DB Backup",
        "job_type": "backup",
        "action_id": "ssm_command",
        "parameters": {"instance_id": "i-123", "commands": ["echo hi"]},
        "target_description": "nexplane postgres DB",
        "cron_expression": "0 2 * * *",
        "schedule_preset": "daily",
        "schedule_hour": 2,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Daily DB Backup"
    assert data["job_type"] == "backup"
    assert data["enabled"] is True
    assert data["next_run_at"] is not None

@pytest.mark.asyncio
async def test_list_recurring_jobs(auth_client: AsyncClient):
    await auth_client.post("/recurring-jobs", json={
        "name": "Test Job",
        "job_type": "scheduled_op",
        "action_id": "ssm_command",
        "parameters": {},
        "target_description": "test target",
        "cron_expression": "0 * * * *",
    })
    resp = await auth_client.get("/recurring-jobs")
    assert resp.status_code == 200
    assert any(j["name"] == "Test Job" for j in resp.json())

@pytest.mark.asyncio
async def test_disable_recurring_job(auth_client: AsyncClient):
    create_resp = await auth_client.post("/recurring-jobs", json={
        "name": "Disable Me",
        "job_type": "scheduled_op",
        "action_id": "ssm_command",
        "parameters": {},
        "target_description": "test",
        "cron_expression": "0 * * * *",
    })
    job_id = create_resp.json()["id"]
    resp = await auth_client.post(f"/recurring-jobs/{job_id}/disable")
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

@pytest.mark.asyncio
async def test_delete_recurring_job(auth_client: AsyncClient):
    create_resp = await auth_client.post("/recurring-jobs", json={
        "name": "Delete Me",
        "job_type": "scheduled_op",
        "action_id": "ssm_command",
        "parameters": {},
        "target_description": "test",
        "cron_expression": "0 * * * *",
    })
    job_id = create_resp.json()["id"]
    resp = await auth_client.delete(f"/recurring-jobs/{job_id}")
    assert resp.status_code == 204
    get_resp = await auth_client.get(f"/recurring-jobs/{job_id}")
    assert get_resp.status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker exec nexplane-backend-1 bash -c \
  "cd /app && python -m pytest app/tests/test_recurring_jobs.py::test_create_recurring_job -v"
```
Expected: `404 Not Found` or `422` — route doesn't exist yet

- [ ] **Step 3: Create the router**

```python
# backend/app/routers/recurring_jobs.py
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.recurring_job import RecurringJob
from app.models.user import User
from app.routers import current_user
from app.schemas.recurring_job import RecurringJobCreate, RecurringJobUpdate, RecurringJobRead
from app.services.recurring_job_service import (
    register_job, deregister_job, fire_job_now, compute_next_run,
)

router = APIRouter(prefix="/recurring-jobs", tags=["Recurring Jobs"])


async def _get_job(db: AsyncSession, job_id: uuid.UUID, org_id: uuid.UUID) -> RecurringJob:
    result = await db.execute(
        select(RecurringJob).where(
            RecurringJob.id == job_id,
            RecurringJob.organization_id == org_id,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Recurring job not found")
    return job


@router.get("", response_model=list[RecurringJobRead])
async def list_jobs(
    job_type: str | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(RecurringJob).where(RecurringJob.organization_id == user.organization_id)
    if job_type:
        q = q.where(RecurringJob.job_type == job_type)
    result = await db.execute(q.order_by(RecurringJob.created_at.desc()))
    return result.scalars().all()


@router.post("", response_model=RecurringJobRead, status_code=201)
async def create_job(
    body: RecurringJobCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = RecurringJob(
        organization_id=user.organization_id,
        created_by=user.id,
        next_run_at=compute_next_run(body.cron_expression),
        **body.model_dump(),
    )
    db.add(job)
    await db.flush()
    register_job(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/{job_id}", response_model=RecurringJobRead)
async def get_job(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_job(db, job_id, user.organization_id)


@router.put("/{job_id}", response_model=RecurringJobRead)
async def update_job(
    job_id: uuid.UUID,
    body: RecurringJobUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id, user.organization_id)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(job, field, value)
    if body.cron_expression:
        job.next_run_at = compute_next_run(body.cron_expression)
    deregister_job(str(job.id))
    if job.enabled:
        register_job(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.delete("/{job_id}", status_code=204)
async def delete_job(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id, user.organization_id)
    deregister_job(str(job.id))
    await db.delete(job)
    await db.commit()


@router.post("/{job_id}/enable", response_model=RecurringJobRead)
async def enable_job(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id, user.organization_id)
    job.enabled = True
    job.next_run_at = compute_next_run(job.cron_expression)
    register_job(job)
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/{job_id}/disable", response_model=RecurringJobRead)
async def disable_job(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id, user.organization_id)
    job.enabled = False
    deregister_job(str(job.id))
    await db.commit()
    await db.refresh(job)
    return job


@router.post("/{job_id}/run-now", response_model=RecurringJobRead)
async def run_now(
    job_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _get_job(db, job_id, user.organization_id)
    await fire_job_now(job)
    await db.refresh(job)
    return job
```

- [ ] **Step 4: Run the API tests (they will still fail — router not wired yet)**

```bash
docker exec nexplane-backend-1 bash -c \
  "cd /app && python -m pytest app/tests/test_recurring_jobs.py::test_create_recurring_job -v"
```
Expected: `404` — confirms router exists but isn't registered

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/recurring_jobs.py backend/app/tests/test_recurring_jobs.py
git commit -m "feat: add recurring_jobs router"
```

---

## Task 6: Wire Router and Scheduler

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/scheduler_service.py`

- [ ] **Step 1: Register router in `main.py`**

In `backend/app/main.py`, add after the existing imports (around line 31):

```python
from app.routers import recurring_jobs as recurring_jobs_router
```

Add after the `app.include_router(api_tokens_router)` line (around line 167):

```python
app.include_router(recurring_jobs_router.router)
```

- [ ] **Step 2: Load recurring jobs at startup in `scheduler_service.py`**

In `backend/app/services/scheduler_service.py`, in the `start()` function, add after the existing scheduled ingest loading block (after line 89):

```python
    # Load recurring jobs
    loaded_recurring = 0
    try:
        async with _db_factory() as db:
            from app.models.recurring_job import RecurringJob
            from app.services.recurring_job_service import register_job, set_scheduler
            set_scheduler(scheduler)
            result = await db.execute(
                select(RecurringJob).where(RecurringJob.enabled == True)
            )
            jobs = result.scalars().all()
            for job in jobs:
                register_job(job)
                loaded_recurring += 1
    except Exception as e:
        logger.warning(f"Could not load recurring jobs on startup: {e}")
    logger.info(f"Loaded {loaded_recurring} recurring jobs")
```

- [ ] **Step 3: Run the API tests**

```bash
docker exec nexplane-backend-1 bash -c \
  "cd /app && python -m pytest app/tests/test_recurring_jobs.py -v"
```
Expected: all 4 API tests `PASSED`

- [ ] **Step 4: Restart backend to pick up changes, verify health**

```bash
docker compose restart backend
curl -s http://localhost:8000/health
```
Expected: `{"status": "ok", "service": "nexplane"}`

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/services/scheduler_service.py
git commit -m "feat: wire recurring jobs router and load jobs into APScheduler at startup"
```

---

## Task 7: Frontend API Client

**Files:**
- Modify: `frontend/src/api/endpoints.ts`

- [ ] **Step 1: Add `recurringJobsApi` to `endpoints.ts`**

In `frontend/src/api/endpoints.ts`, add after the existing API objects (before the last export or at the end of the file):

```typescript
export interface RecurringJob {
  id: string;
  organization_id: string;
  name: string;
  job_type: "backup" | "scheduled_restore" | "scheduled_op";
  connector_id: string | null;
  action_id: string;
  parameters: Record<string, unknown>;
  target_description: string;
  cron_expression: string;
  schedule_preset: string | null;
  schedule_hour: number | null;
  enabled: boolean;
  last_run_at: string | null;
  last_cr_id: string | null;
  next_run_at: string | null;
  created_by: string;
  created_at: string;
}

export interface RecurringJobCreate {
  name: string;
  job_type: "backup" | "scheduled_restore" | "scheduled_op";
  connector_id?: string;
  action_id: string;
  parameters?: Record<string, unknown>;
  target_description: string;
  cron_expression: string;
  schedule_preset?: string;
  schedule_hour?: number;
}

export const recurringJobsApi = {
  list: (job_type?: string) =>
    apiClient.get<RecurringJob[]>("/recurring-jobs", { params: job_type ? { job_type } : {} }).then((r) => r.data),
  get: (id: string) =>
    apiClient.get<RecurringJob>(`/recurring-jobs/${id}`).then((r) => r.data),
  create: (data: RecurringJobCreate) =>
    apiClient.post<RecurringJob>("/recurring-jobs", data).then((r) => r.data),
  update: (id: string, data: Partial<RecurringJobCreate> & { enabled?: boolean }) =>
    apiClient.put<RecurringJob>(`/recurring-jobs/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    apiClient.delete(`/recurring-jobs/${id}`),
  enable: (id: string) =>
    apiClient.post<RecurringJob>(`/recurring-jobs/${id}/enable`).then((r) => r.data),
  disable: (id: string) =>
    apiClient.post<RecurringJob>(`/recurring-jobs/${id}/disable`).then((r) => r.data),
  runNow: (id: string) =>
    apiClient.post<RecurringJob>(`/recurring-jobs/${id}/run-now`).then((r) => r.data),
};
```

- [ ] **Step 2: Copy to EC2**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 \
  frontend/src/api/endpoints.ts \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/frontend/src/api/endpoints.ts
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/endpoints.ts
git commit -m "feat: add recurringJobsApi to frontend API client"
```

---

## Task 8: Scheduled Operations Page

**Files:**
- Rewrite: `frontend/src/pages/ScheduledOperations.tsx`

- [ ] **Step 1: Write the page**

```tsx
// frontend/src/pages/ScheduledOperations.tsx
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { format, formatDistanceToNow, parseISO } from "date-fns";
import {
  Play, Pause, Trash2, Plus, ChevronDown, ChevronRight,
  Clock, CheckCircle2, AlertTriangle, XCircle, RefreshCw,
} from "lucide-react";
import { recurringJobsApi, RecurringJob, RecurringJobCreate } from "../api/endpoints";

const JOB_TYPE_LABELS: Record<string, string> = {
  backup: "Backup",
  scheduled_restore: "Scheduled Restore",
  scheduled_op: "Scheduled Op",
};

const JOB_TYPE_COLORS: Record<string, string> = {
  backup: "bg-blue-100 text-blue-700",
  scheduled_restore: "bg-amber-100 text-amber-700",
  scheduled_op: "bg-slate-100 text-slate-600",
};

function jobStatus(job: RecurringJob): "healthy" | "overdue" | "disabled" | "never_run" {
  if (!job.enabled) return "disabled";
  if (!job.last_run_at) return "never_run";
  return "healthy";
}

function StatusBadge({ job }: { job: RecurringJob }) {
  const status = jobStatus(job);
  const config = {
    healthy: { icon: CheckCircle2, label: "Healthy", cls: "text-emerald-600" },
    overdue: { icon: AlertTriangle, label: "Overdue", cls: "text-amber-600" },
    disabled: { icon: XCircle, label: "Disabled", cls: "text-slate-400" },
    never_run: { icon: Clock, label: "Scheduled", cls: "text-slate-500" },
  }[status];
  const Icon = config.icon;
  return (
    <span className={`flex items-center gap-1 text-xs font-medium ${config.cls}`}>
      <Icon className="w-3.5 h-3.5" />
      {config.label}
    </span>
  );
}

function CronPreview({ cron }: { cron: string }) {
  const labels: Record<string, string> = {
    "0 * * * *": "Hourly",
    "0 2 * * *": "Daily at 2am",
    "0 0 * * *": "Daily at midnight",
    "0 2 * * 1": "Weekly Mon 2am",
    "0 2 * * 0": "Weekly Sun 2am",
  };
  return (
    <span className="text-xs text-slate-400 font-mono">
      {labels[cron] ?? cron}
    </span>
  );
}

function JobRow({ job }: { job: RecurringJob }) {
  const [expanded, setExpanded] = useState(false);
  const qc = useQueryClient();

  const enableMutation = useMutation({
    mutationFn: () => recurringJobsApi.enable(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring-jobs"] }),
  });
  const disableMutation = useMutation({
    mutationFn: () => recurringJobsApi.disable(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring-jobs"] }),
  });
  const runNowMutation = useMutation({
    mutationFn: () => recurringJobsApi.runNow(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring-jobs"] }),
  });
  const deleteMutation = useMutation({
    mutationFn: () => recurringJobsApi.delete(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring-jobs"] }),
  });

  return (
    <>
      <tr
        className={`border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${expanded ? "bg-slate-50" : ""}`}
        onClick={() => setExpanded((v) => !v)}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            {expanded ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
            <span className="text-sm font-medium text-slate-900">{job.name}</span>
          </div>
        </td>
        <td className="px-4 py-3">
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${JOB_TYPE_COLORS[job.job_type]}`}>
            {JOB_TYPE_LABELS[job.job_type]}
          </span>
        </td>
        <td className="px-4 py-3 text-xs text-slate-600">{job.target_description}</td>
        <td className="px-4 py-3"><CronPreview cron={job.cron_expression} /></td>
        <td className="px-4 py-3 text-xs text-slate-500">
          {job.last_run_at ? formatDistanceToNow(parseISO(job.last_run_at), { addSuffix: true }) : "—"}
        </td>
        <td className="px-4 py-3 text-xs text-slate-500">
          {job.next_run_at ? formatDistanceToNow(parseISO(job.next_run_at), { addSuffix: true }) : "—"}
        </td>
        <td className="px-4 py-3"><StatusBadge job={job} /></td>
      </tr>
      {expanded && (
        <tr className="bg-slate-50 border-b border-slate-100">
          <td colSpan={7} className="px-8 py-4">
            <div className="flex items-center gap-3">
              <button
                onClick={(e) => { e.stopPropagation(); runNowMutation.mutate(); }}
                disabled={runNowMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-600 text-white text-xs rounded hover:bg-brand-700 disabled:opacity-50"
              >
                <RefreshCw className="w-3 h-3" />
                Run Now
              </button>
              {job.enabled ? (
                <button
                  onClick={(e) => { e.stopPropagation(); disableMutation.mutate(); }}
                  disabled={disableMutation.isPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 border border-slate-300 text-slate-600 text-xs rounded hover:bg-slate-100 disabled:opacity-50"
                >
                  <Pause className="w-3 h-3" />
                  Disable
                </button>
              ) : (
                <button
                  onClick={(e) => { e.stopPropagation(); enableMutation.mutate(); }}
                  disabled={enableMutation.isPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 border border-slate-300 text-slate-600 text-xs rounded hover:bg-slate-100 disabled:opacity-50"
                >
                  <Play className="w-3 h-3" />
                  Enable
                </button>
              )}
              {job.last_cr_id && (
                <Link
                  to={`/change-requests/${job.last_cr_id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="text-xs text-brand-600 hover:underline"
                >
                  View last CR →
                </Link>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  if (window.confirm(`Delete job "${job.name}"? This cannot be undone.`)) {
                    deleteMutation.mutate();
                  }
                }}
                disabled={deleteMutation.isPending}
                className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-red-600 text-xs rounded hover:bg-red-50 disabled:opacity-50"
              >
                <Trash2 className="w-3 h-3" />
                Delete
              </button>
            </div>
            <div className="mt-3 grid grid-cols-3 gap-4 text-xs text-slate-500">
              <div><span className="font-medium text-slate-700">Action:</span> {job.action_id}</div>
              <div><span className="font-medium text-slate-700">Cron:</span> <code>{job.cron_expression}</code></div>
              <div><span className="font-medium text-slate-700">Connector:</span> {job.connector_id ?? "default"}</div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function CreateJobDrawer({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<RecurringJobCreate>({
    name: "",
    job_type: "scheduled_op",
    action_id: "ssm_command",
    parameters: {},
    target_description: "",
    cron_expression: "0 2 * * *",
    schedule_preset: "daily",
    schedule_hour: 2,
  });
  const [scheduleTab, setScheduleTab] = useState<"daily" | "weekly" | "hourly" | "custom">("daily");
  const [hour, setHour] = useState(2);
  const [customCron, setCustomCron] = useState("");
  const [paramsText, setParamsText] = useState("{}");
  const [paramsError, setParamsError] = useState("");

  const mutation = useMutation({
    mutationFn: (data: RecurringJobCreate) => recurringJobsApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["recurring-jobs"] });
      onClose();
    },
  });

  function buildCron(): string {
    if (scheduleTab === "custom") return customCron;
    if (scheduleTab === "hourly") return "0 * * * *";
    if (scheduleTab === "weekly") return `0 ${hour} * * 1`;
    return `0 ${hour} * * *`; // daily
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    let params: Record<string, unknown> = {};
    try {
      params = JSON.parse(paramsText);
      setParamsError("");
    } catch {
      setParamsError("Invalid JSON");
      return;
    }
    mutation.mutate({
      ...form,
      cron_expression: buildCron(),
      schedule_preset: scheduleTab === "custom" ? undefined : scheduleTab,
      schedule_hour: scheduleTab === "custom" || scheduleTab === "hourly" ? undefined : hour,
      parameters: params,
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div className="relative w-[480px] bg-white shadow-xl flex flex-col h-full overflow-y-auto">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">New Scheduled Job</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-sm">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="flex-1 px-6 py-5 space-y-5">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Name</label>
            <input
              required
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              placeholder="Daily nexplane DB backup"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Job Type</label>
            <select
              value={form.job_type}
              onChange={(e) => setForm((f) => ({ ...f, job_type: e.target.value as RecurringJobCreate["job_type"] }))}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              <option value="backup">Backup</option>
              <option value="scheduled_restore">Scheduled Restore</option>
              <option value="scheduled_op">Scheduled Op</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Action ID</label>
            <input
              required
              value={form.action_id}
              onChange={(e) => setForm((f) => ({ ...f, action_id: e.target.value }))}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-500"
              placeholder="ssm_command"
            />
            <p className="text-xs text-slate-400 mt-0.5">Must match a ChangeType value (e.g. ssm_command, create_backup)</p>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Target Description</label>
            <input
              required
              value={form.target_description}
              onChange={(e) => setForm((f) => ({ ...f, target_description: e.target.value }))}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              placeholder="nexplane postgres DB"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-2">Schedule</label>
            <div className="flex gap-1 mb-3">
              {(["daily", "weekly", "hourly", "custom"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setScheduleTab(tab)}
                  className={`px-3 py-1 text-xs rounded ${scheduleTab === tab ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                >
                  {tab.charAt(0).toUpperCase() + tab.slice(1)}
                </button>
              ))}
            </div>
            {scheduleTab !== "hourly" && scheduleTab !== "custom" && (
              <div className="flex items-center gap-2">
                <label className="text-xs text-slate-600">Hour (UTC):</label>
                <input
                  type="number"
                  min={0}
                  max={23}
                  value={hour}
                  onChange={(e) => setHour(Number(e.target.value))}
                  className="w-16 border border-slate-300 rounded px-2 py-1 text-sm text-center"
                />
                <span className="text-xs text-slate-400">→ cron: <code>{buildCron()}</code></span>
              </div>
            )}
            {scheduleTab === "custom" && (
              <div>
                <input
                  value={customCron}
                  onChange={(e) => setCustomCron(e.target.value)}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm font-mono"
                  placeholder="0 2 * * 1-5"
                />
                <p className="text-xs text-slate-400 mt-0.5">Standard 5-field cron expression (UTC)</p>
              </div>
            )}
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Parameters (JSON)</label>
            <textarea
              value={paramsText}
              onChange={(e) => setParamsText(e.target.value)}
              rows={5}
              className="w-full border border-slate-300 rounded px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-brand-500"
              placeholder='{"instance_id": "i-abc123", "commands": ["echo hi"]}'
            />
            {paramsError && <p className="text-xs text-red-600 mt-0.5">{paramsError}</p>}
          </div>
          <div className="pt-2 flex gap-3">
            <button
              type="submit"
              disabled={mutation.isPending}
              className="flex-1 bg-brand-600 text-white py-2 rounded text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
            >
              {mutation.isPending ? "Creating..." : "Create Job"}
            </button>
            <button type="button" onClick={onClose} className="px-4 py-2 border border-slate-300 text-slate-600 text-sm rounded hover:bg-slate-50">
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function ScheduledOperations() {
  const [showCreate, setShowCreate] = useState(false);
  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ["recurring-jobs"],
    queryFn: () => recurringJobsApi.list(),
    refetchInterval: 30_000,
  });

  const enabled = jobs.filter((j) => j.enabled).length;
  const disabled = jobs.filter((j) => !j.enabled).length;

  if (isLoading) return <div className="p-8 text-sm text-slate-500">Loading...</div>;

  return (
    <div className="p-8 max-w-6xl">
      {showCreate && <CreateJobDrawer onClose={() => setShowCreate(false)} />}

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Scheduled Operations</h1>
          <p className="text-sm text-slate-500 mt-1">
            {jobs.length} jobs · {enabled} enabled · {disabled} disabled
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700"
        >
          <Plus className="w-4 h-4" />
          New Job
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-lg p-12 text-center">
          <Clock className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <p className="text-slate-500 text-sm">No scheduled jobs yet.</p>
          <button
            onClick={() => setShowCreate(true)}
            className="mt-4 px-4 py-2 bg-brand-600 text-white text-sm rounded hover:bg-brand-700"
          >
            Create your first job
          </button>
        </div>
      ) : (
        <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Name</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Type</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Target</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Schedule</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Last Run</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Next Run</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Status</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <JobRow key={job.id} job={job} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Copy updated files to EC2**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 \
  frontend/src/pages/ScheduledOperations.tsx \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/frontend/src/pages/ScheduledOperations.tsx

scp -i /c/Users/john/.ssh/id_ed25519 \
  frontend/src/api/endpoints.ts \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/frontend/src/api/endpoints.ts
```

Vite will hot-reload automatically.

- [ ] **Step 3: Verify in browser**

Open `http://100.101.186.39:3000/scheduled-operations`. Expected: page loads, shows "No scheduled jobs yet" with a "Create your first job" button. Create a test job with action_id `ssm_command` and cron `0 * * * *`. Verify it appears in the table with status "Scheduled". Click the row to expand, verify Run Now / Disable / Delete buttons appear.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ScheduledOperations.tsx frontend/src/api/endpoints.ts
git commit -m "feat: Scheduled Operations page with recurring jobs management UI"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `recurring_jobs` table with all columns | Task 1 + 2 |
| `RecurringJobType` enum (backup/scheduled_restore/scheduled_op) | Task 1 |
| Alembic migration | Task 2 |
| `compute_next_run` cron helper | Task 4 |
| `register_job` / `deregister_job` / `fire_job_now` | Task 4 |
| `_fire_recurring_job` creates CR + auto-approves + executes | Task 4 |
| CRUD endpoints + enable/disable/run-now | Task 5 |
| APScheduler loaded at startup with all enabled jobs | Task 6 |
| `job_type` filter on list endpoint | Task 5 |
| Frontend `recurringJobsApi` | Task 7 |
| Scheduled Ops page: summary strip, table, row expansion | Task 8 |
| Create Job drawer with preset/custom cron | Task 8 |
| Run Now / Enable / Disable / Delete from row | Task 8 |
| Link to last CR from expanded row | Task 8 |

**Deferred (out of scope per spec):** retry on failure, job dependencies, per-execution approval override, failure notifications.
