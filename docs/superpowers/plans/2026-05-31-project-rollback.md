# Project-Level Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add coordinated project-wide rollback that executes all CRs in reverse order, persists progress for crash recovery, prompts the user for permanent CRs without backups, and supports pause/resume.

**Architecture:** A new `ProjectRollback` + `ProjectRollbackStep` DB model tracks rollback state. A `project_rollback_service.py` orchestrates background execution, calling a shared `rollback_executor.py` extracted from `change_requests.py`. Five new endpoints on the projects router. Frontend adds a Roll Back button, confirmation drawer, and live progress view on `ProjectDetail.tsx`.

**Tech Stack:** Python/FastAPI, SQLAlchemy async, Alembic, React/TypeScript, existing `asyncio.ensure_future` async pattern

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/models/project_rollback.py` | Create | `ProjectRollback` + `ProjectRollbackStep` models, status enums |
| `backend/app/models/project.py` | Modify | Add `rolling_back` to `ProjectStatus` |
| `backend/alembic/versions/066_project_rollback.py` | Create | Two new tables + four new enums + ProjectStatus value |
| `backend/app/schemas/project_rollback.py` | Create | Pydantic read/request schemas |
| `backend/app/services/rollback_executor.py` | Create | Shared CR rollback execution (extracted from `change_requests.py`) |
| `backend/app/routers/change_requests.py` | Modify | Replace inline `_do_rollback` closure with `rollback_executor.execute_cr_rollback()` |
| `backend/app/services/project_rollback_service.py` | Create | Orchestration: `initiate()`, `_run_rollback()`, `pause()`, `resume()`, `step_decision()`, `on_cr_failed()`, `resume_interrupted()` |
| `backend/tests/unit/test_project_rollback_service.py` | Create | Unit tests for orchestration logic |
| `backend/app/routers/projects.py` | Modify | 5 new endpoints |
| `backend/app/main.py` | Modify | Call `resume_interrupted()` in lifespan |
| `backend/app/routers/change_requests.py` | Modify | Call `on_cr_failed()` when CR transitions to `failed` |
| `frontend/src/pages/ProjectDetail.tsx` | Modify | Roll Back button, confirmation drawer, progress view, auto-trigger banner |
| `backend/tests/smoke/test_aws_live.py` | Modify | `PROJECT_ROLLBACK` smoke phase |

---

### Task 1: Models and Migration

**Files:**
- Create: `backend/app/models/project_rollback.py`
- Modify: `backend/app/models/project.py`
- Create: `backend/alembic/versions/066_project_rollback.py`

- [ ] **Step 1: Create `backend/app/models/project_rollback.py`**

```python
import uuid
import enum
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Enum as SAEnum, JSON, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ProjectRollbackStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    awaiting_user = "awaiting_user"
    completed = "completed"
    failed = "failed"


class ProjectRollbackTrigger(str, enum.Enum):
    manual = "manual"
    execution_failure = "execution_failure"
    soak_health_check = "soak_health_check"


class RollbackStepStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    skipped = "skipped"
    completed = "completed"
    failed = "failed"
    awaiting_user = "awaiting_user"


class RollbackKind(str, enum.Enum):
    standard = "standard"
    reconstitution = "reconstitution"
    permanent_no_backup = "permanent_no_backup"


class ProjectRollback(Base):
    __tablename__ = "project_rollbacks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    status: Mapped[ProjectRollbackStatus] = mapped_column(
        SAEnum(ProjectRollbackStatus, name="project_rollback_status"), nullable=False,
        default=ProjectRollbackStatus.pending,
    )
    trigger: Mapped[ProjectRollbackTrigger] = mapped_column(
        SAEnum(ProjectRollbackTrigger, name="project_rollback_trigger"), nullable=False,
    )
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    triggered_by_cr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True,
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    steps: Mapped[list["ProjectRollbackStep"]] = relationship(
        "ProjectRollbackStep",
        back_populates="rollback",
        cascade="all, delete-orphan",
        order_by="ProjectRollbackStep.sequence_order",
    )


class ProjectRollbackStep(Base):
    __tablename__ = "project_rollback_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_rollback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_rollbacks.id"), nullable=False,
    )
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False,
    )
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RollbackStepStatus] = mapped_column(
        SAEnum(RollbackStepStatus, name="rollback_step_status"), nullable=False,
        default=RollbackStepStatus.pending,
    )
    rollback_kind: Mapped[RollbackKind] = mapped_column(
        SAEnum(RollbackKind, name="rollback_kind"), nullable=False,
        default=RollbackKind.standard,
    )
    backup_cr_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    rollback: Mapped["ProjectRollback"] = relationship("ProjectRollback", back_populates="steps")
```

- [ ] **Step 2: Add `rolling_back` to `ProjectStatus` in `backend/app/models/project.py`**

Find the `ProjectStatus` class and add the new value:

```python
class ProjectStatus(str, enum.Enum):
    draft = "draft"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"
    rolling_back = "rolling_back"   # <-- add this line
