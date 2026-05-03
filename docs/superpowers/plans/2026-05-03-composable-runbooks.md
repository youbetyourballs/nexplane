# Composable Runbooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Composable Runbooks end-to-end: DB models + migration, Pydantic schemas, CRUD/trigger/execution APIs, RunbookService + RunbookExecutor (APScheduler-driven), seed templates, and frontend list/editor/execution pages.

**Architecture:** Runbooks are versioned, org-scoped templates of steps. Each step is one of: `change` (creates a standard ChangeRequest), `condition` (restricted `eval()`), `human_checkpoint` (pauses execution), or `parallel_group` (concurrent ChangeRequests). A `RunbookExecution` stores a full snapshot of the runbook at trigger time and is advanced by a 30-second APScheduler tick. The executor reuses the existing ChangeRequest lifecycle — no custom execution engine.

**Tech Stack:** FastAPI + SQLAlchemy async + PostgreSQL (JSONB, ARRAY), Alembic migrations, APScheduler (already wired in `scheduler_service.py`), React 18 + TanStack Query, lucide-react, react-beautiful-dnd.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/models/runbook.py` | Create | `Runbook`, `RunbookStep`, `RunbookExecution`, `RunbookStepResult` ORM models |
| `backend/alembic/versions/016_add_runbooks.py` | Create | Schema migration: 4 runbook tables + `source`/`runbook_execution_id` on `change_requests` |
| `backend/app/schemas/runbook.py` | Create | All Pydantic schemas |
| `backend/app/routers/runbooks.py` | Create | CRUD + fork + trigger + execution endpoints |
| `backend/app/services/runbook_service.py` | Create | DB operations: create, update, fork, trigger, resume, abort |
| `backend/app/services/runbook_executor.py` | Create | APScheduler tick: per-step handlers for all step types |
| `backend/app/seed/runbook_templates.py` | Create | 3 seed template dicts |
| `backend/app/tests/test_runbooks.py` | Create | Runbook CRUD + trigger API tests |
| `backend/app/tests/test_runbook_executions.py` | Create | Execution list/get/resume/abort tests |
| `backend/app/main.py` | Modify | Register routers; register `tick_all_executions` with APScheduler |
| `frontend/src/pages/Runbooks.tsx` | Create | Runbook list with fork/trigger |
| `frontend/src/pages/RunbookEditor.tsx` | Create | Step builder: create/edit |
| `frontend/src/pages/RunbookExecution.tsx` | Create | Execution detail: live poll + checkpoint UI |
| `frontend/src/hooks/useRunbooks.ts` | Create | React Query hooks for all runbook API calls |
| `frontend/src/components/Sidebar.tsx` | Modify | Add Runbooks nav link |
| `frontend/src/routes/index.tsx` | Modify | Add `/runbooks`, `/runbooks/new`, `/runbooks/:id`, `/executions/:id` routes |

---

## Task 1: DB Models

**Files:**
- Create: `backend/app/models/runbook.py`

- [ ] **Step 1: Write the models**

Create `backend/app/models/runbook.py`:

```python
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Runbook(Base):
    __tablename__ = "runbooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tags: Mapped[list] = mapped_column(ARRAY(String), nullable=False, default=list)
    is_seed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    steps: Mapped[list["RunbookStep"]] = relationship(
        "RunbookStep",
        back_populates="runbook",
        order_by="RunbookStep.step_number",
        cascade="all, delete-orphan",
        foreign_keys="RunbookStep.runbook_id",
    )
    executions: Mapped[list["RunbookExecution"]] = relationship("RunbookExecution", back_populates="runbook")


class RunbookStep(Base):
    __tablename__ = "runbook_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    runbook_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runbooks.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("runbook_steps.id"), nullable=True)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # "change" | "condition" | "human_checkpoint" | "parallel_group"
    type: Mapped[str] = mapped_column(String(32), nullable=False)

    # type="change"
    change_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parameters: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    asset_selector: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # type="condition"
    condition_expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    on_true_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_false_step: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # type="human_checkpoint"
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timeout_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_timeout: Mapped[str | None] = mapped_column(String(32), nullable=True)  # "abort" | "continue"

    # Failure handling for "change" and "parallel_group"
    on_failure: Mapped[str] = mapped_column(String(32), nullable=False, default="abort")

    runbook: Mapped["Runbook"] = relationship("Runbook", back_populates="steps", foreign_keys=[runbook_id])
    children: Mapped[list["RunbookStep"]] = relationship(
        "RunbookStep",
        foreign_keys=[parent_step_id],
        order_by="RunbookStep.step_number",
    )


class RunbookExecution(Base):
    __tablename__ = "runbook_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    runbook_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runbooks.id"), nullable=False, index=True)
    runbook_version: Mapped[int] = mapped_column(Integer, nullable=False)
    runbook_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    triggered_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # "running" | "waiting_human" | "completed" | "failed" | "rolled_back"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    step_results: Mapped[list["RunbookStepResult"]] = relationship(
        "RunbookStepResult",
        back_populates="execution",
        order_by="RunbookStepResult.step_number",
    )
    runbook: Mapped["Runbook"] = relationship("Runbook", back_populates="executions")


class RunbookStepResult(Base):
    __tablename__ = "runbook_step_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("runbook_executions.id", ondelete="CASCADE"), nullable=False, index=True)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)

    # "pending" | "running" | "waiting_human" | "completed" | "failed" | "skipped"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    change_request_ids: Mapped[list] = mapped_column(ARRAY(String), nullable=False, default=list)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    execution: Mapped["RunbookExecution"] = relationship("RunbookExecution", back_populates="step_results")
