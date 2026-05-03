# Composable Runbooks — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** User-defined, versioned, multi-step workflow templates that chain existing Nexplane change types with conditional branching, parallel execution, and human checkpoints. The runbook executor creates ordinary change requests for each step and reuses the full existing plan/approve/execute lifecycle.

---

## Background

Every change request in Nexplane today executes a fixed, predefined sequence of steps for its change type. There is no way for an operator to compose multiple change types into a reusable workflow, express conditional logic between steps, or pause execution for human review. Common multi-step operations — onboarding an engineer, responding to a compromised account, running a patch campaign — are instead documented in external runbooks (Confluence pages, Google Docs) and executed manually, step by step, with no audit trail tying the steps together.

Composable Runbooks moves this into the platform: operators define a `Runbook` once, execute it on demand or on schedule, and get a unified audit trail across every change request the runbook creates.

---

## Design Decisions

- **Reuse the existing change request lifecycle:** Each runbook step that performs an action creates a standard Nexplane change request (plan → approve → execute). The executor polls change request status to advance. No parallel execution engine is built from scratch.
- **Versioning on save:** Every edit to a runbook body (steps, parameters) auto-increments `version`. In-flight executions record `runbook_version` and are unaffected by later edits.
- **Condition expressions via restricted `eval()`:** Condition steps evaluate a string expression in a namespace containing only `steps` (a dict of step results) and `ctx` (the runtime context dict). No imports, no builtins beyond comparison operators.
- **Parallel groups via concurrent change request creation:** A `parallel_group` step creates all child change requests simultaneously, then waits for all to reach terminal status before advancing.
- **Human checkpoints pause, not block:** Execution status transitions to `waiting_human`. A background poller checks timeout; if exceeded, the configured `on_timeout` action fires automatically.
- **Seed templates stored as JSON fixtures:** Pre-built runbooks are loaded via a `seed_runbooks` Alembic migration. Organizations can fork (copy) a seed template and customize it.
- **APScheduler for the executor poller:** A single APScheduler job runs every 30 seconds, advances all `running` and `waiting_human` executions that have work to do. Individual step execution is async-safe.
- **No runbook scheduler for MVP:** Runbooks are triggered manually via API or UI. Scheduled triggers (cron) are deferred to a follow-on spec.

---

## Section 1: Data Models

### 1.1 `Runbook`

**New file:** `backend/app/models/runbook.py`

```python
class Runbook(Base):
    __tablename__ = "runbooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    is_seed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # True = shipped template
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    steps: Mapped[list["RunbookStep"]] = relationship(
        "RunbookStep", back_populates="runbook",
        order_by="RunbookStep.step_number",
        cascade="all, delete-orphan"
    )
    executions: Mapped[list["RunbookExecution"]] = relationship("RunbookExecution", back_populates="runbook")
```

### 1.2 `RunbookStep`

Stored flat in the DB. Parallel groups reference child steps via `parent_step_id`. Condition branches reference step numbers (resolved at execution time).

```python
class RunbookStep(Base):
    __tablename__ = "runbook_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    runbook_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runbooks.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("runbook_steps.id"), nullable=True)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-based, unique within runbook (or within parent for parallel children)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Discriminator
    type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # "change" | "condition" | "human_checkpoint" | "parallel_group"

    # type="change"
    change_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    asset_selector: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # asset_selector shape: {"tags": [...], "asset_ids": [...], "environment": "prod"}

    # type="condition"
    condition_expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    # e.g. "steps[1]['exit_code'] == 0"
    on_true_step: Mapped[int | None] = mapped_column(Integer, nullable=True)   # step_number to jump to
    on_false_step: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # type="human_checkpoint"
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_role: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "admin", "operator"
    timeout_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_timeout: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "abort" | "continue"

    # Failure handling (applies to "change" and "parallel_group")
    on_failure: Mapped[str] = mapped_column(String(32), nullable=False, default="abort")
    # "abort" | "continue" | "rollback_all"

    runbook: Mapped["Runbook"] = relationship("Runbook", back_populates="steps")
    children: Mapped[list["RunbookStep"]] = relationship(
        "RunbookStep", foreign_keys=[parent_step_id],
        order_by="RunbookStep.step_number"
    )
```

### 1.3 `RunbookExecution`