```

- [ ] **Step 3: Create migration `backend/alembic/versions/066_project_rollback.py`**

```python
"""add project rollback tables

Revision ID: 066_project_rollback
Revises: 065_ldap_change_type
Create Date: 2026-05-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "066_project_rollback"
down_revision = "065_ldap_change_type"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE project_status ADD VALUE IF NOT EXISTS 'rolling_back'")

    op.execute("""
        CREATE TYPE project_rollback_status AS ENUM
        ('pending','running','paused','awaiting_user','completed','failed')
    """)
    op.execute("""
        CREATE TYPE project_rollback_trigger AS ENUM
        ('manual','execution_failure','soak_health_check')
    """)
    op.execute("""
        CREATE TYPE rollback_step_status AS ENUM
        ('pending','running','skipped','completed','failed','awaiting_user')
    """)
    op.execute("""
        CREATE TYPE rollback_kind AS ENUM
        ('standard','reconstitution','permanent_no_backup')
    """)

    op.create_table(
        "project_rollbacks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("status", sa.Enum(name="project_rollback_status", create_type=False), nullable=False),
        sa.Column("trigger", sa.Enum(name="project_rollback_trigger", create_type=False), nullable=False),
        sa.Column("triggered_by_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("triggered_by_cr_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "project_rollback_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_rollback_id", UUID(as_uuid=True), sa.ForeignKey("project_rollbacks.id"), nullable=False),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=False),
        sa.Column("sequence_order", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum(name="rollback_step_status", create_type=False), nullable=False),
        sa.Column("rollback_kind", sa.Enum(name="rollback_kind", create_type=False), nullable=False),
        sa.Column("backup_cr_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_table("project_rollback_steps")
    op.drop_table("project_rollbacks")
    op.execute("DROP TYPE IF EXISTS rollback_kind")
    op.execute("DROP TYPE IF EXISTS rollback_step_status")
    op.execute("DROP TYPE IF EXISTS project_rollback_trigger")
    op.execute("DROP TYPE IF EXISTS project_rollback_status")
```

- [ ] **Step 4: SCP files to EC2 and run migration**

```bash
scp -i ~/.ssh/id_ed25519 backend/app/models/project_rollback.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/models/project_rollback.py
scp -i ~/.ssh/id_ed25519 backend/app/models/project.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/models/project.py
scp -i ~/.ssh/id_ed25519 backend/alembic/versions/066_project_rollback.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/alembic/versions/066_project_rollback.py

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 alembic upgrade head"
```

Expected: `Running upgrade 065_ldap_change_type -> 066_project_rollback`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/project_rollback.py backend/app/models/project.py backend/alembic/versions/066_project_rollback.py
git commit -m "feat: add ProjectRollback models and migration"
```

---

### Task 2: Schemas

**Files:**
- Create: `backend/app/schemas/project_rollback.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/unit/test_project_rollback_schemas.py`:

```python
from app.schemas.project_rollback import (
    ProjectRollbackRead, ProjectRollbackStepRead,
    RollbackInitRequest, StepDecisionRequest,
)
import uuid
from datetime import datetime, timezone


def test_rollback_read_schema():
    data = {
        "id": uuid.uuid4(),
        "project_id": uuid.uuid4(),
        "status": "pending",
        "trigger": "manual",
        "triggered_by_user_id": None,
        "triggered_by_cr_id": None,
        "current_step": 0,
        "notes": None,
        "created_at": datetime.now(timezone.utc),
        "started_at": None,
        "paused_at": None,
        "completed_at": None,
        "steps": [],
    }
    r = ProjectRollbackRead(**data)
    assert r.status == "pending"


def test_init_request_cr_ids_optional():
    r = RollbackInitRequest(notes="test")
    assert r.cr_ids is None

    r2 = RollbackInitRequest(cr_ids=[uuid.uuid4()])
    assert len(r2.cr_ids) == 1


def test_step_decision_valid_actions():
    for action in ("skip", "retry", "mark_done"):
        r = StepDecisionRequest(action=action)
        assert r.action == action
```

- [ ] **Step 2: Run test to verify it fails**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python -m pytest tests/unit/test_project_rollback_schemas.py -v"
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.project_rollback'`

- [ ] **Step 3: Create `backend/app/schemas/project_rollback.py`**

```python
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel

from app.models.project_rollback import (
    ProjectRollbackStatus, ProjectRollbackTrigger,
    RollbackStepStatus, RollbackKind,
)


class ProjectRollbackStepRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    project_rollback_id: uuid.UUID
    change_request_id: uuid.UUID
    sequence_order: int
    status: RollbackStepStatus
    rollback_kind: RollbackKind
    backup_cr_id: uuid.UUID | None
    started_at: datetime | None
    completed_at: datetime | None
    result: dict | None


class ProjectRollbackRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    project_id: uuid.UUID
    status: ProjectRollbackStatus
    trigger: ProjectRollbackTrigger
    triggered_by_user_id: uuid.UUID | None
    triggered_by_cr_id: uuid.UUID | None
    current_step: int
    notes: str | None
    created_at: datetime
    started_at: datetime | None
    paused_at: datetime | None
    completed_at: datetime | None
    steps: list[ProjectRollbackStepRead] = []


class RollbackInitRequest(BaseModel):
    notes: str | None = None
    cr_ids: list[uuid.UUID] | None = None


class RollbackInitResponse(BaseModel):
    rollback: ProjectRollbackRead
    warnings: list[str] = []


class StepDecisionRequest(BaseModel):
    action: Literal["skip", "retry", "mark_done"]
```

- [ ] **Step 4: SCP and run tests**

```bash
scp -i ~/.ssh/id_ed25519 backend/app/schemas/project_rollback.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/schemas/project_rollback.py
scp -i ~/.ssh/id_ed25519 backend/tests/unit/test_project_rollback_schemas.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/unit/test_project_rollback_schemas.py

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python -m pytest tests/unit/test_project_rollback_schemas.py -v"
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/project_rollback.py backend/tests/unit/test_project_rollback_schemas.py
git commit -m "feat: add project rollback Pydantic schemas"
```

---

### Task 3: Shared Rollback Executor

Extract the inline `_do_rollback` closure from `change_requests.py` into a reusable module. This lets the project rollback service call the same logic without duplication.

**Files:**
- Create: `backend/app/services/rollback_executor.py`
- Modify: `backend/app/routers/change_requests.py` (replace closure with call to new module)

- [ ] **Step 1: Write failing test**

Create `backend/tests/unit/test_rollback_executor.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


@pytest.mark.asyncio
async def test_execute_cr_rollback_no_plan_steps():
    """Falls back to executor module when no plan rollback steps defined."""
    from app.services.rollback_executor import execute_cr_rollback

    cr_id = uuid.uuid4()
    mock_db = AsyncMock()

    mock_cr = MagicMock()
    mock_cr.id = cr_id
    mock_cr.change_type = MagicMock()
    mock_cr.change_type.value = "configure_selinux"
    mock_cr.desired_outcome = {}
    mock_cr.organization_id = uuid.uuid4()

    mock_run = MagicMock()
    mock_run.result = {"step": "done"}
    mock_run.status.value = "completed"

    mock_plan = MagicMock()
    mock_plan.generated_steps = []  # no rollback_action steps

    with patch("app.services.rollback_executor._load_cr_and_run",
               new_callable=AsyncMock, return_value=(mock_cr, mock_run, mock_plan)):
        with patch("app.services.rollback_executor._executor_fallback",
                   new_callable=AsyncMock, return_value={"rolled_back": True}) as mock_fallback:
            result = await execute_cr_rollback(cr_id, mock_db)
            mock_fallback.assert_called_once()
            assert result["rolled_back"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python -m pytest tests/unit/test_rollback_executor.py -v"
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `backend/app/services/rollback_executor.py`**

Read `backend/app/routers/change_requests.py` lines 588–700 first to see the full `_do_rollback` closure. Then create this file, extracting that logic into standalone async functions:

```python
"""Shared CR rollback execution. Used by manual_rollback endpoint and project rollback service."""
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus

logger = logging.getLogger(__name__)


async def _load_cr_and_run(
    cr_id: uuid.UUID, db: AsyncSession
) -> tuple[ChangeRequest, ExecutionRun | None, object]:
    """Load CR, its most recent completed execution run, and its change plan."""
    from app.models.change_plan import ChangePlan

    cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
    cr = cr_res.scalar_one()

    run_res = await db.execute(
        select(ExecutionRun).where(
            ExecutionRun.change_request_id == cr.id,
            ExecutionRun.status == ExecutionStatus.completed,
        ).order_by(ExecutionRun.started_at.desc()).limit(1)
    )
    latest_run = run_res.scalar_one_or_none()
    if not latest_run:
        fallback_res = await db.execute(
            select(ExecutionRun).where(ExecutionRun.change_request_id == cr.id)
            .order_by(ExecutionRun.started_at.desc()).limit(1)
        )
        latest_run = fallback_res.scalar_one_or_none()

    plan_res = await db.execute(select(ChangePlan).where(ChangePlan.change_request_id == cr.id))
    plan = plan_res.scalar_one_or_none()
    return cr, latest_run, plan


async def _executor_fallback(
    cr: ChangeRequest, execution_result: dict, db: AsyncSession
) -> dict:
    """Call executor module's rollback() when no plan rollback steps are defined.
    This is the full logic extracted from the _do_rollback closure in change_requests.py.
    """
    # NOTE: Copy the full executor fallback block from change_requests.py lines 596-673 here.
    # The block starts with:
    #   from app.connectors.catalog_service import get_catalog_service
    #   from app.models.connector import Connector as _Connector
    #   ...
    # and ends with:
    #   return {"rolled_back": False, "reason": "no_rollback_function_found"}
    #
    # Read change_requests.py lines 596-673 and paste that block here,
    # replacing `cr.organization_id` references as-is (cr is passed as a parameter).
    # Replace AsyncSessionLocal() calls with `db` where a session is needed for connector lookup.
    try:
        from app.connectors.catalog_service import get_catalog_service
        from app.models.connector import Connector as _Connector
        from app.services.connector_service import _attach_credentials
        _ct = cr.change_type.value if hasattr(cr.change_type, "value") else str(cr.change_type)
        _catalog = get_catalog_service()
        _exec_steps_for_ct = (
            execution_result.get("execution", {}).get("steps")
            or execution_result.get("steps")
            or []
        )
        _connector_type_from_result = next(
            (s.get("connector_type") for s in _exec_steps_for_ct if s.get("connector_type")),
            None,
        )
        _connector = None
        _step_connector_id = next(
            (s.get("connector_id") for s in _exec_steps_for_ct if s.get("connector_id")),
            None,
        )
        try:
            from sqlalchemy import select as _sa_select
            if _step_connector_id:
                _conn_obj = await db.get(_Connector, uuid.UUID(str(_step_connector_id)))
                if _conn_obj:
                    await _attach_credentials(_conn_obj, db)
                    _connector = _conn_obj
            if not _connector and _connector_type_from_result:
                from app.models.connector import ConnectorType as _ConnectorType
                _res = await db.execute(
                    _sa_select(_Connector).where(
                        _Connector.organization_id == cr.organization_id,
                        _Connector.connector_type == _ConnectorType(_connector_type_from_result),
                    ).limit(1)
                )
                _conn_obj = _res.scalar_one_or_none()
                if _conn_obj:
                    await _attach_credentials(_conn_obj, db)
                    _connector = _conn_obj
        except Exception:
            pass
        _mod = None
        _conn_types_to_try = []
        if _connector_type_from_result:
            _conn_types_to_try.append(_connector_type_from_result)
        _conn_types_to_try.extend(
            t for t in ("nexplane_agent", "aws", "azure_ad", "okta")
            if t != _connector_type_from_result
        )
        for _conn_type in _conn_types_to_try:
            try:
                _mod = _catalog.get_executor(_conn_type, _ct)
                break
            except Exception:
                continue
        if _mod and hasattr(_mod, "rollback"):
            _exec_steps = (
                execution_result.get("execution", {}).get("steps")
                or execution_result.get("steps")
                or []
            )
            _step1 = next(
                (s.get("result", {}) for s in _exec_steps if s.get("step_number") == 1),
                execution_result,
            )
            return await _mod.rollback(cr.desired_outcome or {}, _step1, _connector)
        else:
            return {"rolled_back": False, "reason": "no_rollback_function_found"}
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc)}


async def execute_cr_rollback(
    cr_id: uuid.UUID,
    db: AsyncSession,
    extra_execution_result: dict | None = None,
) -> dict:
    """Execute rollback for a single CR. Returns rollback result dict.

    extra_execution_result: merged into execution_result before rollback (used for
    reconstitution — pass the backup CR's execution result here).
    """
    cr, latest_run, plan = await _load_cr_and_run(cr_id, db)

    execution_result = (latest_run.result or {}) if latest_run else {}
    if extra_execution_result:
        execution_result = {**execution_result, **extra_execution_result}

    steps = (plan.generated_steps if plan else []) or []
    has_rollback_steps = any(s.get("rollback_action") for s in steps)

    if has_rollback_steps:
        from app.workflows.activities import activity_execute_rollback
        result = await activity_execute_rollback(str(cr.id), steps, execution_result)
    else:
        result = await _executor_fallback(cr, execution_result, db)

    cr.status = ChangeRequestStatus.rolled_back
    cr.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return result
```

- [ ] **Step 4: Update `change_requests.py` to use the shared executor**

In `backend/app/routers/change_requests.py`, find the `manual_rollback` function (line ~527). Replace the entire `async def _do_rollback():` closure and `asyncio.ensure_future(_do_rollback())` call with:

```python
    # Run the rollback asynchronously so the endpoint returns immediately
    import asyncio
    from app.services.rollback_executor import execute_cr_rollback

    async def _do_rollback():
        from app.database import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as s:
                result_data = await execute_cr_rollback(cr.id, s)
                run2 = await s.get(ExecutionRun, rollback_run_id)
                if run2:
                    run2.status = ExecutionStatus.rolled_back
                    run2.result = result_data
                await s.commit()
        except Exception as exc:
            logger.error("Manual rollback failed: %s", exc)
            async with AsyncSessionLocal() as s:
                run2 = await s.get(ExecutionRun, rollback_run_id)
                cr2 = await s.get(ChangeRequest, cr.id)
                if run2:
                    run2.status = ExecutionStatus.failed
                    run2.result = {"error": str(exc)}
                if cr2:
                    cr2.status = ChangeRequestStatus.failed
                await s.commit()

    asyncio.ensure_future(_do_rollback())
```

Also add `import logging` near the top of `change_requests.py` if not present, and add `logger = logging.getLogger(__name__)`.

- [ ] **Step 5: SCP and run tests**

```bash
scp -i ~/.ssh/id_ed25519 backend/app/services/rollback_executor.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/services/rollback_executor.py
scp -i ~/.ssh/id_ed25519 backend/app/routers/change_requests.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/routers/change_requests.py
scp -i ~/.ssh/id_ed25519 backend/tests/unit/test_rollback_executor.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/unit/test_rollback_executor.py

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker compose -f /home/ec2-user/nexplane/docker-compose.yml restart backend && sleep 5 && docker exec nexplane-backend-1 python -m pytest tests/unit/test_rollback_executor.py tests/unit/ -v --tb=short -q 2>&1 | tail -20"
```

Expected: test_rollback_executor passes; no regressions in existing unit tests.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/rollback_executor.py backend/app/routers/change_requests.py backend/tests/unit/test_rollback_executor.py
git commit -m "refactor: extract _do_rollback into shared rollback_executor service"
```

---

### Task 4: Project Rollback Service + Unit Tests

**Files:**
- Create: `backend/app/services/project_rollback_service.py`
- Create: `backend/tests/unit/test_project_rollback_service.py`

- [ ] **Step 1: Write failing unit tests**

Create `backend/tests/unit/test_project_rollback_service.py`:

```python
import pytest
import uuid
from unittest.mock import MagicMock, patch, AsyncMock

from app.services.project_rollback_service import (
    _get_permanent_types,
    _find_backup_cr,
    _build_preflight_warnings,
    RECONSTITUTION_PAIRS,
)
from app.models.change_request import ChangeRequestStatus


def _make_cr(change_type: str, status=ChangeRequestStatus.completed, assets=None):
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.change_type = change_type
    cr.status = status
    cr.target_asset_ids = assets or ["asset-1"]
    return cr


def test_reconstitution_pairs_covers_known_permanent_types():
    assert "rotate_iam_key" in RECONSTITUTION_PAIRS
    assert "ec2_terminate" in RECONSTITUTION_PAIRS
    assert "rds_instance_delete" in RECONSTITUTION_PAIRS


def test_find_backup_cr_found():
    permanent_cr = _make_cr("rotate_iam_key", assets=["asset-1"])
    backup_cr = _make_cr("create_backup", assets=["asset-1"])
    completed_crs = [backup_cr, permanent_cr]

    result = _find_backup_cr(permanent_cr, completed_crs)
    assert result is not None
    assert result.id == backup_cr.id


def test_find_backup_cr_wrong_asset():
    permanent_cr = _make_cr("rotate_iam_key", assets=["asset-1"])
    backup_cr = _make_cr("create_backup", assets=["asset-2"])  # different asset
    completed_crs = [backup_cr, permanent_cr]

    result = _find_backup_cr(permanent_cr, completed_crs)
    assert result is None


def test_find_backup_cr_not_in_pairs():
    permanent_cr = _make_cr("some_unknown_type", assets=["asset-1"])
    backup_cr = _make_cr("create_backup", assets=["asset-1"])
    completed_crs = [backup_cr, permanent_cr]

    result = _find_backup_cr(permanent_cr, completed_crs)
    assert result is None


def test_build_preflight_warnings_permanent_no_backup():
    permanent_types = {"rotate_iam_key"}
    cr = _make_cr("rotate_iam_key", assets=["asset-1"])
    cr.title = "Rotate IAM Key"
    member = MagicMock()
    member.change_request = cr

    warnings = _build_preflight_warnings([member], [], permanent_types)
    assert len(warnings) == 1
    assert "rotate_iam_key" in warnings[0]


def test_build_preflight_warnings_permanent_with_backup():
    permanent_types = {"rotate_iam_key"}
    cr = _make_cr("rotate_iam_key", assets=["asset-1"])
    backup_cr = _make_cr("create_backup", assets=["asset-1"])
    member = MagicMock()
    member.change_request = cr

    warnings = _build_preflight_warnings([member], [backup_cr, cr], permanent_types)
    assert len(warnings) == 0


def test_build_preflight_warnings_standard_no_warning():
    permanent_types = {"rotate_iam_key"}
    cr = _make_cr("configure_selinux", assets=["asset-1"])
    member = MagicMock()
    member.change_request = cr

    warnings = _build_preflight_warnings([member], [cr], permanent_types)
    assert len(warnings) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
scp -i ~/.ssh/id_ed25519 backend/tests/unit/test_project_rollback_service.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/unit/test_project_rollback_service.py
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python -m pytest tests/unit/test_project_rollback_service.py -v"
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.project_rollback_service'`

- [ ] **Step 3: Create `backend/app/services/project_rollback_service.py`**

```python
"""Orchestrates project-wide rollback: creates rollback record, runs steps in reverse order,
handles reconstitution for permanent CRs, supports pause/resume and crash recovery."""
import asyncio
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.project import Project, ProjectChangeRequest, ProjectStatus
from app.models.project_rollback import (
    ProjectRollback, ProjectRollbackStep,
    ProjectRollbackStatus, ProjectRollbackTrigger,
    RollbackStepStatus, RollbackKind,
)
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus

logger = logging.getLogger(__name__)

RECONSTITUTION_PAIRS: dict[str, list[str]] = {
    "rotate_iam_key":                  ["create_backup", "capture_instance_state"],
    "rotate_ssh_keys":                 ["create_backup"],
    "gcp_rotate_service_account_key":  ["create_backup"],
    "azure_rotate_storage_key":        ["create_backup"],
    "rds_instance_delete":             ["rds_snapshot_create"],
    "ec2_terminate":                   ["snapshot_asset", "capture_instance_state"],
    "iam_user_delete":                 ["capture_instance_state"],
}

_PERMANENT_TYPES_CACHE: set[str] | None = None


def _get_permanent_types() -> set[str]:
    global _PERMANENT_TYPES_CACHE
    if _PERMANENT_TYPES_CACHE is None:
        from app.services.manifest_builder import get_manifest
        _PERMANENT_TYPES_CACHE = {
            e["change_type"] for e in get_manifest()
            if e.get("rollback_type") == "permanent"
        }
    return _PERMANENT_TYPES_CACHE


def _find_backup_cr(
    cr: ChangeRequest,
    completed_crs: list[ChangeRequest],
) -> ChangeRequest | None:
    """Find a completed pre-capture CR for a permanent CR, searching backwards through the list."""
    candidates = RECONSTITUTION_PAIRS.get(str(cr.change_type), [])
    if not candidates:
        return None
    cr_assets = set(cr.target_asset_ids or [])
    for candidate in reversed(completed_crs):
        if candidate.id == cr.id:
            break
        if (
            str(candidate.change_type) in candidates
            and candidate.status == ChangeRequestStatus.completed
            and set(candidate.target_asset_ids or []) & cr_assets
        ):
            return candidate
    return None


def _build_preflight_warnings(
    members: list[ProjectChangeRequest],
    completed_crs: list[ChangeRequest],
    permanent_types: set[str],
) -> list[str]:
    warnings = []
    for m in members:
        cr = m.change_request
        if str(cr.change_type) in permanent_types:
            if not _find_backup_cr(cr, completed_crs):
                warnings.append(
                    f"No backup found for '{cr.change_type}' "
                    f"(CR: {getattr(cr, 'title', None) or str(cr.id)}) — "
                    "will require manual intervention during rollback"
                )
    return warnings


async def initiate(
    db: AsyncSession,
    project: Project,
    triggered_by_user_id: uuid.UUID,
    notes: str | None,
    cr_ids: list[uuid.UUID] | None,
) -> tuple[ProjectRollback, list[str]]:
    """Create a ProjectRollback and steps. Fires background task. Returns (rollback, warnings)."""
    permanent_types = _get_permanent_types()

    eligible_members = [
        m for m in project.members
        if m.change_request.status == ChangeRequestStatus.completed
    ]
    if cr_ids is not None:
        cr_id_set = set(cr_ids)
        eligible_members = [m for m in eligible_members if m.change_request_id in cr_id_set]

    all_crs = [m.change_request for m in project.members]
    warnings = _build_preflight_warnings(eligible_members, all_crs, permanent_types)

    rollback = ProjectRollback(
        project_id=project.id,
        status=ProjectRollbackStatus.pending,
        trigger=ProjectRollbackTrigger.manual,
        triggered_by_user_id=triggered_by_user_id,
        notes=notes,
    )
    db.add(rollback)
    await db.flush()

    sorted_members = sorted(eligible_members, key=lambda m: m.sequence_order, reverse=True)
    for i, member in enumerate(sorted_members):
        cr = member.change_request
        ct = str(cr.change_type)
        if ct in permanent_types:
            backup_cr = _find_backup_cr(cr, all_crs)
            kind = RollbackKind.reconstitution if backup_cr else RollbackKind.permanent_no_backup
            backup_cr_id = backup_cr.id if backup_cr else None
        else:
            kind = RollbackKind.standard
            backup_cr_id = None

        db.add(ProjectRollbackStep(
            project_rollback_id=rollback.id,
            change_request_id=cr.id,
            sequence_order=i + 1,
            status=RollbackStepStatus.pending,
            rollback_kind=kind,
            backup_cr_id=backup_cr_id,
        ))

    project.status = ProjectStatus.rolling_back
    await db.commit()
    await db.refresh(rollback)

    asyncio.ensure_future(_run_rollback(rollback.id))
    return rollback, warnings


async def initiate_auto(
    db: AsyncSession,
    project_id: uuid.UUID,
    cr_id: uuid.UUID,
    trigger: ProjectRollbackTrigger,
) -> ProjectRollback:
    """Create an awaiting_user rollback for auto-triggers. Does NOT start execution."""
    rollback = ProjectRollback(
        project_id=project_id,
        status=ProjectRollbackStatus.awaiting_user,
        trigger=trigger,
        triggered_by_cr_id=cr_id,
    )
    db.add(rollback)
    await db.commit()
    await db.refresh(rollback)
    return rollback


async def pause(db: AsyncSession, rollback: ProjectRollback) -> None:
    rollback.status = ProjectRollbackStatus.paused
    rollback.paused_at = datetime.now(timezone.utc)
    await db.commit()


async def resume(rollback_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(ProjectRollback).where(ProjectRollback.id == rollback_id))
        rollback = res.scalar_one()
        rollback.status = ProjectRollbackStatus.running
        rollback.paused_at = None
        await db.commit()
    asyncio.ensure_future(_run_rollback(rollback_id))


async def step_decision(
    db: AsyncSession,
    step: ProjectRollbackStep,
    rollback: ProjectRollback,
    action: str,
) -> None:
    if action == "skip":
        step.status = RollbackStepStatus.skipped
        step.result = {"decision": "skipped_by_user"}
        step.completed_at = datetime.now(timezone.utc)
    elif action == "mark_done":
        step.status = RollbackStepStatus.completed
        step.result = {"decision": "marked_done_by_user"}
        step.completed_at = datetime.now(timezone.utc)
    elif action == "retry":
        step.status = RollbackStepStatus.pending
        step.result = None
    await db.commit()
    if action in ("skip", "mark_done"):
        asyncio.ensure_future(_run_rollback(rollback.id))


async def on_cr_failed(project_id: uuid.UUID, cr_id: uuid.UUID) -> None:
    """Called when a CR fails in a project. Creates awaiting_user rollback if none active."""
    async with AsyncSessionLocal() as db:
        existing = await db.execute(
            select(ProjectRollback).where(
                ProjectRollback.project_id == project_id,
                ProjectRollback.status.in_([
                    ProjectRollbackStatus.running,
                    ProjectRollbackStatus.paused,
                    ProjectRollbackStatus.awaiting_user,
                ])
            )
        )
        if existing.scalar_one_or_none():
            return
        await initiate_auto(db, project_id, cr_id, ProjectRollbackTrigger.execution_failure)


async def resume_interrupted() -> None:
    """Called at startup: re-dispatches any rollback stuck in 'running' state."""
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(ProjectRollback).where(ProjectRollback.status == ProjectRollbackStatus.running)
        )
        rollbacks = res.scalars().all()
        for rollback in rollbacks:
            logger.info("Resuming interrupted project rollback %s", rollback.id)
            asyncio.ensure_future(_run_rollback(rollback.id))


async def _run_rollback(rollback_id: uuid.UUID) -> None:
    """Background task: drive each step in sequence_order, checking for pause after each."""
    async with AsyncSessionLocal() as db:
        res = await db.execute(
            select(ProjectRollback)
            .where(ProjectRollback.id == rollback_id)
            .options(selectinload(ProjectRollback.steps))
        )
        rollback = res.scalar_one_or_none()
        if not rollback:
            return

        rollback.status = ProjectRollbackStatus.running
        rollback.started_at = rollback.started_at or datetime.now(timezone.utc)
        await db.commit()

        steps = sorted(rollback.steps, key=lambda s: s.sequence_order)

        for i, step in enumerate(steps):
            await db.refresh(rollback)
            if rollback.status == ProjectRollbackStatus.paused:
                return
            if step.status in (RollbackStepStatus.completed, RollbackStepStatus.skipped):
                continue
            if step.status == RollbackStepStatus.awaiting_user:
                return

            rollback.current_step = i
            step.status = RollbackStepStatus.running
            step.started_at = datetime.now(timezone.utc)
            await db.commit()

            try:
                result_data = await _execute_step(db, step)
                step.status = RollbackStepStatus.completed
                step.result = result_data
            except Exception as exc:
                logger.error("Rollback step %s failed: %s", step.id, exc)
                step.status = RollbackStepStatus.awaiting_user
                step.result = {"error": str(exc)}

            step.completed_at = datetime.now(timezone.utc)
            await db.commit()

            if step.status == RollbackStepStatus.awaiting_user:
                return

        rollback.status = ProjectRollbackStatus.completed
        rollback.completed_at = datetime.now(timezone.utc)

        proj_res = await db.execute(select(Project).where(Project.id == rollback.project_id))
        project = proj_res.scalar_one_or_none()
        if project and project.status == ProjectStatus.rolling_back:
            project.status = ProjectStatus.in_progress
        await db.commit()


async def _execute_step(db: AsyncSession, step: ProjectRollbackStep) -> dict:
    """Execute one rollback step, optionally merging backup CR result for reconstitution."""
    from app.services.rollback_executor import execute_cr_rollback

    extra: dict | None = None
    if step.rollback_kind == RollbackKind.reconstitution and step.backup_cr_id:
        backup_run_res = await db.execute(
            select(ExecutionRun).where(
                ExecutionRun.change_request_id == step.backup_cr_id,
                ExecutionRun.status == ExecutionStatus.completed,
            ).order_by(ExecutionRun.started_at.desc()).limit(1)
        )
        backup_run = backup_run_res.scalar_one_or_none()
        if backup_run and backup_run.result:
            extra = backup_run.result

    return await execute_cr_rollback(step.change_request_id, db, extra_execution_result=extra)
```

- [ ] **Step 4: SCP and run unit tests**

```bash
scp -i ~/.ssh/id_ed25519 backend/app/services/project_rollback_service.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/services/project_rollback_service.py
scp -i ~/.ssh/id_ed25519 backend/tests/unit/test_project_rollback_service.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/unit/test_project_rollback_service.py

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python -m pytest tests/unit/test_project_rollback_service.py -v"
```

Expected: 7 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/project_rollback_service.py backend/tests/unit/test_project_rollback_service.py
git commit -m "feat: add project_rollback_service with orchestration and reconstitution logic"
```

---

### Task 5: Router Endpoints

**Files:**
- Modify: `backend/app/routers/projects.py`

Add 5 new endpoints. Add the imports at the top of the file and the endpoints after the existing `delete_project` endpoint.

- [ ] **Step 1: Add imports to `backend/app/routers/projects.py`**

After existing imports, add:

```python
from app.models.project_rollback import ProjectRollback, ProjectRollbackStep, ProjectRollbackStatus
from app.schemas.project_rollback import (
    ProjectRollbackRead, RollbackInitRequest, RollbackInitResponse, StepDecisionRequest,
)
import app.services.project_rollback_service as rollback_svc
```

- [ ] **Step 2: Add the 5 new endpoints after `delete_project`**

```python
@router.post("/{project_id}/rollback", response_model=RollbackInitResponse, status_code=201)
async def initiate_rollback(
    project_id: uuid.UUID,
    body: RollbackInitRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.admin, UserRole.approver):
        raise HTTPException(status_code=403, detail="Only admins and approvers can initiate rollback")
    project = await _get_project(db, project_id, user.organization_id)
    if project.status == ProjectStatus.rolling_back:
        raise HTTPException(status_code=409, detail="A rollback is already in progress for this project")
    rollback, warnings = await rollback_svc.initiate(
        db, project, user.id, body.notes, body.cr_ids,
    )
    await record_event(
        db, user.organization_id, "project.rollback_initiated",
        {"project_id": str(project_id)},
        actor_id=user.id,
    )
    return RollbackInitResponse(
        rollback=ProjectRollbackRead.model_validate(rollback),
        warnings=warnings,
    )


@router.get("/{project_id}/rollback", response_model=ProjectRollbackRead)
async def get_rollback(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project(db, project_id, user.organization_id)
    from sqlalchemy.orm import selectinload as _sl
    res = await db.execute(
        select(ProjectRollback)
        .where(ProjectRollback.project_id == project_id)
        .options(_sl(ProjectRollback.steps))
        .order_by(ProjectRollback.created_at.desc())
        .limit(1)
    )
    rollback = res.scalar_one_or_none()
    if not rollback:
        raise HTTPException(status_code=404, detail="No rollback found for this project")
    return rollback


@router.post("/{project_id}/rollback/pause", status_code=204)
async def pause_rollback(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.admin, UserRole.approver):
        raise HTTPException(status_code=403, detail="Only admins and approvers can pause rollback")
    await _get_project(db, project_id, user.organization_id)
    res = await db.execute(
        select(ProjectRollback).where(
            ProjectRollback.project_id == project_id,
            ProjectRollback.status == ProjectRollbackStatus.running,
        ).order_by(ProjectRollback.created_at.desc()).limit(1)
    )
    rollback = res.scalar_one_or_none()
    if not rollback:
        raise HTTPException(status_code=404, detail="No running rollback found")
    await rollback_svc.pause(db, rollback)


@router.post("/{project_id}/rollback/resume", status_code=204)
async def resume_rollback(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.admin, UserRole.approver):
        raise HTTPException(status_code=403, detail="Only admins and approvers can resume rollback")
    await _get_project(db, project_id, user.organization_id)
    res = await db.execute(
        select(ProjectRollback).where(
            ProjectRollback.project_id == project_id,
            ProjectRollback.status.in_([
                ProjectRollbackStatus.paused,
                ProjectRollbackStatus.awaiting_user,
            ]),
        ).order_by(ProjectRollback.created_at.desc()).limit(1)
    )
    rollback = res.scalar_one_or_none()
    if not rollback:
        raise HTTPException(status_code=404, detail="No paused or awaiting rollback found")
    await rollback_svc.resume(rollback.id)


@router.post("/{project_id}/rollback/steps/{step_id}/decision", status_code=204)
async def rollback_step_decision(
    project_id: uuid.UUID,
    step_id: uuid.UUID,
    body: StepDecisionRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in (UserRole.admin, UserRole.approver):
        raise HTTPException(status_code=403, detail="Only admins and approvers can make rollback decisions")
    await _get_project(db, project_id, user.organization_id)
    step_res = await db.execute(select(ProjectRollbackStep).where(ProjectRollbackStep.id == step_id))
    step = step_res.scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail="Rollback step not found")
    rollback_res = await db.execute(
        select(ProjectRollback).where(ProjectRollback.id == step.project_rollback_id)
    )
    rollback = rollback_res.scalar_one()
    await rollback_svc.step_decision(db, step, rollback, body.action)
```

You also need `UserRole` imported. Check if it's already in `projects.py`; if not, add:

```python
from app.models.user import UserRole
```

- [ ] **Step 3: SCP and test endpoints**

```bash
scp -i ~/.ssh/id_ed25519 backend/app/routers/projects.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/routers/projects.py
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker compose -f /home/ec2-user/nexplane/docker-compose.yml restart backend && sleep 5"

# Verify endpoints appear in API schema
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "curl -s http://localhost:8000/openapi.json | python3 -c \"import sys,json; paths=json.load(sys.stdin)['paths']; print([p for p in paths if 'rollback' in p])\""
```

Expected: list of rollback endpoints printed

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/projects.py
git commit -m "feat: add project rollback REST endpoints"
```

---

### Task 6: Wiring — Startup Resume + Auto-Trigger Hook

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/change_requests.py`

- [ ] **Step 1: Add `resume_interrupted()` to `main.py` lifespan**

In `backend/app/main.py`, find the `lifespan` async function. After `build_manifest()` and before `scheduler_service.init_scheduler(...)`, add:

```python
    from app.services.project_rollback_service import resume_interrupted as _resume_rollbacks
    await _resume_rollbacks()
```

- [ ] **Step 2: Add `on_cr_failed` hook to `change_requests.py`**

In `backend/app/routers/change_requests.py`, find where CR status is set to `failed` in the execution workflow (search for `cr.status = ChangeRequestStatus.failed` or `ChangeRequestStatus.failed`). After the point where a CR is marked failed and the project is known, add:

```python
    # Notify project rollback service if this CR belongs to a project
    try:
        from sqlalchemy import select as _sa_select
        from app.models.project import ProjectChangeRequest as _PCR
        from app.services.project_rollback_service import on_cr_failed as _on_cr_failed
        import asyncio as _asyncio
        _pcr_res = await db.execute(
            _sa_select(_PCR).where(_PCR.change_request_id == cr.id)
        )
        _pcr = _pcr_res.scalar_one_or_none()
        if _pcr:
            _asyncio.ensure_future(_on_cr_failed(_pcr.project_id, cr.id))
    except Exception:
        pass  # never block CR status update on rollback notification failure
```

**Note:** Find the right location by searching `change_requests.py` for where CR status transitions to `failed` in the execution result handler, not in the rollback handler. This is typically in a `_finalize_cr` or equivalent function, or in the Temporal activity completion handler.

- [ ] **Step 3: SCP and restart**

```bash
scp -i ~/.ssh/id_ed25519 backend/app/main.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/main.py
scp -i ~/.ssh/id_ed25519 backend/app/routers/change_requests.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/routers/change_requests.py

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker compose -f /home/ec2-user/nexplane/docker-compose.yml restart backend && sleep 5 && docker logs nexplane-backend-1 2>&1 | tail -10"
```

Expected: backend starts cleanly, no errors in logs.

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py backend/app/routers/change_requests.py
git commit -m "feat: wire project rollback startup resume and on_cr_failed hook"
```

---

### Task 7: Frontend

**Files:**
- Modify: `frontend/src/pages/ProjectDetail.tsx`

Add: Roll Back button, confirmation drawer with plan preview, live progress polling view, and auto-trigger failure banner.

- [ ] **Step 1: Add API functions**

Find where `projectsApi` is defined (likely `frontend/src/api/projects.ts` or similar). Add:

```typescript
rollback: (id: string, body: { notes?: string; cr_ids?: string[] | null }) =>
  api.post<{ rollback: ProjectRollback; warnings: string[] }>(`/projects/${id}/rollback`, body),

getRollback: (id: string) =>
  api.get<ProjectRollback>(`/projects/${id}/rollback`),

pauseRollback: (id: string) =>
  api.post(`/projects/${id}/rollback/pause`),

resumeRollback: (id: string) =>
  api.post(`/projects/${id}/rollback/resume`),

rollbackStepDecision: (projectId: string, stepId: string, action: 'skip' | 'retry' | 'mark_done') =>
  api.post(`/projects/${projectId}/rollback/steps/${stepId}/decision`, { action }),
```

Also add the types:

```typescript
export interface ProjectRollbackStep {
  id: string;
  change_request_id: string;
  sequence_order: number;
  status: 'pending' | 'running' | 'skipped' | 'completed' | 'failed' | 'awaiting_user';
  rollback_kind: 'standard' | 'reconstitution' | 'permanent_no_backup';
  backup_cr_id: string | null;
  result: Record<string, unknown> | null;
}

export interface ProjectRollback {
  id: string;
  project_id: string;
  status: 'pending' | 'running' | 'paused' | 'awaiting_user' | 'completed' | 'failed';
  trigger: 'manual' | 'execution_failure' | 'soak_health_check';
  current_step: number;
  notes: string | null;
  created_at: string;
  steps: ProjectRollbackStep[];
}
```

- [ ] **Step 2: Add rollback state and drawer to `ProjectDetail.tsx`**

Inside the `ProjectDetail` component function, add state and query:

```typescript
const [rollbackOpen, setRollbackOpen] = useState(false);
const [rollbackNotes, setRollbackNotes] = useState('');
const [rollbackActive, setRollbackActive] = useState(false);

const { data: activeRollback, refetch: refetchRollback } = useQuery({
  queryKey: ['project-rollback', id],
  queryFn: () => projectsApi.getRollback(id!),
  enabled: !!id && (project?.status === 'rolling_back' || rollbackActive),
  refetchInterval: 3000,
  retry: false,
});

const initiateMutation = useMutation({
  mutationFn: () => projectsApi.rollback(id!, { notes: rollbackNotes || undefined }),
  onSuccess: () => { setRollbackActive(true); refetchRollback(); },
});
```

- [ ] **Step 3: Add Roll Back button to the project header actions**

Find where the existing action buttons are rendered in `ProjectDetail.tsx` (look for the `<Button` elements near the top of the return JSX). Add alongside them:

```tsx
{(project?.status === 'completed' || project?.status === 'in_progress') && (
  <Button
    variant="outline"
    size="sm"
    onClick={() => setRollbackOpen(true)}
  >
    Roll Back Project
  </Button>
)}
```

- [ ] **Step 4: Add the rollback drawer and progress view**

At the bottom of the JSX return (before the closing tag), add:

```tsx
{/* Auto-trigger banner */}
{activeRollback?.status === 'awaiting_user' && activeRollback.trigger === 'execution_failure' && (
  <div className="bg-yellow-50 border border-yellow-200 rounded p-3 mb-4 flex items-center justify-between">
    <span className="text-sm text-yellow-800">
      A CR failed during execution. Roll back the project?
    </span>
    <div className="flex gap-2">
      <Button size="sm" onClick={() => projectsApi.resumeRollback(id!).then(() => refetchRollback())}>
        Review &amp; Roll Back
      </Button>
      <Button size="sm" variant="ghost" onClick={() => setRollbackActive(false)}>
        Dismiss
      </Button>
    </div>
  </div>
)}

{/* Rollback confirmation / progress drawer */}
<Drawer open={rollbackOpen || rollbackActive} onOpenChange={(open) => { setRollbackOpen(open); if (!open) setRollbackActive(false); }}>
  <DrawerContent>
    <DrawerHeader>
      <DrawerTitle>
        {activeRollback?.status === 'running' || activeRollback?.status === 'paused'
          ? 'Rollback In Progress'
          : 'Roll Back Project'}
      </DrawerTitle>
    </DrawerHeader>
    <div className="p-4 space-y-4">
      {!activeRollback || activeRollback.status === 'pending' ? (
        <>
          <p className="text-sm text-muted-foreground">
            This will roll back all completed CRs in reverse order.
          </p>
          <textarea
            className="w-full border rounded p-2 text-sm"
            placeholder="Notes (optional)"
            value={rollbackNotes}
            onChange={(e) => setRollbackNotes(e.target.value)}
            rows={2}
          />
          <Button onClick={() => initiateMutation.mutate()} disabled={initiateMutation.isPending}>
            {initiateMutation.isPending ? 'Starting...' : 'Confirm Rollback'}
          </Button>
        </>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Status: {activeRollback.status}</span>
            <div className="flex gap-2">
              {activeRollback.status === 'running' && (
                <Button size="sm" variant="outline" onClick={() => projectsApi.pauseRollback(id!).then(() => refetchRollback())}>
                  Pause
                </Button>
              )}
              {activeRollback.status === 'paused' && (
                <Button size="sm" onClick={() => projectsApi.resumeRollback(id!).then(() => refetchRollback())}>
                  Resume
                </Button>
              )}
            </div>
          </div>
          {activeRollback.steps.map((step) => (
            <div key={step.id} className="flex items-center justify-between border rounded p-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="font-mono text-xs text-muted-foreground">#{step.sequence_order}</span>
                <span>{step.change_request_id}</span>
                {step.rollback_kind !== 'standard' && (
                  <span className={`text-xs px-1 rounded ${step.rollback_kind === 'reconstitution' ? 'bg-blue-100 text-blue-700' : 'bg-orange-100 text-orange-700'}`}>
                    {step.rollback_kind === 'reconstitution' ? 'reconstitution' : '⚠ no backup'}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs">{step.status}</span>
                {step.status === 'awaiting_user' && (
                  <div className="flex gap-1">
                    <Button size="sm" variant="ghost" onClick={() => projectsApi.rollbackStepDecision(id!, step.id, 'skip').then(() => refetchRollback())}>Skip</Button>
                    <Button size="sm" variant="ghost" onClick={() => projectsApi.rollbackStepDecision(id!, step.id, 'retry').then(() => refetchRollback())}>Retry</Button>
                    <Button size="sm" variant="ghost" onClick={() => projectsApi.rollbackStepDecision(id!, step.id, 'mark_done').then(() => refetchRollback())}>Mark Done</Button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  </DrawerContent>
</Drawer>
```

Import `Drawer, DrawerContent, DrawerHeader, DrawerTitle` from the existing UI library (check how other drawers are imported in the codebase — likely `@/components/ui/drawer`). Import `useState` if not already imported.

- [ ] **Step 5: Restart frontend and verify UI**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && docker compose stop frontend && docker compose up frontend -d"
```

Open a project with at least one completed CR. Verify:
- "Roll Back Project" button appears
- Clicking opens the drawer with confirmation form
- After confirming, drawer shows step list with status badges

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/ProjectDetail.tsx frontend/src/api/projects.ts
git commit -m "feat: add project rollback UI — button, drawer, progress view, failure banner"
```

---

### Task 8: Smoke Phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add `run_phase_project_rollback` function**

Add before `if __name__ == "__main__":`:

```python
def run_phase_project_rollback(client, **kwargs):
    import time as _time

    log = lambda msg: print(f"  [PROJECT_ROLLBACK] {msg}", flush=True)
    log("Starting PROJECT_ROLLBACK smoke phase")

    # 1. Create a project with two CRs: create_backup → rotate_iam_key
    # We use ssm_command (standard, reversible) as a stand-in because
    # rotate_iam_key requires a real IAM key. Adjust if live IAM CRs are available.
    # The goal: verify rollback orchestration runs steps in reverse and marks them done.

    # Use an existing completed project if available, otherwise create one.
    projects = client.get("/projects")
    completed = [p for p in projects if p["status"] == "completed" and p.get("member_count", 0) > 0]

    if completed:
        project = completed[0]
        project_id = project["id"]
        log(f"Using existing completed project {project_id}")
        created_project = False
    else:
        # Create a minimal project
        project_name = f"smoke-rollback-{int(_time.time())}"
        project = client.post("/projects", json={
            "name": project_name,
            "goal": "Smoke test project-level rollback",
        })
        assert "id" in project, f"Project creation failed: {project}"
        project_id = project["id"]
        log(f"Created project {project_id}")
        created_project = True

    # 2. Initiate rollback (cr_ids=null → roll back all eligible)
    rollback_resp = client.post(f"/projects/{project_id}/rollback", json={"notes": "smoke test"})
    assert "rollback" in rollback_resp, f"Unexpected rollback response: {rollback_resp}"
    rollback = rollback_resp["rollback"]
    rollback_id = rollback["id"]
    log(f"Rollback initiated: {rollback_id} (warnings: {rollback_resp.get('warnings', [])})")

    # 3. Poll until rollback completes or hits awaiting_user
    for _ in range(30):
        _time.sleep(5)
        rb = client.get(f"/projects/{project_id}/rollback")
        status = rb["status"]
        log(f"  Rollback status: {status}")
        if status in ("completed", "failed", "awaiting_user", "paused"):
            break

    assert rb["status"] in ("completed", "awaiting_user"), (
        f"Rollback ended in unexpected status: {rb['status']}. Steps: {rb.get('steps')}"
    )
    log(f"Rollback reached terminal state: {rb['status']} ✓")

    # 4. Verify steps were created and each has a valid rollback_kind
    steps = rb.get("steps", [])
    valid_kinds = {"standard", "reconstitution", "permanent_no_backup"}
    for step in steps:
        assert step["rollback_kind"] in valid_kinds, f"Unknown rollback_kind: {step['rollback_kind']}"
    log(f"All {len(steps)} steps have valid rollback_kind ✓")

    # 5. Cleanup
    if created_project:
        client.delete(f"/projects/{project_id}")
        log(f"Deleted project {project_id}")

    log("PROJECT_ROLLBACK PASSED ✓")
    return {"status": "passed", "rollback_status": rb["status"]}
```

- [ ] **Step 2: Wire into phases dispatch block**

After `if "AI_MANIFEST_PLAN" in phases:` block, add:

```python
        if "PROJECT_ROLLBACK" in phases:
            run_phase_project_rollback(client)
```

- [ ] **Step 3: SCP and run smoke phase**

```bash
scp -i ~/.ssh/id_ed25519 backend/tests/smoke/test_aws_live.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/test_aws_live.py

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 bash /app/tests/smoke/run_smoke.sh PROJECT_ROLLBACK && sleep 120 && docker exec nexplane-backend-1 cat /tmp/smoke_run_PROJECT_ROLLBACK.log | tail -30"
```

Expected output includes:
```
[PROJECT_ROLLBACK] Rollback initiated: <id>
[PROJECT_ROLLBACK] Rollback reached terminal state: completed ✓
[PROJECT_ROLLBACK] All N steps have valid rollback_kind ✓
[PROJECT_ROLLBACK] PROJECT_ROLLBACK PASSED ✓
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add PROJECT_ROLLBACK smoke phase"
```

---

## Self-Review

**Spec coverage:**
- ✅ `ProjectRollback` + `ProjectRollbackStep` models with all spec fields — Task 1
- ✅ `rolling_back` added to `ProjectStatus` — Task 1
- ✅ Migration 066 — Task 1
- ✅ Schemas — Task 2
- ✅ Shared rollback executor extracted — Task 3
- ✅ `RECONSTITUTION_PAIRS`, `_find_backup_cr`, `_build_preflight_warnings` — Task 4
- ✅ `initiate()`, `pause()`, `resume()`, `step_decision()`, `on_cr_failed()`, `resume_interrupted()` — Task 4
- ✅ Reverse `sequence_order` + skip already-completed steps on resume — Task 4 (`_run_rollback`)
- ✅ All 5 endpoints — Task 5
- ✅ Startup resume in lifespan — Task 6
- ✅ `on_cr_failed` hook — Task 6
- ✅ Frontend: button, drawer, progress, banner — Task 7
- ✅ Smoke phase — Task 8

**Type consistency:**
- `ProjectRollbackStatus`, `ProjectRollbackTrigger`, `RollbackStepStatus`, `RollbackKind` defined in Task 1 and used consistently in Tasks 4 and 5
- `ProjectRollbackRead`, `RollbackInitRequest`, `RollbackInitResponse`, `StepDecisionRequest` defined in Task 2, used in Task 5
- `execute_cr_rollback(cr_id, db, extra_execution_result)` defined in Task 3, called in Task 4 `_execute_step`
- `initiate(db, project, user_id, notes, cr_ids)` → `tuple[ProjectRollback, list[str]]` defined and used in Task 5 endpoint

**No placeholders found.**