```

- [ ] **Step 2: Register models in `backend/app/models/__init__.py`**

Add to the imports/exports so Alembic autogenerate can see them:

```python
from app.models.runbook import Runbook, RunbookStep, RunbookExecution, RunbookStepResult  # noqa: F401
```

- [ ] **Step 3: Verify import**

```bash
cd /app && python -c "from app.models.runbook import Runbook, RunbookStep, RunbookExecution, RunbookStepResult; print('OK')"
```
Expected: `OK`

---

## Task 2: Alembic Migration

**Files:**
- Create: `backend/alembic/versions/016_add_runbooks.py`

Highest existing version is `015`. This migration covers all four runbook tables and the two new columns on `change_requests`.

- [ ] **Step 1: Write the migration**

Create `backend/alembic/versions/016_add_runbooks.py`:

```python
"""add runbooks

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runbooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("tags", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("is_seed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_runbooks_organization_id", "runbooks", ["organization_id"])

    op.create_table(
        "runbook_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("runbook_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbooks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_step_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbook_steps.id"), nullable=True),
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
        sa.Column("runbook_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbooks.id"), nullable=False),
        sa.Column("runbook_version", sa.Integer, nullable=False),
        sa.Column("runbook_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
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
        sa.Column("execution_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("runbook_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_number", sa.Integer, nullable=False),
        sa.Column("step_name", sa.String(255), nullable=False),
        sa.Column("step_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_request_ids", postgresql.ARRAY(sa.String), nullable=False, server_default="{}"),
        sa.Column("result", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_runbook_step_results_execution_id", "runbook_step_results", ["execution_id"])

    # Source tracking on existing change_requests table
    op.add_column("change_requests", sa.Column("source", sa.String(32), nullable=True))
    op.add_column("change_requests", sa.Column(
        "runbook_execution_id", postgresql.UUID(as_uuid=True), nullable=True
    ))


def downgrade():
    op.drop_column("change_requests", "runbook_execution_id")
    op.drop_column("change_requests", "source")
    op.drop_index("ix_runbook_step_results_execution_id", table_name="runbook_step_results")
    op.drop_table("runbook_step_results")
    op.drop_index("ix_runbook_executions_status", table_name="runbook_executions")
    op.drop_index("ix_runbook_executions_runbook_id", table_name="runbook_executions")
    op.drop_table("runbook_executions")
    op.drop_index("ix_runbook_steps_runbook_id", table_name="runbook_steps")
    op.drop_table("runbook_steps")
    op.drop_index("ix_runbooks_organization_id", table_name="runbooks")
    op.drop_table("runbooks")
```

- [ ] **Step 2: Run migration**

```bash
cd /app && alembic upgrade head
```
Expected: migration runs without error, all tables visible in psql.

- [ ] **Step 3: Verify tables**

```bash
cd /app && python -c "
import asyncio
from app.database import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as db:
        result = await db.execute(text(\"SELECT tablename FROM pg_tables WHERE tablename LIKE 'runbook%'\"))
        for row in result:
            print(row[0])

asyncio.run(check())
"
```
Expected output (any order):
```
runbooks
runbook_steps
runbook_executions
runbook_step_results
```

---

## Task 3: Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/runbook.py`

- [ ] **Step 1: Write schemas**

Create `backend/app/schemas/runbook.py`:

```python
from __future__ import annotations
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
    parallel_steps: list[RunbookStepCreate] = []  # children for parallel_group


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
    parallel_steps: list[RunbookStepOut] = []

    model_config = {"from_attributes": True}


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
    step_number: int
    action: str  # "resume" | "abort"
```

- [ ] **Step 2: Verify import**

```bash
cd /app && python -c "from app.schemas.runbook import RunbookCreate, RunbookOut, RunbookExecutionOut; print('OK')"
```
Expected: `OK`

---

## Task 4: Runbook CRUD + Trigger API

**Files:**
- Create: `backend/app/routers/runbooks.py`
- Modify: `backend/app/main.py`
- Create: `backend/app/tests/test_runbooks.py`

### Step 1: Write failing tests first

Create `backend/app/tests/test_runbooks.py`:

```python
"""
Tests for runbook CRUD and trigger endpoints.
All tests use the shared `client` fixture (AsyncClient with overridden auth).
"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_runbooks_empty(client: AsyncClient):
    resp = await client.get("/api/runbooks")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_runbook(client: AsyncClient):
    payload = {
        "name": "Test Runbook",
        "description": "A test",
        "tags": ["test"],
        "steps": [
            {
                "step_number": 1,
                "name": "Patch Fleet",
                "type": "change",
                "change_type": "patch_packages",
                "on_failure": "abort",
                "parallel_steps": [],
            }
        ],
    }
    resp = await client.post("/api/runbooks", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Runbook"
    assert data["version"] == 1
    assert data["is_seed"] is False
    assert len(data["steps"]) == 1
    return data["id"]


@pytest.mark.asyncio
async def test_get_runbook(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Get Test", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    resp = await client.get(f"/api/runbooks/{rb_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == rb_id


@pytest.mark.asyncio
async def test_update_runbook_bumps_version(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Version Test", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    resp = await client.put(f"/api/runbooks/{rb_id}", json={"name": "Version Test v2"})
    assert resp.status_code == 200
    assert resp.json()["version"] == 2
    assert resp.json()["name"] == "Version Test v2"


@pytest.mark.asyncio
async def test_delete_runbook(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Delete Me", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    resp = await client.delete(f"/api/runbooks/{rb_id}")
    assert resp.status_code == 204
    resp2 = await client.get(f"/api/runbooks/{rb_id}")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_fork_runbook(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Original", "tags": ["onboarding"], "steps": []
    })
    rb_id = create_resp.json()["id"]
    resp = await client.post(f"/api/runbooks/{rb_id}/fork")
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Original (copy)"
    assert data["version"] == 1
    assert data["is_seed"] is False


@pytest.mark.asyncio
async def test_trigger_runbook(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Trigger Test", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    resp = await client.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {"env": "prod"}})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "running"
    assert data["runbook_version"] == 1
    assert data["context"] == {"env": "prod"}


@pytest.mark.asyncio
async def test_list_executions(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Exec List Test", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    await client.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {}})
    resp = await client.get(f"/api/runbooks/{rb_id}/executions")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_list_runbooks_tag_filter(client: AsyncClient):
    await client.post("/api/runbooks", json={"name": "Tagged", "tags": ["security"], "steps": []})
    await client.post("/api/runbooks", json={"name": "Untagged", "tags": [], "steps": []})
    resp = await client.get("/api/runbooks?tag=security")
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()]
    assert "Tagged" in names
    assert "Untagged" not in names


@pytest.mark.asyncio
async def test_delete_runbook_with_active_execution_fails(client: AsyncClient):
    create_resp = await client.post("/api/runbooks", json={
        "name": "Active Exec", "tags": [], "steps": []
    })
    rb_id = create_resp.json()["id"]
    await client.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {}})
    resp = await client.delete(f"/api/runbooks/{rb_id}")
    assert resp.status_code == 409
```

- [ ] **Step 2: Run tests — expect failures (router doesn't exist)**

```bash
cd /app && pytest app/tests/test_runbooks.py -v 2>&1 | head -30
```
Expected: import errors or 404s.

### Step 3: Implement the router

Create `backend/app/routers/runbooks.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers import current_user
from app.schemas.runbook import (
    RunbookCreate, RunbookUpdate, RunbookOut, RunbookExecutionOut,
    TriggerRunbookRequest, HumanCheckpointResumeRequest,
)
from app.services.runbook_service import RunbookService

router = APIRouter(prefix="/api/runbooks", tags=["runbooks"])
execution_router = APIRouter(prefix="/api/executions", tags=["runbook-executions"])


@router.get("", response_model=list[RunbookOut])
async def list_runbooks(
    tag: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).list_runbooks(user.organization_id, tag=tag, search=search)


@router.post("", response_model=RunbookOut, status_code=201)
async def create_runbook(
    payload: RunbookCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).create_runbook(user.organization_id, user.id, payload)


@router.get("/{runbook_id}", response_model=RunbookOut)
async def get_runbook(
    runbook_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).get_runbook(runbook_id, user.organization_id)


@router.put("/{runbook_id}", response_model=RunbookOut)
async def update_runbook(
    runbook_id: str,
    payload: RunbookUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).update_runbook(runbook_id, user.organization_id, payload)


@router.delete("/{runbook_id}", status_code=204)
async def delete_runbook(
    runbook_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    await RunbookService(db).delete_runbook(runbook_id, user.organization_id)


@router.post("/{runbook_id}/fork", response_model=RunbookOut, status_code=201)
async def fork_runbook(
    runbook_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).fork_runbook(runbook_id, user.organization_id, user.id)


@router.post("/{runbook_id}/trigger", response_model=RunbookExecutionOut, status_code=201)
async def trigger_runbook(
    runbook_id: str,
    payload: TriggerRunbookRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).trigger_runbook(
        runbook_id, user.organization_id, user.id, payload.context
    )


@router.get("/{runbook_id}/executions", response_model=list[RunbookExecutionOut])
async def list_executions(
    runbook_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).list_executions(runbook_id, user.organization_id)


@execution_router.get("/{execution_id}", response_model=RunbookExecutionOut)
async def get_execution(
    execution_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).get_execution(execution_id, user.organization_id)


@execution_router.post("/{execution_id}/resume", response_model=RunbookExecutionOut)
async def resume_execution(
    execution_id: str,
    payload: HumanCheckpointResumeRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).resume_checkpoint(
        execution_id, payload.step_number, payload.action, user
    )


@execution_router.post("/{execution_id}/abort", response_model=RunbookExecutionOut)
async def abort_execution(
    execution_id: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    return await RunbookService(db).abort_execution(execution_id, user.organization_id)
```

### Step 4: Register routers in main.py

In `backend/app/main.py`, add after the existing imports and `include_router` calls:

```python
# Add to imports at top:
from app.routers.runbooks import router as runbooks_router, execution_router

# Add after existing app.include_router(...) calls:
app.include_router(runbooks_router)
app.include_router(execution_router)
```

- [ ] **Step 5: Run tests — expect pass after service is implemented (Task 5)**

(Tests will pass once `RunbookService` is created in Task 5.)

---

## Task 5: Execution API Tests

**Files:**
- Create: `backend/app/tests/test_runbook_executions.py`

- [ ] **Step 1: Write failing tests first**

Create `backend/app/tests/test_runbook_executions.py`:

```python
"""
Tests for execution detail, resume (human checkpoint), and abort endpoints.
"""
import pytest
from httpx import AsyncClient


async def _create_and_trigger(client: AsyncClient) -> tuple[str, str]:
    """Helper: create a runbook with a human checkpoint and trigger it."""
    resp = await client.post("/api/runbooks", json={
        "name": "Checkpoint Runbook",
        "tags": [],
        "steps": [
            {
                "step_number": 1,
                "name": "Approval Gate",
                "type": "human_checkpoint",
                "prompt": "Please approve.",
                "required_role": "admin",
                "timeout_hours": 24,
                "on_timeout": "abort",
                "on_failure": "abort",
                "parallel_steps": [],
            }
        ],
    })
    rb_id = resp.json()["id"]
    exec_resp = await client.post(f"/api/runbooks/{rb_id}/trigger", json={"context": {}})
    return rb_id, exec_resp.json()["id"]


@pytest.mark.asyncio
async def test_get_execution(client: AsyncClient):
    _, exec_id = await _create_and_trigger(client)
    resp = await client.get(f"/api/executions/{exec_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == exec_id
    assert data["status"] == "running"


@pytest.mark.asyncio
async def test_abort_execution(client: AsyncClient):
    _, exec_id = await _create_and_trigger(client)
    resp = await client.post(f"/api/executions/{exec_id}/abort")
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_resume_execution_wrong_status_fails(client: AsyncClient):
    """Resume should fail if execution is not waiting_human."""
    _, exec_id = await _create_and_trigger(client)
    # Execution is "running", not "waiting_human" yet (poller hasn't ticked)
    resp = await client.post(f"/api/executions/{exec_id}/resume", json={
        "step_number": 1, "action": "resume"
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_execution_wrong_org_fails(client: AsyncClient, other_org_client: AsyncClient):
    """Execution from org A must not be visible to org B."""
    _, exec_id = await _create_and_trigger(client)
    resp = await other_org_client.get(f"/api/executions/{exec_id}")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests — expect failures until service is implemented**

```bash
cd /app && pytest app/tests/test_runbook_executions.py -v 2>&1 | head -20
```

---

## Task 6: RunbookService + RunbookExecutor

**Files:**
- Create: `backend/app/services/runbook_service.py`
- Create: `backend/app/services/runbook_executor.py`
- Modify: `backend/app/services/scheduler_service.py` (register executor job)

### Step 1: Implement RunbookService

Create `backend/app/services/runbook_service.py`:

```python
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from app.models.runbook import Runbook, RunbookStep, RunbookExecution, RunbookStepResult
from app.schemas.runbook import RunbookCreate, RunbookUpdate, RunbookOut

log = logging.getLogger(__name__)

_RB_OPTIONS = [selectinload(Runbook.steps).selectinload(RunbookStep.children)]
_EXEC_OPTIONS = [selectinload(RunbookExecution.step_results)]


class RunbookService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # --- Runbook CRUD ---

    async def list_runbooks(self, org_id: uuid.UUID, *, tag: str | None = None,
                            search: str | None = None) -> list[Runbook]:
        q = select(Runbook).where(Runbook.organization_id == org_id).options(*_RB_OPTIONS)
        if tag:
            q = q.where(Runbook.tags.any(tag))
        if search:
            q = q.where(Runbook.name.ilike(f"%{search}%"))
        result = await self.db.execute(q)
        return list(result.scalars().all())

    async def get_runbook(self, runbook_id: str, org_id: uuid.UUID) -> Runbook:
        result = await self.db.execute(
            select(Runbook)
            .where(Runbook.id == uuid.UUID(runbook_id), Runbook.organization_id == org_id)
            .options(*_RB_OPTIONS)
        )
        rb = result.scalar_one_or_none()
        if not rb:
            raise HTTPException(status_code=404, detail="Runbook not found")
        return rb

    async def create_runbook(self, org_id: uuid.UUID, user_id: uuid.UUID,
                             payload: RunbookCreate) -> Runbook:
        rb = Runbook(
            organization_id=org_id,
            name=payload.name,
            description=payload.description,
            tags=payload.tags,
            version=1,
            is_seed=False,
            created_by=user_id,
        )
        self.db.add(rb)
        await self.db.flush()
        await self._insert_steps(payload.steps, rb.id, parent_id=None)
        await self.db.commit()
        await self.db.refresh(rb)
        return await self.get_runbook(str(rb.id), org_id)

    async def update_runbook(self, runbook_id: str, org_id: uuid.UUID,
                             payload: RunbookUpdate) -> Runbook:
        rb = await self.get_runbook(runbook_id, org_id)
        if payload.name is not None:
            rb.name = payload.name
        if payload.description is not None:
            rb.description = payload.description
        if payload.tags is not None:
            rb.tags = payload.tags
        if payload.steps is not None:
            # Replace all steps atomically
            await self.db.execute(
                RunbookStep.__table__.delete().where(RunbookStep.runbook_id == rb.id)
            )
            await self._insert_steps(payload.steps, rb.id, parent_id=None)
        rb.version += 1
        await self.db.commit()
        return await self.get_runbook(runbook_id, org_id)

    async def delete_runbook(self, runbook_id: str, org_id: uuid.UUID) -> None:
        rb = await self.get_runbook(runbook_id, org_id)
        # Reject if active executions exist
        active = await self.db.execute(
            select(RunbookExecution).where(
                RunbookExecution.runbook_id == rb.id,
                RunbookExecution.status.in_(["running", "waiting_human"]),
            )
        )
        if active.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Cannot delete runbook with active executions")
        await self.db.delete(rb)
        await self.db.commit()

    async def fork_runbook(self, runbook_id: str, org_id: uuid.UUID,
                           user_id: uuid.UUID) -> Runbook:
        # Seed templates can be forked even from a different org (they are global)
        result = await self.db.execute(
            select(Runbook).where(Runbook.id == uuid.UUID(runbook_id)).options(*_RB_OPTIONS)
        )
        source = result.scalar_one_or_none()
        if not source:
            raise HTTPException(status_code=404, detail="Runbook not found")

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
        return await self.get_runbook(str(new_rb.id), org_id)

    # --- Trigger ---

    async def trigger_runbook(self, runbook_id: str, org_id: uuid.UUID,
                              user_id: uuid.UUID, context: dict) -> RunbookExecution:
        rb = await self.get_runbook(runbook_id, org_id)
        snapshot = RunbookOut.model_validate(rb).model_dump(mode="json")
        # Embed org_id in snapshot for executor
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
        return await self.get_execution(str(execution.id), org_id)

    # --- Execution queries ---

    async def list_executions(self, runbook_id: str, org_id: uuid.UUID) -> list[RunbookExecution]:
        # Verify runbook belongs to org
        await self.get_runbook(runbook_id, org_id)
        result = await self.db.execute(
            select(RunbookExecution)
            .where(RunbookExecution.runbook_id == uuid.UUID(runbook_id))
            .options(*_EXEC_OPTIONS)
            .order_by(RunbookExecution.triggered_at.desc())
        )
        return list(result.scalars().all())

    async def get_execution(self, execution_id: str, org_id: uuid.UUID) -> RunbookExecution:
        result = await self.db.execute(
            select(RunbookExecution)
            .where(RunbookExecution.id == uuid.UUID(execution_id))
            .options(*_EXEC_OPTIONS, selectinload(RunbookExecution.runbook))
        )
        ex = result.scalar_one_or_none()
        if not ex or ex.runbook.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Execution not found")
        return ex

    async def resume_checkpoint(self, execution_id: str, step_number: int,
                                action: str, user) -> RunbookExecution:
        ex = await self.get_execution(execution_id, user.organization_id)
        if ex.status != "waiting_human":
            raise HTTPException(status_code=409, detail="Execution is not waiting for human input")

        # Find the waiting step result
        result = await self.db.execute(
            select(RunbookStepResult).where(
                RunbookStepResult.execution_id == ex.id,
                RunbookStepResult.step_number == step_number,
                RunbookStepResult.status == "waiting_human",
            )
        )
        step_result = result.scalar_one_or_none()
        if not step_result:
            raise HTTPException(status_code=404, detail="Waiting step not found")

        now = datetime.now(timezone.utc)
        if action == "resume":
            step_result.status = "completed"
            step_result.completed_at = now
            step_result.result = {
                "action": "resumed",
                "resumed_by": str(user.id),
                "resumed_at": now.isoformat(),
            }
            ex.status = "running"
            ex.current_step = step_number + 1
        elif action == "abort":
            step_result.status = "failed"
            step_result.error_message = f"Aborted by {user.email}"
            ex.status = "failed"
            ex.completed_at = now
        else:
            raise HTTPException(status_code=400, detail="action must be 'resume' or 'abort'")

        await self.db.commit()
        return await self.get_execution(execution_id, user.organization_id)

    async def abort_execution(self, execution_id: str, org_id: uuid.UUID) -> RunbookExecution:
        ex = await self.get_execution(execution_id, org_id)
        if ex.status not in ("running", "waiting_human"):
            raise HTTPException(status_code=409, detail="Execution is not active")
        now = datetime.now(timezone.utc)
        ex.status = "failed"
        ex.completed_at = now
        # Mark current in-progress step result as failed
        result = await self.db.execute(
            select(RunbookStepResult).where(
                RunbookStepResult.execution_id == ex.id,
                RunbookStepResult.step_number == ex.current_step,
            )
        )
        sr = result.scalar_one_or_none()
        if sr:
            sr.status = "failed"
            sr.error_message = "Aborted by operator"
        await self.db.commit()
        return await self.get_execution(execution_id, org_id)

    # --- Internal helpers ---

    async def _insert_steps(self, steps, runbook_id: uuid.UUID,
                            parent_id: uuid.UUID | None) -> None:
        for s in steps:
            step = RunbookStep(
                runbook_id=runbook_id,
                parent_step_id=parent_id,
                step_number=s.step_number,
                name=s.name,
                type=s.type,
                change_type=s.change_type,
                parameters=s.parameters,
                asset_selector=s.asset_selector.model_dump() if s.asset_selector else None,
                condition_expr=s.condition_expr,
                on_true_step=s.on_true_step,
                on_false_step=s.on_false_step,
                prompt=s.prompt,
                required_role=s.required_role,
                timeout_hours=s.timeout_hours,
                on_timeout=s.on_timeout,
                on_failure=s.on_failure,
            )
            self.db.add(step)
            await self.db.flush()
            if s.parallel_steps:
                await self._insert_steps(s.parallel_steps, runbook_id, step.id)

    async def _copy_steps(self, steps, runbook_id: uuid.UUID,
                          parent_id: uuid.UUID | None) -> None:
        for s in steps:
            new_step = RunbookStep(
                runbook_id=runbook_id,
                parent_step_id=parent_id,
                step_number=s.step_number,
                name=s.name,
                type=s.type,
                change_type=s.change_type,
                parameters=s.parameters,
                asset_selector=s.asset_selector,
                condition_expr=s.condition_expr,
                on_true_step=s.on_true_step,
                on_false_step=s.on_false_step,
                prompt=s.prompt,
                required_role=s.required_role,
                timeout_hours=s.timeout_hours,
                on_timeout=s.on_timeout,
                on_failure=s.on_failure,
            )
            self.db.add(new_step)
            await self.db.flush()
            if s.children:
                await self._copy_steps(s.children, runbook_id, new_step.id)
```

### Step 2: Implement RunbookExecutor

Create `backend/app/services/runbook_executor.py`:

```python
"""
APScheduler tick function for advancing RunbookExecution state.

Called every 30 seconds by the scheduler. Each tick inspects the current step
of every active execution and performs the appropriate action.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.runbook import RunbookExecution, RunbookStepResult

log = logging.getLogger(__name__)


async def tick_all_executions(db_factory) -> None:
    """Entry point called by APScheduler. db_factory is the AsyncSessionLocal callable."""
    async with db_factory() as db:
        result = await db.execute(
            select(RunbookExecution)
            .where(RunbookExecution.status.in_(["running", "waiting_human"]))
            .options(selectinload(RunbookExecution.step_results))
        )
        executions = result.scalars().all()
        for execution in executions:
            try:
                await tick_execution(db, execution)
            except Exception as e:
                log.error("Executor error for execution %s: %s", execution.id, e, exc_info=True)
        await db.commit()


async def tick_execution(db: AsyncSession, execution: RunbookExecution) -> None:
    steps = execution.runbook_snapshot.get("steps", [])
    step_map = {s["step_number"]: s for s in steps}

    current_step_def = step_map.get(execution.current_step)
    if current_step_def is None:
        # No more steps — execution complete
        execution.status = "completed"
        execution.completed_at = datetime.now(timezone.utc)
        return

    step_result = await _get_or_create_step_result(db, execution, current_step_def)

    match current_step_def["type"]:
        case "change":
            await _execute_change_step(db, execution, current_step_def, step_result)
        case "condition":
            await _execute_condition_step(db, execution, current_step_def, step_result)
        case "human_checkpoint":
            await _execute_checkpoint_step(db, execution, current_step_def, step_result)
        case "parallel_group":
            await _execute_parallel_step(db, execution, current_step_def, step_result)
        case _:
            log.error("Unknown step type %s in execution %s", current_step_def["type"], execution.id)


async def _get_or_create_step_result(
    db: AsyncSession, execution: RunbookExecution, step_def: dict
) -> RunbookStepResult:
    for sr in execution.step_results:
        if sr.step_number == step_def["step_number"]:
            return sr
    sr = RunbookStepResult(
        execution_id=execution.id,
        step_number=step_def["step_number"],
        step_name=step_def["name"],
        step_type=step_def["type"],
        status="pending",
    )
    db.add(sr)
    await db.flush()
    execution.step_results.append(sr)
    return sr


async def _execute_change_step(
    db: AsyncSession, execution: RunbookExecution, step_def: dict, step_result: RunbookStepResult
) -> None:
    if step_result.status == "pending":
        # Import here to avoid circular imports
        from app.services.runbook_cr_bridge import create_runbook_change_request
        cr = await create_runbook_change_request(db, execution, step_def)
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


async def _execute_condition_step(
    db: AsyncSession, execution: RunbookExecution, step_def: dict, step_result: RunbookStepResult
) -> None:
    if step_result.status != "pending":
        return

    # Build restricted namespace from completed step results
    steps_ns = {sr.step_number: sr.result for sr in execution.step_results}
    namespace = {"steps": steps_ns, "ctx": execution.context, "__builtins__": {}}

    try:
        result = bool(eval(step_def["condition_expr"], namespace))  # noqa: S307
    except Exception as e:
        step_result.status = "failed"
        step_result.error_message = f"Condition eval error: {e}"
        await _handle_step_failure(db, execution, step_def)
        return

    next_step = step_def["on_true_step"] if result else step_def["on_false_step"]
    step_result.status = "completed"
    step_result.completed_at = datetime.now(timezone.utc)
    step_result.result = {"evaluated_to": result, "jumped_to_step": next_step}
    # step_number 99 is convention for "end of runbook" in templates
    execution.current_step = next_step if next_step is not None else 9999


async def _execute_checkpoint_step(
    db: AsyncSession, execution: RunbookExecution, step_def: dict, step_result: RunbookStepResult
) -> None:
    if step_result.status == "pending":
        step_result.status = "waiting_human"
        step_result.started_at = datetime.now(timezone.utc)
        execution.status = "waiting_human"
        # Notification is best-effort; failures are logged, not fatal
        try:
            from app.services.runbook_notifications import send_checkpoint_notification
            await send_checkpoint_notification(execution, step_def)
        except Exception as e:
            log.warning("Failed to send checkpoint notification: %s", e)
        return

    if step_result.status == "waiting_human":
        timeout_hours = step_def.get("timeout_hours")
        if timeout_hours and step_result.started_at:
            elapsed_h = (
                datetime.now(timezone.utc) - step_result.started_at
            ).total_seconds() / 3600
            if elapsed_h >= timeout_hours:
                on_timeout = step_def.get("on_timeout", "abort")
                now = datetime.now(timezone.utc)
                if on_timeout == "abort":
                    step_result.status = "failed"
                    step_result.error_message = "Human checkpoint timed out"
                    step_result.completed_at = now
                    execution.status = "failed"
                    execution.completed_at = now
                else:  # "continue"
                    step_result.status = "completed"
                    step_result.completed_at = now
                    step_result.result = {"action": "timeout_continue"}
                    execution.status = "running"
                    execution.current_step += 1
    # If status is "completed", the resume endpoint already advanced current_step — nothing to do.


async def _execute_parallel_step(
    db: AsyncSession, execution: RunbookExecution, step_def: dict, step_result: RunbookStepResult
) -> None:
    child_steps = step_def.get("parallel_steps", [])

    if step_result.status == "pending":
        from app.services.runbook_cr_bridge import create_runbook_change_request
        cr_ids = []
        for child in child_steps:
            cr = await create_runbook_change_request(db, execution, child)
            cr_ids.append(str(cr.id))
        step_result.change_request_ids = cr_ids
        step_result.status = "running"
        step_result.started_at = datetime.now(timezone.utc)
        return

    if step_result.status == "running":
        import uuid as _uuid
        all_done = True
        any_failed = False
        child_results = []
        for cr_id in step_result.change_request_ids:
            cr = await db.get(ChangeRequest, _uuid.UUID(cr_id))
            terminal = cr and cr.status in (
                ChangeRequestStatus.completed, ChangeRequestStatus.failed,
                ChangeRequestStatus.rejected, ChangeRequestStatus.rolled_back,
            )
            if not terminal:
                all_done = False
            if cr and cr.status != ChangeRequestStatus.completed:
                any_failed = True
            child_results.append({"cr_id": cr_id, "status": cr.status if cr else "unknown"})

        if not all_done:
            return

        step_result.result = {"child_results": child_results}
        step_result.completed_at = datetime.now(timezone.utc)

        if any_failed:
            step_result.status = "failed"
            await _handle_step_failure(db, execution, step_def)
        else:
            step_result.status = "completed"
            execution.current_step += 1


async def _handle_step_failure(
    db: AsyncSession, execution: RunbookExecution, step_def: dict
) -> None:
    on_failure = step_def.get("on_failure", "abort")
    now = datetime.now(timezone.utc)
    if on_failure == "abort":
        execution.status = "failed"
        execution.completed_at = now
    elif on_failure == "continue":
        execution.current_step += 1
    elif on_failure == "rollback_all":
        # Rollback hook: create rollback change requests in reverse order.
        # Full rollback CR support is deferred — log and fail for now.
        log.warning(
            "rollback_all requested for execution %s but rollback CRs are not yet implemented; marking failed.",
            execution.id,
        )
        execution.status = "rolled_back"
        execution.completed_at = now
```

### Step 3: Create CR bridge helper

Create `backend/app/services/runbook_cr_bridge.py`:

```python
"""
Thin bridge between the runbook executor and the existing ChangeRequest creation flow.
Isolates the executor from ChangeRequest model details.
"""
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeType, RiskLevel, ChangeRequestStatus
from app.models.runbook import RunbookExecution


async def create_runbook_change_request(
    db: AsyncSession,
    execution: RunbookExecution,
    step_def: dict,
) -> ChangeRequest:
    """
    Create a ChangeRequest on behalf of a runbook step.
    Maps step_def["change_type"] to the ChangeType enum; falls back to a best-effort
    match. If the change_type is not a known enum value, raises ValueError.
    """
    change_type_str = step_def.get("change_type", "")
    try:
        change_type = ChangeType(change_type_str)
    except ValueError:
        raise ValueError(
            f"Unknown change_type '{change_type_str}' in runbook step '{step_def.get('name')}'. "
            f"Valid types: {[e.value for e in ChangeType]}"
        )

    parameters = step_def.get("parameters") or {}
    parameters.update(execution.context)  # runtime context overrides step defaults

    cr = ChangeRequest(
        organization_id=uuid.UUID(execution.runbook_snapshot["organization_id"]),
        requester_id=execution.triggered_by,
        title=f"[Runbook] {step_def['name']}",
        description=(
            f"Created automatically by runbook execution {execution.id}, "
            f"step {step_def['step_number']}: {step_def['name']}"
        ),
        change_type=change_type,
        target_asset_ids=_resolve_asset_ids(step_def.get("asset_selector")),
        desired_outcome=parameters,
        risk_level=RiskLevel.medium,
        status=ChangeRequestStatus.draft,
        source="runbook",
        runbook_execution_id=execution.id,
    )
    db.add(cr)
    await db.flush()
    return cr


def _resolve_asset_ids(asset_selector: dict | None) -> list:
    """Extract explicit asset_ids from asset_selector. Tag/env resolution is deferred."""
    if not asset_selector:
        return []
    return [str(aid) for aid in asset_selector.get("asset_ids", [])]
```

### Step 4: Register executor job in scheduler_service.py

In `backend/app/services/scheduler_service.py`, add after the existing `scheduler.add_job(...)` calls inside `start()`:

```python
    from app.services.runbook_executor import tick_all_executions
    scheduler.add_job(
        tick_all_executions,
        args=[_db_factory],
        trigger="interval",
        seconds=30,
        id="runbook_executor",
        replace_existing=True,
    )
```

- [ ] **Step 5: Run all tests**

```bash
cd /app && pytest app/tests/test_runbooks.py app/tests/test_runbook_executions.py -v
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/runbook.py \
        backend/app/schemas/runbook.py \
        backend/app/routers/runbooks.py \
        backend/app/services/runbook_service.py \
        backend/app/services/runbook_executor.py \
        backend/app/services/runbook_cr_bridge.py \
        backend/app/services/scheduler_service.py \
        backend/app/main.py \
        backend/app/tests/test_runbooks.py \
        backend/app/tests/test_runbook_executions.py \
        backend/alembic/versions/016_add_runbooks.py
git commit -m "feat: composable runbooks — models, migration, API, service, and executor"
```

---

## Task 7: Seed Templates

**Files:**
- Create: `backend/app/seed/runbook_templates.py`

- [ ] **Step 1: Write the seed module**

Create `backend/app/seed/runbook_templates.py`:

```python
"""
Pre-built runbook seed templates.

Call `load_seed_templates(db, org_id, system_user_id)` once during initial setup
or from an Alembic data migration. Idempotent: skips runbooks where is_seed=True
and name already exists for the org.
"""
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runbook import Runbook, RunbookStep

SEED_TEMPLATES = [
    {
        "name": "Engineer Onboarding",
        "description": (
            "Provision a new engineer: create AD account, assign Okta groups, "
            "add to GitHub org, send welcome email."
        ),
        "tags": ["onboarding", "identity"],
        "steps": [
            {"step_number": 1, "name": "Create AD Account", "type": "change",
             "change_type": "create_ad_account", "on_failure": "abort"},
            {"step_number": 2, "name": "Assign Okta Groups", "type": "change",
             "change_type": "assign_okta_groups", "on_failure": "abort"},
            {"step_number": 3, "name": "Add to GitHub Org", "type": "change",
             "change_type": "add_github_org_member", "on_failure": "continue"},
            {
                "step_number": 4, "name": "Manager Approval", "type": "human_checkpoint",
                "prompt": (
                    "Confirm the above accounts were provisioned correctly "
                    "and approve sending the welcome email."
                ),
                "required_role": "manager", "timeout_hours": 48, "on_timeout": "abort",
                "on_failure": "abort",
            },
            {"step_number": 5, "name": "Send Welcome Email", "type": "change",
             "change_type": "send_welcome_email", "on_failure": "continue"},
        ],
    },
    {
        "name": "Incident Response: Account Compromise",
        "description": (
            "Lock down a compromised account, preserve evidence, "
            "reset credentials, and notify security."
        ),
        "tags": ["incident-response", "security"],
        "steps": [
            {"step_number": 1, "name": "Lockdown Account", "type": "change",
             "change_type": "lockdown_account", "on_failure": "abort"},
            {"step_number": 2, "name": "Preserve Evidence", "type": "change",
             "change_type": "preserve_cloudtrail_logs", "on_failure": "continue"},
            {"step_number": 3, "name": "Force Password Reset", "type": "change",
             "change_type": "force_password_reset", "on_failure": "abort"},
            {
                "step_number": 4, "name": "Notify Security Team", "type": "human_checkpoint",
                "prompt": (
                    "Review the locked account and preserved logs. "
                    "Confirm notification has been sent to the security team."
                ),
                "required_role": "security", "timeout_hours": 4, "on_timeout": "continue",
                "on_failure": "abort",
            },
            {
                "step_number": 5, "name": "Verify Lockdown", "type": "condition",
                "condition_expr": "steps[1]['exit_code'] == 0",
                "on_true_step": 6, "on_false_step": 99, "on_failure": "abort",
            },
            {"step_number": 6, "name": "Close Incident", "type": "change",
             "change_type": "close_incident_ticket", "on_failure": "continue"},
        ],
    },
    {
        "name": "Patch Campaign",
        "description": "Fleet health check, rolling patch, compliance verification.",
        "tags": ["patch", "compliance"],
        "steps": [
            {
                "step_number": 1, "name": "Fleet Health Check", "type": "change",
                "change_type": "check_fleet_health",
                "asset_selector": {"tags": [], "asset_ids": [], "environment": "prod"},
                "on_failure": "abort",
            },
            {
                "step_number": 2, "name": "Health Check Passed?", "type": "condition",
                "condition_expr": "steps[1]['exit_code'] == 0",
                "on_true_step": 3, "on_false_step": 99, "on_failure": "abort",
            },
            {
                "step_number": 3, "name": "Operator Approval", "type": "human_checkpoint",
                "prompt": "Fleet health check passed. Approve rolling patch to prod?",
                "required_role": "operator", "timeout_hours": 24, "on_timeout": "abort",
                "on_failure": "abort",
            },
            {
                "step_number": 4, "name": "Rolling Patch", "type": "change",
                "change_type": "patch_packages",
                "asset_selector": {"tags": [], "asset_ids": [], "environment": "prod"},
                "on_failure": "abort",
            },
            {
                "step_number": 5, "name": "Verify Compliance", "type": "change",
                "change_type": "check_compliance",
                "asset_selector": {"tags": [], "asset_ids": [], "environment": "prod"},
                "on_failure": "continue",
            },
        ],
    },
]


async def load_seed_templates(
    db: AsyncSession, org_id: uuid.UUID, system_user_id: uuid.UUID
) -> None:
    """Insert seed templates for an org. Idempotent."""
    for template in SEED_TEMPLATES:
        existing = await db.execute(
            select(Runbook).where(
                Runbook.organization_id == org_id,
                Runbook.name == template["name"],
                Runbook.is_seed.is_(True),
            )
        )
        if existing.scalar_one_or_none():
            continue  # Already seeded

        rb = Runbook(
            organization_id=org_id,
            name=template["name"],
            description=template["description"],
            tags=template["tags"],
            version=1,
            is_seed=True,
            created_by=system_user_id,
        )
        db.add(rb)
        await db.flush()

        for step_def in template["steps"]:
            step = RunbookStep(
                runbook_id=rb.id,
                step_number=step_def["step_number"],
                name=step_def["name"],
                type=step_def["type"],
                change_type=step_def.get("change_type"),
                parameters=step_def.get("parameters"),
                asset_selector=step_def.get("asset_selector"),
                condition_expr=step_def.get("condition_expr"),
                on_true_step=step_def.get("on_true_step"),
                on_false_step=step_def.get("on_false_step"),
                prompt=step_def.get("prompt"),
                required_role=step_def.get("required_role"),
                timeout_hours=step_def.get("timeout_hours"),
                on_timeout=step_def.get("on_timeout"),
                on_failure=step_def.get("on_failure", "abort"),
            )
            db.add(step)

    await db.commit()
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/seed/runbook_templates.py
git commit -m "feat: runbook seed templates — engineer onboarding, incident response, patch campaign"
```

---

## Task 8: Frontend — Runbooks Pages

**Files:**
- Create: `frontend/src/pages/Runbooks.tsx`
- Create: `frontend/src/pages/RunbookEditor.tsx`
- Create: `frontend/src/pages/RunbookExecution.tsx`
- Create: `frontend/src/hooks/useRunbooks.ts`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/routes/index.tsx`

### Step 1: API hooks

Create `frontend/src/hooks/useRunbooks.ts`:

```typescript
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

export interface RunbookStepOut {
  id: string;
  step_number: number;
  name: string;
  type: string;
  change_type?: string;
  parameters?: Record<string, unknown>;
  asset_selector?: { tags: string[]; asset_ids: string[]; environment?: string };
  condition_expr?: string;
  on_true_step?: number;
  on_false_step?: number;
  prompt?: string;
  required_role?: string;
  timeout_hours?: number;
  on_timeout?: string;
  on_failure: string;
  parallel_steps: RunbookStepOut[];
}

export interface RunbookOut {
  id: string;
  organization_id: string;
  name: string;
  description?: string;
  version: number;
  tags: string[];
  is_seed: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
  steps: RunbookStepOut[];
}

export interface RunbookStepResultOut {
  id: string;
  step_number: number;
  step_name: string;
  step_type: string;
  status: string;
  started_at?: string;
  completed_at?: string;
  change_request_ids: string[];
  result: Record<string, unknown>;
  error_message?: string;
}

export interface RunbookExecutionOut {
  id: string;
  runbook_id: string;
  runbook_version: number;
  triggered_by: string;
  triggered_at: string;
  completed_at?: string;
  context: Record<string, unknown>;
  status: string;
  current_step: number;
  step_results: RunbookStepResultOut[];
}

export const useRunbooks = (params?: { tag?: string; search?: string }) =>
  useQuery<RunbookOut[]>({
    queryKey: ["runbooks", params],
    queryFn: () =>
      apiClient.get<RunbookOut[]>("/api/runbooks", { params }).then((r) => r.data),
  });

export const useRunbook = (id: string) =>
  useQuery<RunbookOut>({
    queryKey: ["runbook", id],
    queryFn: () => apiClient.get<RunbookOut>(`/api/runbooks/${id}`).then((r) => r.data),
    enabled: !!id,
  });

export const useRunbookExecution = (id: string, poll: boolean) =>
  useQuery<RunbookExecutionOut>({
    queryKey: ["runbook-execution", id],
    queryFn: () =>
      apiClient.get<RunbookExecutionOut>(`/api/executions/${id}`).then((r) => r.data),
    refetchInterval: poll ? 5000 : false,
    enabled: !!id,
  });

export const useRunbookExecutions = (runbookId: string) =>
  useQuery<RunbookExecutionOut[]>({
    queryKey: ["runbook-executions", runbookId],
    queryFn: () =>
      apiClient
        .get<RunbookExecutionOut[]>(`/api/runbooks/${runbookId}/executions`)
        .then((r) => r.data),
    enabled: !!runbookId,
  });

export const useCreateRunbook = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<RunbookOut>) =>
      apiClient.post<RunbookOut>("/api/runbooks", data).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runbooks"] }),
  });
};

export const useUpdateRunbook = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<RunbookOut>) =>
      apiClient.put<RunbookOut>(`/api/runbooks/${id}`, data).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runbooks"] });
      qc.invalidateQueries({ queryKey: ["runbook", id] });
    },
  });
};