```python
class RunbookExecution(Base):
    __tablename__ = "runbook_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    runbook_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runbooks.id"), nullable=False, index=True)
    runbook_version: Mapped[int] = mapped_column(Integer, nullable=False)  # snapshot at trigger time
    runbook_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)  # full step list serialized at trigger time

    triggered_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # runtime variables supplied at trigger time; available to condition expressions as `ctx`

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="running"
    )  # "running" | "waiting_human" | "completed" | "failed" | "rolled_back"

    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    step_results: Mapped[list["RunbookStepResult"]] = relationship(
        "RunbookStepResult", back_populates="execution",
        order_by="RunbookStepResult.step_number"
    )
    runbook: Mapped["Runbook"] = relationship("Runbook", back_populates="executions")
```

### 1.4 `RunbookStepResult`

```python
class RunbookStepResult(Base):
    __tablename__ = "runbook_step_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runbook_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    # "pending" | "running" | "waiting_human" | "completed" | "failed" | "skipped"

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    change_request_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    # UUIDs of change requests created for this step (1 for "change", N for "parallel_group" children)

    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Freeform output accessible in condition expressions as steps[N]
    # For "change" steps: {"exit_code": 0, "output": "...", "change_request_id": "..."}
    # For "condition" steps: {"evaluated_to": true, "jumped_to_step": 3}
    # For "human_checkpoint": {"action": "resumed", "resumed_by": "...", "resumed_at": "..."}
    # For "parallel_group": {"child_results": [...]}

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    execution: Mapped["RunbookExecution"] = relationship("RunbookExecution", back_populates="step_results")
```

---

## Section 2: Alembic Migration

**New file:** `backend/alembic/versions/xxxx_add_runbooks.py`

```python
"""add runbooks

Revision ID: <generated>
Revises: <previous>
"""

def upgrade():
    op.create_table(
        "runbooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("tags", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("is_seed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_runbooks_organization_id", "runbooks", ["organization_id"])

    op.create_table(
        "runbook_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("runbook_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_step_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runbook_steps.id"), nullable=True),
        sa.Column("step_number", sa.Integer, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("change_type", sa.String(128), nullable=True),
        sa.Column("parameters", postgresql.JSONB, nullable=True),
        sa.Column("asset_selector", postgresql.JSONB, nullable=True),
        sa.Column("condition_expr", sa.Text, nullable=True),
        sa.Column("on_true_step", sa.Integer, nullable=True),
        sa.Column("on_false_step", sa.Integer, nullable=True),
        sa.Column("prompt", sa.Text, nullable=True),
        sa.Column("required_role", sa.String(64), nullable=True),
        sa.Column("timeout_hours", sa.Integer, nullable=True),
        sa.Column("on_timeout", sa.String(32), nullable=True),
        sa.Column("on_failure", sa.String(32), nullable=False, server_default="abort"),
    )
    op.create_index("ix_runbook_steps_runbook_id", "runbook_steps", ["runbook_id"])

    op.create_table(
        "runbook_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("runbook_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runbooks.id"), nullable=False),
        sa.Column("runbook_version", sa.Integer, nullable=False),
        sa.Column("runbook_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("context", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("current_step", sa.Integer, nullable=False, server_default="1"),
    )
    op.create_index("ix_runbook_executions_runbook_id", "runbook_executions", ["runbook_id"])
    op.create_index("ix_runbook_executions_status", "runbook_executions", ["status"])

    op.create_table(
        "runbook_step_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("runbook_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_number", sa.Integer, nullable=False),
        sa.Column("step_name", sa.String(255), nullable=False),
        sa.Column("step_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_request_ids", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("result", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_runbook_step_results_execution_id", "runbook_step_results", ["execution_id"])
```

---

## Section 3: Pydantic Schemas

**New file:** `backend/app/schemas/runbook.py`