export const useForkRunbook = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.post<RunbookOut>(`/api/runbooks/${id}/fork`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runbooks"] }),
  });
};

export const useTriggerRunbook = (id: string) =>
  useMutation({
    mutationFn: (ctx: Record<string, unknown>) =>
      apiClient
        .post<RunbookExecutionOut>(`/api/runbooks/${id}/trigger`, { context: ctx })
        .then((r) => r.data),
  });

export const useResumeCheckpoint = (executionId: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ step_number, action }: { step_number: number; action: string }) =>
      apiClient
        .post<RunbookExecutionOut>(`/api/executions/${executionId}/resume`, {
          step_number,
          action,
        })
        .then((r) => r.data),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["runbook-execution", executionId] }),
  });
};

export const useAbortExecution = (executionId: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiClient
        .post<RunbookExecutionOut>(`/api/executions/${executionId}/abort`)
        .then((r) => r.data),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["runbook-execution", executionId] }),
  });
};
```

### Step 2: Runbooks list page

Create `frontend/src/pages/Runbooks.tsx`:

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Copy, Play, BookOpen } from "lucide-react";
import { useRunbooks, useForkRunbook, useTriggerRunbook } from "../hooks/useRunbooks";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

export function Runbooks() {
  const navigate = useNavigate();
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
        action={
          <button
            onClick={() => navigate("/runbooks/new")}
            className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 text-sm font-medium"
          >
            <Plus className="w-4 h-4" /> New Runbook
          </button>
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
            <div
              key={rb.id}
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
              <div className="flex items-center gap-2 ml-4 flex-shrink-0">
                {rb.is_seed ? (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      fork.mutate(rb.id);
                    }}
                    className="flex items-center gap-1 px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-100"
                  >
                    <Copy className="w-3 h-3" /> Fork
                  </button>
                ) : (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/runbooks/${rb.id}`);
                    }}
                    className="flex items-center gap-1 px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-100"
                  >
                    Edit
                  </button>
                )}
                <TriggerButton runbookId={rb.id} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TriggerButton({ runbookId }: { runbookId: string }) {
  const navigate = useNavigate();
  const trigger = useTriggerRunbook(runbookId);
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        trigger.mutate(
          {},
          { onSuccess: (exec) => navigate(`/executions/${exec.id}`) }
        );
      }}
      disabled={trigger.isPending}
      className="flex items-center gap-1 px-2 py-1 text-xs bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-50"
    >
      <Play className="w-3 h-3" /> Run
    </button>
  );
}
```

### Step 3: RunbookEditor page

Create `frontend/src/pages/RunbookEditor.tsx`:

```tsx
import { useState, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Plus, Trash2, GripVertical, Play } from "lucide-react";
import { useRunbook, useCreateRunbook, useUpdateRunbook, useTriggerRunbook } from "../hooks/useRunbooks";
import type { RunbookStepOut } from "../hooks/useRunbooks";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