```python
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from typing import Any

class AssetSelector(BaseModel):
    tags: list[str] = []
    asset_ids: list[UUID] = []
    environment: str | None = None

class RunbookStepCreate(BaseModel):
    step_number: int
    name: str
    type: str  # "change" | "condition" | "human_checkpoint" | "parallel_group"
    change_type: str | None = None
    parameters: dict[str, Any] | None = None
    asset_selector: AssetSelector | None = None
    condition_expr: str | None = None
    on_true_step: int | None = None
    on_false_step: int | None = None
    prompt: str | None = None
    required_role: str | None = None
    timeout_hours: int | None = None
    on_timeout: str | None = None  # "abort" | "continue"
    on_failure: str = "abort"
    parallel_steps: list["RunbookStepCreate"] = []  # children for parallel_group

RunbookStepCreate.model_rebuild()

class RunbookCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    tags: list[str] = []
    steps: list[RunbookStepCreate]

class RunbookUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    steps: list[RunbookStepCreate] | None = None

class RunbookStepOut(RunbookStepCreate):
    id: UUID
    runbook_id: UUID
    parallel_steps: list["RunbookStepOut"] = []

RunbookStepOut.model_rebuild()

class RunbookOut(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    description: str | None
    version: int
    tags: list[str]
    is_seed: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    steps: list[RunbookStepOut]

    model_config = {"from_attributes": True}

class TriggerRunbookRequest(BaseModel):
    context: dict[str, Any] = {}

class RunbookStepResultOut(BaseModel):
    id: UUID
    step_number: int
    step_name: str
    step_type: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    change_request_ids: list[str]
    result: dict[str, Any]
    error_message: str | None

    model_config = {"from_attributes": True}

class RunbookExecutionOut(BaseModel):
    id: UUID
    runbook_id: UUID
    runbook_version: int
    triggered_by: UUID
    triggered_at: datetime
    completed_at: datetime | None
    context: dict[str, Any]
    status: str
    current_step: int
    step_results: list[RunbookStepResultOut]

    model_config = {"from_attributes": True}

class HumanCheckpointResumeRequest(BaseModel):
    execution_id: UUID
    step_number: int
    action: str  # "resume" | "abort"
```

---

## Section 4: API Router

**New file:** `backend/app/routers/runbooks.py`

All endpoints require an authenticated session. Organization scoping is applied automatically via `current_user.organization_id`.

```
GET    /api/runbooks                          List runbooks for org (supports ?tag=, ?search=)
POST   /api/runbooks                          Create a new runbook
GET    /api/runbooks/{runbook_id}             Get runbook with steps
PUT    /api/runbooks/{runbook_id}             Update runbook (bumps version, validates steps)
DELETE /api/runbooks/{runbook_id}             Delete runbook (fails if active executions exist)
POST   /api/runbooks/{runbook_id}/fork        Fork a runbook (copy it, even a seed template)
POST   /api/runbooks/{runbook_id}/trigger     Trigger a new execution → returns RunbookExecutionOut
GET    /api/runbooks/{runbook_id}/executions  List executions for a runbook
GET    /api/executions/{execution_id}         Get execution detail with step results
POST   /api/executions/{execution_id}/resume  Resume a waiting_human step (operator action)
POST   /api/executions/{execution_id}/abort   Abort a running or waiting_human execution
```

Key behaviors:

**`PUT /api/runbooks/{runbook_id}`** — if `steps` is provided, delete all existing `RunbookStep` rows for this runbook and insert the new set in a single transaction. Increment `version` by 1.

**`POST /api/runbooks/{runbook_id}/trigger`** — serialize the current runbook + steps to `runbook_snapshot` (so the execution is self-contained), create a `RunbookExecution` with `status="running"`, `current_step=1`, and return immediately. The APScheduler poller picks it up within 30 seconds.

**`POST /api/executions/{execution_id}/resume`** — validate that `status == "waiting_human"` and the caller has the required role. Update the checkpoint `RunbookStepResult` with `{"action": "resumed", "resumed_by": ..., "resumed_at": ...}` and set `execution.status = "running"`. The poller advances on next tick.

**`POST /api/executions/{execution_id}/abort`** — set `status = "failed"`, mark current step result as `"failed"`, record `error_message = "Aborted by operator"`.

Full router skeleton:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.auth import get_current_user
from app.schemas.runbook import (
    RunbookCreate, RunbookUpdate, RunbookOut, RunbookExecutionOut,
    TriggerRunbookRequest, HumanCheckpointResumeRequest
)
from app.services.runbook_service import RunbookService

router = APIRouter(prefix="/api/runbooks", tags=["runbooks"])

@router.get("", response_model=list[RunbookOut])
async def list_runbooks(tag: str | None = None, search: str | None = None,
                        db: AsyncSession = Depends(get_db),
                        user=Depends(get_current_user)):
    return await RunbookService(db).list_runbooks(user.organization_id, tag=tag, search=search)

@router.post("", response_model=RunbookOut, status_code=201)
async def create_runbook(payload: RunbookCreate, db: AsyncSession = Depends(get_db),
                         user=Depends(get_current_user)):
    return await RunbookService(db).create_runbook(user.organization_id, user.id, payload)