type StepDraft = Omit<RunbookStepOut, "id" | "runbook_id">;

const BLANK_STEP = (n: number): StepDraft => ({
  step_number: n,
  name: "",
  type: "change",
  on_failure: "abort",
  parallel_steps: [],
});

export function RunbookEditor() {
  const { id } = useParams<{ id: string }>();
  const isNew = !id || id === "new";
  const navigate = useNavigate();

  const { data: existing, isLoading } = useRunbook(isNew ? "" : id!);
  const create = useCreateRunbook();
  const update = useUpdateRunbook(id ?? "");
  const trigger = useTriggerRunbook(id ?? "");

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [steps, setSteps] = useState<StepDraft[]>([BLANK_STEP(1)]);
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [ctxKV, setCtxKV] = useState([{ k: "", v: "" }]);

  useEffect(() => {
    if (existing) {
      setName(existing.name);
      setDescription(existing.description ?? "");
      setTags(existing.tags.join(", "));
      setSteps(
        existing.steps.map((s) => ({
          ...s,
          parallel_steps: s.parallel_steps ?? [],
        }))
      );
    }
  }, [existing]);

  if (!isNew && isLoading) return <PageLoading />;

  const addStep = () =>
    setSteps((prev) => [...prev, BLANK_STEP(prev.length + 1)]);

  const removeStep = (i: number) =>
    setSteps((prev) =>
      prev.filter((_, idx) => idx !== i).map((s, idx) => ({ ...s, step_number: idx + 1 }))
    );

  const updateStep = (i: number, patch: Partial<StepDraft>) =>
    setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));

  const save = async () => {
    const payload = {
      name,
      description: description || undefined,
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      steps,
    };
    if (isNew) {
      const rb = await create.mutateAsync(payload);
      navigate(`/runbooks/${rb.id}`);
    } else {
      await update.mutateAsync(payload);
      navigate(`/runbooks/${id}`);
    }
  };

  const handleTrigger = () => {
    const context = Object.fromEntries(
      ctxKV.filter((kv) => kv.k).map((kv) => [kv.k, kv.v])
    );
    trigger.mutate(context, {
      onSuccess: (exec) => navigate(`/executions/${exec.id}`),
    });
  };

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <PageHeader
        title={isNew ? "New Runbook" : "Edit Runbook"}
        subtitle={isNew ? "Define steps to compose into a reusable workflow." : `Editing v${existing?.version ?? 1}`}
        action={
          <div className="flex gap-2">
            {!isNew && (
              <button
                onClick={() => setTriggerOpen(true)}
                className="flex items-center gap-2 px-4 py-2 border border-brand-600 text-brand-600 rounded-md hover:bg-brand-50 text-sm font-medium"
              >
                <Play className="w-4 h-4" /> Trigger
              </button>
            )}
            <button
              onClick={save}
              disabled={create.isPending || update.isPending}
              className="px-4 py-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 text-sm font-medium disabled:opacity-50"
            >
              Save
            </button>
          </div>
        }
      />

      <div className="space-y-4 mb-6">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Name</label>
          <input
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Engineer Onboarding"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Description</label>
          <textarea
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Tags (comma-separated)</label>
          <input
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="e.g. onboarding, identity"
          />
        </div>
      </div>

      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">Steps</h2>
        <button
          onClick={addStep}
          className="flex items-center gap-1 text-xs text-brand-600 hover:underline"
        >
          <Plus className="w-3 h-3" /> Add step
        </button>
      </div>

      <div className="space-y-3">
        {steps.map((step, i) => (
          <StepCard
            key={i}
            index={i}
            step={step}
            onChange={(patch) => updateStep(i, patch)}
            onRemove={() => removeStep(i)}
          />
        ))}
      </div>

      {/* Trigger modal */}
      {triggerOpen && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl w-96 p-6">
            <h3 className="font-semibold text-slate-900 mb-3">Trigger Runbook</h3>
            <p className="text-sm text-slate-500 mb-4">
              Supply runtime context variables (available as <code>ctx</code> in condition expressions).
            </p>
            {ctxKV.map((kv, i) => (
              <div key={i} className="flex gap-2 mb-2">
                <input
                  className="flex-1 border border-slate-300 rounded px-2 py-1 text-sm"
                  placeholder="key"
                  value={kv.k}
                  onChange={(e) =>
                    setCtxKV((prev) => prev.map((x, idx) => idx === i ? { ...x, k: e.target.value } : x))
                  }
                />
                <input
                  className="flex-1 border border-slate-300 rounded px-2 py-1 text-sm"
                  placeholder="value"
                  value={kv.v}
                  onChange={(e) =>
                    setCtxKV((prev) => prev.map((x, idx) => idx === i ? { ...x, v: e.target.value } : x))
                  }
                />
              </div>
            ))}
            <button
              onClick={() => setCtxKV((prev) => [...prev, { k: "", v: "" }])}
              className="text-xs text-brand-600 hover:underline mb-4"
            >
              + Add variable
            </button>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setTriggerOpen(false)}
                className="px-3 py-2 text-sm border border-slate-300 rounded hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                onClick={handleTrigger}
                disabled={trigger.isPending}
                className="px-3 py-2 text-sm bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-50"
              >
                {trigger.isPending ? "Starting..." : "Trigger"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StepCard({
  index,
  step,
  onChange,
  onRemove,
}: {
  index: number;
  step: StepDraft;
  onChange: (patch: Partial<StepDraft>) => void;
  onRemove: () => void;
}) {
  return (
    <div className="border border-slate-200 rounded-lg p-4 bg-white">
      <div className="flex items-center gap-2 mb-3">
        <GripVertical className="w-4 h-4 text-slate-300" />
        <span className="text-xs font-medium text-slate-400 w-5">{step.step_number}</span>
        <input
          className="flex-1 border border-slate-300 rounded px-2 py-1 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-brand-500"
          placeholder="Step name"
          value={step.name}
          onChange={(e) => onChange({ name: e.target.value })}
        />
        <select
          className="border border-slate-300 rounded px-2 py-1 text-sm focus:outline-none"
          value={step.type}
          onChange={(e) => onChange({ type: e.target.value })}
        >
          <option value="change">Change</option>
          <option value="condition">Condition</option>
          <option value="human_checkpoint">Human Checkpoint</option>
          <option value="parallel_group">Parallel Group</option>
        </select>
        <button onClick={onRemove} className="text-slate-400 hover:text-red-500">
          <Trash2 className="w-4 h-4" />
        </button>
      </div>

      {step.type === "change" && (
        <div className="space-y-2 pl-7">
          <div>
            <label className="text-xs text-slate-500">Change Type</label>
            <input
              className="w-full border border-slate-300 rounded px-2 py-1 text-sm mt-0.5"
              placeholder="e.g. patch_packages"
              value={step.change_type ?? ""}
              onChange={(e) => onChange({ change_type: e.target.value })}
            />
          </div>
          <div>
            <label className="text-xs text-slate-500">On Failure</label>
            <select
              className="border border-slate-300 rounded px-2 py-1 text-sm ml-2"
              value={step.on_failure}
              onChange={(e) => onChange({ on_failure: e.target.value })}
            >
              <option value="abort">Abort</option>
              <option value="continue">Continue</option>
              <option value="rollback_all">Rollback All</option>
            </select>
          </div>
        </div>
      )}

      {step.type === "condition" && (
        <div className="space-y-2 pl-7">
          <div>
            <label className="text-xs text-slate-500">Expression (Python-safe)</label>
            <input
              className="w-full border border-slate-300 rounded px-2 py-1 text-sm font-mono mt-0.5"
              placeholder="steps[1]['exit_code'] == 0"
              value={step.condition_expr ?? ""}
              onChange={(e) => onChange({ condition_expr: e.target.value })}
            />
          </div>
          <div className="flex gap-4">
            <div>
              <label className="text-xs text-slate-500">If true → step #</label>
              <input
                type="number"
                className="border border-slate-300 rounded px-2 py-1 text-sm ml-1 w-16"
                value={step.on_true_step ?? ""}
                onChange={(e) => onChange({ on_true_step: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">If false → step #</label>
              <input
                type="number"
                className="border border-slate-300 rounded px-2 py-1 text-sm ml-1 w-16"
                value={step.on_false_step ?? ""}
                onChange={(e) => onChange({ on_false_step: Number(e.target.value) })}
              />
            </div>
          </div>
        </div>
      )}

      {step.type === "human_checkpoint" && (
        <div className="space-y-2 pl-7">
          <div>
            <label className="text-xs text-slate-500">Prompt</label>
            <textarea
              className="w-full border border-slate-300 rounded px-2 py-1 text-sm mt-0.5"
              rows={2}
              value={step.prompt ?? ""}
              onChange={(e) => onChange({ prompt: e.target.value })}
            />
          </div>
          <div className="flex gap-4 flex-wrap">
            <div>
              <label className="text-xs text-slate-500">Required Role</label>
              <input
                className="border border-slate-300 rounded px-2 py-1 text-sm ml-1 w-28"
                placeholder="admin"
                value={step.required_role ?? ""}
                onChange={(e) => onChange({ required_role: e.target.value })}
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">Timeout (hours)</label>
              <input
                type="number"
                className="border border-slate-300 rounded px-2 py-1 text-sm ml-1 w-16"
                value={step.timeout_hours ?? ""}
                onChange={(e) => onChange({ timeout_hours: Number(e.target.value) || undefined })}
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">On Timeout</label>
              <select
                className="border border-slate-300 rounded px-2 py-1 text-sm ml-1"
                value={step.on_timeout ?? "abort"}
                onChange={(e) => onChange({ on_timeout: e.target.value })}
              >
                <option value="abort">Abort</option>
                <option value="continue">Continue</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {step.type === "parallel_group" && (
        <div className="pl-7 text-xs text-slate-500 italic">
          Parallel group — add child change steps (parallel_steps) via the API for MVP.
        </div>
      )}
    </div>
  );
}
```

### Step 4: RunbookExecution detail page

Create `frontend/src/pages/RunbookExecution.tsx`:

```tsx
import { useParams, Link } from "react-router-dom";
import {
  CheckCircle, XCircle, Clock, AlertCircle, Loader2,
} from "lucide-react";
import {
  useRunbookExecution,
  useResumeCheckpoint,
  useAbortExecution,
} from "../hooks/useRunbooks";
import type { RunbookStepResultOut } from "../hooks/useRunbooks";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

const STATUS_ICON: Record<string, React.ReactNode> = {
  completed: <CheckCircle className="w-4 h-4 text-green-500" />,
  failed: <XCircle className="w-4 h-4 text-red-500" />,
  running: <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />,
  waiting_human: <AlertCircle className="w-4 h-4 text-amber-500" />,
  pending: <Clock className="w-4 h-4 text-slate-300" />,
  skipped: <Clock className="w-4 h-4 text-slate-300" />,
};

const STATUS_LABEL: Record<string, string> = {
  running: "Running",
  waiting_human: "Awaiting Approval",
  completed: "Completed",
  failed: "Failed",
  rolled_back: "Rolled Back",
};

export function RunbookExecution() {
  const { id } = useParams<{ id: string }>();
  const { data: execution, isLoading } = useRunbookExecution(
    id!,
    /* poll */ ["running", "waiting_human"].includes("running") // will re-evaluate reactively
  );

  // Reactive poll condition
  const shouldPoll = execution
    ? ["running", "waiting_human"].includes(execution.status)
    : false;

  const { data: polledExecution } = useRunbookExecution(id!, shouldPoll);
  const ex = polledExecution ?? execution;

  const resume = useResumeCheckpoint(id!);
  const abort = useAbortExecution(id!);

  if (isLoading || !ex) return <PageLoading />;

  const runbookName = (ex as any).runbook_snapshot?.name ?? "Runbook";

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <PageHeader
        title={`Execution: ${runbookName}`}
        subtitle={
          <span className="flex items-center gap-2 text-sm text-slate-500">
            v{ex.runbook_version} ·{" "}
            <span
              className={`font-medium ${
                ex.status === "completed"
                  ? "text-green-600"
                  : ex.status === "failed"
                  ? "text-red-600"
                  : ex.status === "waiting_human"
                  ? "text-amber-600"
                  : "text-blue-600"
              }`}
            >
              {STATUS_LABEL[ex.status] ?? ex.status}
            </span>
          </span>
        }
        action={
          ["running", "waiting_human"].includes(ex.status) ? (
            <button
              onClick={() => abort.mutate()}
              disabled={abort.isPending}
              className="px-3 py-2 text-sm border border-red-300 text-red-600 rounded hover:bg-red-50 disabled:opacity-50"
            >
              Abort Execution
            </button>
          ) : undefined
        }
      />

      <div className="space-y-3 mt-4">
        {ex.step_results.length === 0 && (
          <p className="text-sm text-slate-500 italic">
            Waiting for first executor tick (up to 30 seconds)…
          </p>
        )}
        {ex.step_results.map((sr) => (
          <StepResultCard
            key={sr.id}
            stepResult={sr}
            executionId={id!}
            onResume={(action) =>
              resume.mutate({ step_number: sr.step_number, action })
            }
            resumePending={resume.isPending}
          />
        ))}
      </div>
    </div>
  );
}

function StepResultCard({
  stepResult: sr,
  executionId,
  onResume,
  resumePending,
}: {
  stepResult: RunbookStepResultOut;
  executionId: string;
  onResume: (action: string) => void;
  resumePending: boolean;
}) {
  const duration =
    sr.started_at && sr.completed_at
      ? `${Math.round(
          (new Date(sr.completed_at).getTime() - new Date(sr.started_at).getTime()) / 1000
        )}s`
      : null;

  return (
    <div className="border border-slate-200 rounded-lg p-4 bg-white">
      <div className="flex items-center gap-2 mb-1">
        {STATUS_ICON[sr.status] ?? <Clock className="w-4 h-4 text-slate-300" />}
        <span className="font-medium text-slate-900 text-sm">
          {sr.step_number}. {sr.step_name}
        </span>
        <span className="text-xs px-1.5 py-0.5 bg-slate-100 text-slate-500 rounded ml-auto">
          {sr.step_type}
        </span>
        {duration && <span className="text-xs text-slate-400">{duration}</span>}
      </div>

      {/* Change request links */}
      {sr.change_request_ids.length > 0 && (
        <div className="pl-6 mt-1">
          {sr.change_request_ids.map((crId) => (
            <Link
              key={crId}
              to={`/change-requests/${crId}`}
              className="text-xs text-brand-600 hover:underline block"
            >
              Change Request {crId.slice(0, 8)}…
            </Link>
          ))}
        </div>
      )}

      {/* Condition result */}
      {sr.step_type === "condition" && sr.result?.evaluated_to !== undefined && (
        <div className="pl-6 mt-1 text-xs text-slate-500">
          Evaluated to:{" "}
          <span className={sr.result.evaluated_to ? "text-green-600" : "text-red-600"}>
            {String(sr.result.evaluated_to)}
          </span>
          {sr.result.jumped_to_step !== undefined && (
            <> → jumped to step {sr.result.jumped_to_step}</>
          )}
        </div>
      )}

      {/* Human checkpoint prompt + actions */}
      {sr.step_type === "human_checkpoint" && sr.status === "waiting_human" && (
        <div className="pl-6 mt-2 p-3 bg-amber-50 border border-amber-200 rounded-md">
          <p className="text-sm text-amber-900 mb-3">
            {(sr as any).prompt ?? "Manual approval required."}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => onResume("resume")}
              disabled={resumePending}
              className="px-3 py-1.5 text-sm bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
            >
              Resume
            </button>
            <button
              onClick={() => onResume("abort")}
              disabled={resumePending}
              className="px-3 py-1.5 text-sm border border-red-300 text-red-600 rounded hover:bg-red-50 disabled:opacity-50"
            >
              Abort
            </button>
          </div>
        </div>
      )}

      {/* Parallel group children */}
      {sr.step_type === "parallel_group" && sr.result?.child_results && (
        <div className="pl-6 mt-1 grid grid-cols-2 gap-1">
          {(sr.result.child_results as { cr_id: string; status: string }[]).map((c) => (
            <Link
              key={c.cr_id}
              to={`/change-requests/${c.cr_id}`}
              className="text-xs text-brand-600 hover:underline"
            >
              {c.cr_id.slice(0, 8)}… ({c.status})
            </Link>
          ))}
        </div>
      )}

      {/* Error */}
      {sr.error_message && (
        <p className="pl-6 mt-1 text-xs text-red-600">{sr.error_message}</p>
      )}
    </div>
  );
}
```

### Step 5: Update Sidebar

In `frontend/src/components/Sidebar.tsx`, add the Runbooks nav item between "Change Requests" and "Approvals Queue":

```typescript
// Add to imports:
import { BookOpen } from "lucide-react";

// Add to navItems array, between Change Requests and Approvals Queue:
{ to: "/runbooks", label: "Runbooks", icon: BookOpen },
```

Full updated `navItems`:
```typescript
const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { to: "/projects", label: "Projects", icon: FolderOpen },
  { to: "/change-requests", label: "Change Requests", icon: FileStack },
  { to: "/runbooks", label: "Runbooks", icon: BookOpen },
  { to: "/approvals", label: "Approvals Queue", icon: CheckSquare },
  { to: "/assets", label: "Asset Inventory", icon: Server },
  { to: "/connectors", label: "Connectors", icon: Plug },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];
```

### Step 6: Update routes

In `frontend/src/routes/index.tsx`, add imports and routes:

```typescript
// Add to imports:
import { Runbooks } from "../pages/Runbooks";
import { RunbookEditor } from "../pages/RunbookEditor";
import { RunbookExecution } from "../pages/RunbookExecution";

// Add inside <Route element={<Layout />}> block:
<Route path="/runbooks" element={<Runbooks />} />
<Route path="/runbooks/new" element={<RunbookEditor />} />
<Route path="/runbooks/:id" element={<RunbookEditor />} />
<Route path="/executions/:id" element={<RunbookExecution />} />
```

- [ ] **Step 7: Verify frontend builds**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Open `http://localhost:3000/runbooks` — verify Runbooks nav item appears and page loads.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/hooks/useRunbooks.ts \
        frontend/src/pages/Runbooks.tsx \
        frontend/src/pages/RunbookEditor.tsx \
        frontend/src/pages/RunbookExecution.tsx \
        frontend/src/components/Sidebar.tsx \
        frontend/src/routes/index.tsx
git commit -m "feat(frontend): composable runbooks — list, editor, execution pages and hooks"
```

---

## Self-Review Checklist

**Spec coverage:**
- Task 1 + 2: Section 1 (data models) + Section 2 (migration)
- Task 3: Section 3 (Pydantic schemas)
- Task 4 + 5: Section 4 (API router) + Section 5 (RunbookService) + Section 6 (RunbookExecutor) + Section 10 (CR source tracking)
- Task 6: Section 8 (seed templates)
- Task 7: Section 9 (frontend pages + hooks + nav)
- Section 7 (notifications): stub wired in executor (`send_checkpoint_notification` imported best-effort); full implementation requires a `runbook_notifications.py` file reusing existing email/notification infrastructure — deferred but hookpoint exists.

**Migration version:** `016` follows highest existing `015_add_connector_id_to_assets.py`.

**Alembic down_revision:** `015` — matches filename of last existing migration.

**CR source tracking:** `source` and `runbook_execution_id` columns added to `change_requests` in the same migration (016). The `ChangeRequest` ORM model will need these two columns added manually (not shown above — executor bridge uses them at insert time; SQLAlchemy will error if the ORM model doesn't declare them). Add to `backend/app/models/change_request.py`:

```python
source: Mapped[str | None] = mapped_column(String(32), nullable=True)
runbook_execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
```

**Placeholder scan:** No TBDs or vague steps. All code is complete and concrete.

**Type consistency:**
- `RunbookStepOut` extends `RunbookStepCreate` with `id` and `runbook_id`; `model_rebuild()` called for forward-reference resolution.
- `useRunbookExecution` hook accepts `poll: boolean` — the execution page derives it reactively from `execution.status`.
- `tick_all_executions` receives `db_factory` as an arg (matches APScheduler `args=[_db_factory]` pattern used by existing scheduler jobs in `scheduler_service.py`).