@router.get("/{runbook_id}", response_model=RunbookOut)
async def get_runbook(runbook_id: str, db: AsyncSession = Depends(get_db),
                      user=Depends(get_current_user)):
    return await RunbookService(db).get_runbook(runbook_id, user.organization_id)

@router.put("/{runbook_id}", response_model=RunbookOut)
async def update_runbook(runbook_id: str, payload: RunbookUpdate,
                         db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    return await RunbookService(db).update_runbook(runbook_id, user.organization_id, payload)

@router.delete("/{runbook_id}", status_code=204)
async def delete_runbook(runbook_id: str, db: AsyncSession = Depends(get_db),
                         user=Depends(get_current_user)):
    await RunbookService(db).delete_runbook(runbook_id, user.organization_id)

@router.post("/{runbook_id}/fork", response_model=RunbookOut, status_code=201)
async def fork_runbook(runbook_id: str, db: AsyncSession = Depends(get_db),
                       user=Depends(get_current_user)):
    return await RunbookService(db).fork_runbook(runbook_id, user.organization_id, user.id)

@router.post("/{runbook_id}/trigger", response_model=RunbookExecutionOut, status_code=201)
async def trigger_runbook(runbook_id: str, payload: TriggerRunbookRequest,
                          db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    return await RunbookService(db).trigger_runbook(runbook_id, user.organization_id, user.id, payload.context)

@router.get("/{runbook_id}/executions", response_model=list[RunbookExecutionOut])
async def list_executions(runbook_id: str, db: AsyncSession = Depends(get_db),
                          user=Depends(get_current_user)):
    return await RunbookService(db).list_executions(runbook_id, user.organization_id)

# Execution-scoped routes live under /api/executions
execution_router = APIRouter(prefix="/api/executions", tags=["runbook-executions"])

@execution_router.get("/{execution_id}", response_model=RunbookExecutionOut)
async def get_execution(execution_id: str, db: AsyncSession = Depends(get_db),
                        user=Depends(get_current_user)):
    return await RunbookService(db).get_execution(execution_id, user.organization_id)

@execution_router.post("/{execution_id}/resume", response_model=RunbookExecutionOut)
async def resume_execution(execution_id: str, payload: HumanCheckpointResumeRequest,
                           db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    return await RunbookService(db).resume_checkpoint(execution_id, payload.step_number,
                                                       payload.action, user)

@execution_router.post("/{execution_id}/abort", response_model=RunbookExecutionOut)
async def abort_execution(execution_id: str, db: AsyncSession = Depends(get_db),
                          user=Depends(get_current_user)):
    return await RunbookService(db).abort_execution(execution_id, user.organization_id)
```

Register both routers in `backend/app/main.py`:
```python
from app.routers.runbooks import router as runbooks_router, execution_router
app.include_router(runbooks_router)
app.include_router(execution_router)
```

---

## Section 5: Runbook Service

**New file:** `backend/app/services/runbook_service.py`

Handles all DB operations: create, update (with step replacement), fork (deep copy), trigger (serialize snapshot, create execution), and checkpoint resolution.

Fork logic:
```python
async def fork_runbook(self, runbook_id, org_id, user_id) -> Runbook:
    source = await self.get_runbook(runbook_id, org_id)
    new_rb = Runbook(
        organization_id=org_id,
        name=f"{source.name} (copy)",
        description=source.description,
        tags=list(source.tags),
        version=1,
        is_seed=False,
        created_by=user_id,
    )
    self.db.add(new_rb)
    await self.db.flush()
    await self._copy_steps(source.steps, new_rb.id, parent_id=None)
    await self.db.commit()
    return new_rb
```

Trigger logic:
```python
async def trigger_runbook(self, runbook_id, org_id, user_id, context) -> RunbookExecution:
    rb = await self.get_runbook(runbook_id, org_id)
    snapshot = RunbookOut.model_validate(rb).model_dump(mode="json")
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
    return execution
```

---

## Section 6: Runbook Executor

**New file:** `backend/app/services/runbook_executor.py`

The executor is a pure function called by the APScheduler poller. It processes one execution tick: inspect the current step, perform the appropriate action, update status, advance `current_step` or set terminal status.

### 6.1 Poller Registration

In `backend/app/main.py`, after the scheduler is initialized:

```python
from app.services.runbook_executor import tick_all_executions

scheduler.add_job(
    tick_all_executions,
    trigger="interval",
    seconds=30,
    id="runbook_executor",
    replace_existing=True,
)
```

### 6.2 `tick_all_executions`

```python
async def tick_all_executions():
    async with get_db_session() as db:
        # Fetch all executions that need work
        executions = await db.execute(
            select(RunbookExecution).where(
                RunbookExecution.status.in_(["running", "waiting_human"])
            )
        )
        for execution in executions.scalars():
            try:
                await tick_execution(db, execution)
            except Exception as e:
                log.error(f"Executor error for execution {execution.id}: {e}")
        await db.commit()
```

### 6.3 `tick_execution`

```python
async def tick_execution(db: AsyncSession, execution: RunbookExecution):
    steps = execution.runbook_snapshot["steps"]
    step_map = {s["step_number"]: s for s in steps}

    current_step_def = step_map.get(execution.current_step)
    if current_step_def is None:
        # No more steps — execution complete
        execution.status = "completed"
        execution.completed_at = datetime.utcnow()
        return

    step_result = await get_or_create_step_result(db, execution, current_step_def)

    match current_step_def["type"]:
        case "change":
            await execute_change_step(db, execution, current_step_def, step_result)
        case "condition":
            await execute_condition_step(db, execution, current_step_def, step_result)
        case "human_checkpoint":
            await execute_checkpoint_step(db, execution, current_step_def, step_result)
        case "parallel_group":
            await execute_parallel_step(db, execution, current_step_def, step_result)
```

### 6.4 Change Step

```python
async def execute_change_step(db, execution, step_def, step_result):
    if step_result.status == "pending":
        # Create the change request
        cr = await create_change_request(
            db,
            organization_id=execution.runbook_snapshot["organization_id"],
            triggered_by=execution.triggered_by,
            change_type=step_def["change_type"],
            parameters={**step_def.get("parameters", {}), **execution.context},
            asset_selector=step_def.get("asset_selector"),
            source="runbook",
            runbook_execution_id=str(execution.id),
        )
        step_result.change_request_ids = [str(cr.id)]
        step_result.status = "running"
        step_result.started_at = datetime.utcnow()

    elif step_result.status == "running":
        # Poll the change request
        cr_id = step_result.change_request_ids[0]
        cr = await db.get(ChangeRequest, cr_id)

        if cr.status == "completed":
            step_result.status = "completed"
            step_result.completed_at = datetime.utcnow()
            step_result.result = {
                "exit_code": 0,
                "output": cr.result_summary,
                "change_request_id": cr_id,
            }
            execution.current_step += 1

        elif cr.status == "failed":
            step_result.status = "failed"
            step_result.error_message = cr.failure_reason
            step_result.result = {"exit_code": 1, "change_request_id": cr_id}
            await handle_step_failure(db, execution, step_def)
```

### 6.5 Condition Step

Condition expressions are evaluated in a tightly restricted namespace. Only `steps` (dict keyed by step number, values are `result` dicts) and `ctx` (the execution context) are available. `eval()` is called with `{"__builtins__": {}}` to strip all builtins.

```python
async def execute_condition_step(db, execution, step_def, step_result):
    if step_result.status != "pending":
        return

    steps_ns = {
        r.step_number: r.result
        for r in await get_step_results(db, execution.id)
    }
    namespace = {"steps": steps_ns, "ctx": execution.context, "__builtins__": {}}

    try:
        result = bool(eval(step_def["condition_expr"], namespace))  # noqa: S307
    except Exception as e:
        step_result.status = "failed"
        step_result.error_message = f"Condition eval error: {e}"
        await handle_step_failure(db, execution, step_def)
        return

    next_step = step_def["on_true_step"] if result else step_def["on_false_step"]
    step_result.status = "completed"
    step_result.completed_at = datetime.utcnow()
    step_result.result = {"evaluated_to": result, "jumped_to_step": next_step}
    execution.current_step = next_step
```

### 6.6 Human Checkpoint Step

```python
async def execute_checkpoint_step(db, execution, step_def, step_result):
    if step_result.status == "pending":
        step_result.status = "waiting_human"
        step_result.started_at = datetime.utcnow()
        execution.status = "waiting_human"
        await send_checkpoint_notification(execution, step_def)
        return

    if step_result.status == "waiting_human":
        # Check timeout
        timeout_hours = step_def.get("timeout_hours")
        if timeout_hours and step_result.started_at:
            elapsed = (datetime.utcnow() - step_result.started_at).total_seconds() / 3600
            if elapsed >= timeout_hours:
                on_timeout = step_def.get("on_timeout", "abort")
                if on_timeout == "abort":
                    step_result.status = "failed"
                    step_result.error_message = "Human checkpoint timed out"
                    execution.status = "failed"
                else:  # "continue"
                    step_result.status = "completed"
                    step_result.result = {"action": "timeout_continue"}
                    step_result.completed_at = datetime.utcnow()
                    execution.status = "running"
                    execution.current_step += 1
        # Otherwise: still waiting, nothing to do this tick

    # "completed" status is set by the resume endpoint, not the poller
```

### 6.7 Parallel Group Step

```python
async def execute_parallel_step(db, execution, step_def, step_result):
    child_steps = step_def.get("parallel_steps", [])

    if step_result.status == "pending":
        # Create all child change requests simultaneously
        cr_ids = []
        for child in child_steps:
            cr = await create_change_request(
                db, change_type=child["change_type"],
                parameters=child.get("parameters", {}),
                asset_selector=child.get("asset_selector"),
                source="runbook", runbook_execution_id=str(execution.id),
                organization_id=execution.runbook_snapshot["organization_id"],
                triggered_by=execution.triggered_by,
            )
            cr_ids.append(str(cr.id))
        step_result.change_request_ids = cr_ids
        step_result.status = "running"
        step_result.started_at = datetime.utcnow()
        return

    if step_result.status == "running":
        # Poll all child change requests
        all_done = True
        any_failed = False
        child_results = []
        for cr_id in step_result.change_request_ids:
            cr = await db.get(ChangeRequest, cr_id)
            if cr.status not in ("completed", "failed"):
                all_done = False
            if cr.status == "failed":
                any_failed = True
            child_results.append({"cr_id": cr_id, "status": cr.status})

        if not all_done:
            return

        step_result.result = {"child_results": child_results}
        step_result.completed_at = datetime.utcnow()

        if any_failed:
            step_result.status = "failed"
            await handle_step_failure(db, execution, step_def)
        else:
            step_result.status = "completed"
            execution.current_step += 1
```

### 6.8 Failure Handling

```python
async def handle_step_failure(db, execution, step_def):
    on_failure = step_def.get("on_failure", "abort")
    if on_failure == "abort":
        execution.status = "failed"
        execution.completed_at = datetime.utcnow()
    elif on_failure == "continue":
        execution.current_step += 1  # skip to next step
    elif on_failure == "rollback_all":
        await rollback_execution(db, execution)
```

`rollback_all` creates a new change request of type `"rollback"` for each completed step in reverse order, using the stored `change_request_ids`. Rollback change request support is outside the scope of this spec but the hook is wired.

---

## Section 7: Notifications

**New file:** `backend/app/services/runbook_notifications.py`

```python
async def send_checkpoint_notification(execution: RunbookExecution, step_def: dict):
    """Send email + in-app notification to users with the required role."""
    required_role = step_def.get("required_role", "operator")
    prompt = step_def.get("prompt", "Manual approval required.")
    # Reuse existing notification infrastructure (email via SMTP, in-app via notification table)
    # Subject: f"[Nexplane] Runbook checkpoint: {execution.runbook_snapshot['name']}"
    # Body: prompt + link to execution detail page
    ...
```

In-app notifications are written to an existing `notifications` table (or equivalent). The frontend already polls for notifications.

---

## Section 8: Seed Templates

**New file:** `backend/app/seeds/runbooks.json`

Three pre-built runbooks loaded by a separate Alembic seed migration (runs after schema migration, idempotent via `is_seed=True` guard):

### 8.1 Engineer Onboarding

```json
{
  "name": "Engineer Onboarding",
  "description": "Provision a new engineer: create AD account, assign Okta groups, add to GitHub org, send welcome email.",
  "tags": ["onboarding", "identity"],
  "steps": [
    {"step_number": 1, "name": "Create AD Account", "type": "change", "change_type": "create_ad_account", "on_failure": "abort"},
    {"step_number": 2, "name": "Assign Okta Groups", "type": "change", "change_type": "assign_okta_groups", "on_failure": "abort"},
    {"step_number": 3, "name": "Add to GitHub Org", "type": "change", "change_type": "add_github_org_member", "on_failure": "continue"},
    {"step_number": 4, "name": "Manager Approval", "type": "human_checkpoint",
     "prompt": "Confirm the above accounts were provisioned correctly and approve sending the welcome email.",
     "required_role": "manager", "timeout_hours": 48, "on_timeout": "abort"},
    {"step_number": 5, "name": "Send Welcome Email", "type": "change", "change_type": "send_welcome_email", "on_failure": "continue"}
  ]
}
```

### 8.2 Incident Response: Account Compromise

```json
{
  "name": "Incident Response: Account Compromise",
  "description": "Lock down a compromised account, preserve evidence, reset credentials, and notify security.",
  "tags": ["incident-response", "security"],
  "steps": [
    {"step_number": 1, "name": "Lockdown Account", "type": "change", "change_type": "lockdown_account", "on_failure": "abort"},
    {"step_number": 2, "name": "Preserve Evidence", "type": "change", "change_type": "preserve_cloudtrail_logs", "on_failure": "continue"},
    {"step_number": 3, "name": "Force Password Reset", "type": "change", "change_type": "force_password_reset", "on_failure": "abort"},
    {"step_number": 4, "name": "Notify Security Team", "type": "human_checkpoint",
     "prompt": "Review the locked account and preserved logs. Confirm notification has been sent to the security team.",
     "required_role": "security", "timeout_hours": 4, "on_timeout": "continue"},
    {"step_number": 5, "name": "Verify Lockdown", "type": "condition",
     "condition_expr": "steps[1]['exit_code'] == 0",
     "on_true_step": 6, "on_false_step": 99},
    {"step_number": 6, "name": "Close Incident", "type": "change", "change_type": "close_incident_ticket", "on_failure": "continue"}
  ]
}
```

### 8.3 Patch Campaign

```json
{
  "name": "Patch Campaign",
  "description": "Fleet health check, rolling patch, compliance verification.",
  "tags": ["patch", "compliance"],
  "steps": [
    {"step_number": 1, "name": "Fleet Health Check", "type": "change", "change_type": "check_fleet_health",
     "asset_selector": {"environment": "prod"}, "on_failure": "abort"},
    {"step_number": 2, "name": "Health Check Passed?", "type": "condition",
     "condition_expr": "steps[1]['exit_code'] == 0",
     "on_true_step": 3, "on_false_step": 99},
    {"step_number": 3, "name": "Operator Approval", "type": "human_checkpoint",
     "prompt": "Fleet health check passed. Approve rolling patch to prod?",
     "required_role": "operator", "timeout_hours": 24, "on_timeout": "abort"},
    {"step_number": 4, "name": "Rolling Patch", "type": "change", "change_type": "patch_packages",
     "asset_selector": {"environment": "prod"}, "on_failure": "abort"},
    {"step_number": 5, "name": "Verify Compliance", "type": "change", "change_type": "check_compliance",
     "asset_selector": {"environment": "prod"}, "on_failure": "continue"}
  ]
}
```

---

## Section 9: Frontend

### 9.1 Navigation

Add **Runbooks** to the main nav in `frontend/src/components/Sidebar.tsx` (or equivalent nav component), between "Changes" and "Settings".

### 9.2 Pages

**New file:** `frontend/src/pages/Runbooks.tsx` — Runbook list page

- Fetches `GET /api/runbooks` via `useQuery(["runbooks"])`.
- Shows a table: Name, Version, Tags, Last Executed, Status of last execution.
- "New Runbook" button → navigates to `/runbooks/new`.
- Row click → navigates to `/runbooks/:id`.
- Seed runbooks shown with a "Template" badge; have a "Fork" button instead of "Edit".

**New file:** `frontend/src/pages/RunbookEditor.tsx` — Create / edit page

- Loads runbook by ID (or blank for new).
- Step builder: ordered list of steps. Each step has a type selector and type-specific fields:
  - **change**: change_type dropdown (populated from `GET /api/change-types`), parameters key-value editor, asset selector (tags multiselect, environment dropdown).
  - **condition**: condition expression text field, on_true_step / on_false_step number inputs.
  - **human_checkpoint**: prompt textarea, required_role input, timeout_hours input, on_timeout toggle.
  - **parallel_group**: nested step list (same step builder, restricted to "change" type for MVP).
- Drag-to-reorder steps (react-beautiful-dnd or equivalent).
- "Save" → `POST /api/runbooks` (new) or `PUT /api/runbooks/:id` (edit).
- "Trigger" button → opens a modal to supply runtime `context` as key-value pairs → `POST /api/runbooks/:id/trigger` → redirects to execution detail.

**New file:** `frontend/src/pages/RunbookExecution.tsx` — Execution detail page

- Fetches `GET /api/executions/:id`, polls every 5 seconds while `status` is `"running"` or `"waiting_human"`.
- Shows execution metadata: runbook name, version, triggered by, started at, status badge.
- Step timeline: each step as a card showing name, type, status badge, duration, and a link to the created change request(s).
- **Human checkpoint card**: when `step_result.status == "waiting_human"`, shows the checkpoint prompt and two buttons — "Resume" and "Abort Execution". "Resume" calls `POST /api/executions/:id/resume`.
- Parallel group card: shows child change request links in a grid.
- Condition step card: shows the expression, which branch was taken.

### 9.3 API Hooks

**New file:** `frontend/src/hooks/useRunbooks.ts`

```typescript
export const useRunbooks = () =>
  useQuery({ queryKey: ["runbooks"], queryFn: () => api.get("/api/runbooks") });

export const useRunbook = (id: string) =>
  useQuery({ queryKey: ["runbook", id], queryFn: () => api.get(`/api/runbooks/${id}`) });

export const useRunbookExecution = (id: string, poll: boolean) =>
  useQuery({
    queryKey: ["runbook-execution", id],
    queryFn: () => api.get(`/api/executions/${id}`),
    refetchInterval: poll ? 5000 : false,
  });

export const useCreateRunbook = () =>
  useMutation({ mutationFn: (data) => api.post("/api/runbooks", data),
                onSuccess: () => queryClient.invalidateQueries(["runbooks"]) });

export const useTriggerRunbook = (id: string) =>
  useMutation({ mutationFn: (ctx) => api.post(`/api/runbooks/${id}/trigger`, { context: ctx }) });

export const useResumeCheckpoint = (executionId: string) =>
  useMutation({
    mutationFn: ({ step_number, action }: { step_number: number; action: string }) =>
      api.post(`/api/executions/${executionId}/resume`, { execution_id: executionId, step_number, action }),
    onSuccess: () => queryClient.invalidateQueries(["runbook-execution", executionId]),
  });
```

---

## Section 10: ChangeRequest Source Tracking

To link change requests back to the runbook execution that created them, add two nullable columns to the existing `change_requests` table:

```python
# New Alembic migration (separate from runbooks schema migration)
op.add_column("change_requests", sa.Column("source", sa.String(32), nullable=True))
# "manual" | "runbook"
op.add_column("change_requests", sa.Column("runbook_execution_id", postgresql.UUID(as_uuid=True), nullable=True))
```

The change request detail page in the frontend should show a "Created by Runbook" badge with a link to the execution when `source == "runbook"`.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/models/runbook.py` | New — `Runbook`, `RunbookStep`, `RunbookExecution`, `RunbookStepResult` models |
| `backend/app/schemas/runbook.py` | New — Pydantic schemas for all runbook types |
| `backend/app/routers/runbooks.py` | New — `runbooks` and `execution_router` FastAPI routers |
| `backend/app/services/runbook_service.py` | New — DB operations: create, update, fork, trigger, resume, abort |
| `backend/app/services/runbook_executor.py` | New — APScheduler tick function and per-step handlers |
| `backend/app/services/runbook_notifications.py` | New — checkpoint notification dispatch |
| `backend/app/seeds/runbooks.json` | New — three pre-built seed runbook templates |
| `backend/alembic/versions/xxxx_add_runbooks.py` | New — schema migration for all four runbook tables |
| `backend/alembic/versions/xxxx_cr_source_tracking.py` | New — adds `source` and `runbook_execution_id` to `change_requests` |
| `backend/app/main.py` | Register runbook routers; register `tick_all_executions` with APScheduler |
| `frontend/src/pages/Runbooks.tsx` | New — runbook list page |
| `frontend/src/pages/RunbookEditor.tsx` | New — create/edit page with step builder UI |
| `frontend/src/pages/RunbookExecution.tsx` | New — execution detail page with live polling and checkpoint UI |
| `frontend/src/hooks/useRunbooks.ts` | New — React Query hooks for all runbook API calls |
| `frontend/src/components/Sidebar.tsx` | Add Runbooks nav link |
| `frontend/src/App.tsx` | Add routes: `/runbooks`, `/runbooks/new`, `/runbooks/:id`, `/executions/:id` |
