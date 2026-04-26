# Projects Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Projects feature — named containers for ordered, dependency-linked change requests — with a list page, a combined create/edit/execute detail page, inline CR creation, and dependency management.

**Architecture:** New `Project` and `ProjectChangeRequest` SQLAlchemy models with a dedicated FastAPI router. The frontend adds a Projects list page and a single `ProjectDetail` component that handles creation, editing, dependency setting, inline CR creation, and execution view depending on project status. Member mutations (add, remove, reorder, set deps) are applied immediately; project-level fields (name, goal, status) are saved explicitly.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 / Alembic / PostgreSQL — React 18 / TypeScript / TanStack Query / React Router v6 / Tailwind CSS / lucide-react

**Test commands:**
- Backend: `docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short"`
- Run migrations: `docker compose exec backend alembic upgrade head`
- Rebuild backend: `docker compose up --build -d backend`
- Frontend hot-reloads at http://localhost:3000

---

### Task 1: Project + ProjectChangeRequest ORM models

**Files:**
- Create: `backend/app/models/project.py`
- Modify: `backend/app/models/__init__.py` (if it exists — check first; if not, skip)

- [ ] **Step 1: Create `backend/app/models/project.py`**

```python
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Enum as SAEnum, JSON, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
import enum

from app.database import Base


class ProjectStatus(str, enum.Enum):
    draft = "draft"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[ProjectStatus] = mapped_column(
        SAEnum(ProjectStatus, name="project_status"), nullable=False, default=ProjectStatus.draft
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    members: Mapped[list["ProjectChangeRequest"]] = relationship(
        "ProjectChangeRequest",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectChangeRequest.sequence_order",
    )


class ProjectChangeRequest(Base):
    __tablename__ = "project_change_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False
    )
    change_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=False
    )
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    depends_on: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    project: Mapped["Project"] = relationship("Project", back_populates="members")
    change_request: Mapped["ChangeRequest"] = relationship("ChangeRequest")
```

- [ ] **Step 2: Verify the file is importable**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -c 'from app.models.project import Project, ProjectChangeRequest, ProjectStatus; print(\"OK\")'"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/project.py
git commit -m "feat: add Project and ProjectChangeRequest ORM models"
```

---

### Task 2: Project Pydantic schemas + tests

**Files:**
- Create: `backend/app/schemas/project.py`
- Create: `backend/app/tests/test_project_schemas.py`

- [ ] **Step 1: Write failing tests**

`backend/app/tests/test_project_schemas.py`:
```python
import uuid
import pytest
from pydantic import ValidationError
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectMemberCreate, ProjectMemberUpdate
from app.models.project import ProjectStatus


def test_project_create_requires_name():
    with pytest.raises(ValidationError):
        ProjectCreate()


def test_project_create_defaults():
    p = ProjectCreate(name="Test Project")
    assert p.name == "Test Project"
    assert p.description == ""
    assert p.goal == ""


def test_project_update_all_optional():
    u = ProjectUpdate()
    assert u.name is None
    assert u.status is None


def test_project_update_status_validates():
    u = ProjectUpdate(status=ProjectStatus.in_progress)
    assert u.status == ProjectStatus.in_progress


def test_project_update_invalid_status():
    with pytest.raises(ValidationError):
        ProjectUpdate(status="not_a_status")


def test_member_create_defaults():
    cr_id = uuid.uuid4()
    m = ProjectMemberCreate(change_request_id=cr_id)
    assert m.sequence_order == 0
    assert m.depends_on == []


def test_member_update_all_optional():
    u = ProjectMemberUpdate()
    assert u.sequence_order is None
    assert u.depends_on is None


def test_member_create_depends_on_accepts_uuid_list():
    pcr_id = uuid.uuid4()
    m = ProjectMemberCreate(change_request_id=uuid.uuid4(), depends_on=[pcr_id])
    assert m.depends_on == [pcr_id]
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/test_project_schemas.py -v" 2>&1 | tail -10
```

Expected: `ImportError` — `app.schemas.project` does not exist yet.

- [ ] **Step 3: Create `backend/app/schemas/project.py`**

```python
import uuid
from datetime import datetime
from pydantic import BaseModel
from app.models.project import ProjectStatus
from app.schemas.change_request import ChangeRequestSummary


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    goal: str = ""


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    goal: str | None = None
    status: ProjectStatus | None = None


class ProjectRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    created_by: uuid.UUID
    name: str
    description: str
    goal: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class ProjectSummary(ProjectRead):
    member_count: int = 0
    completed_count: int = 0


class ProjectMemberCreate(BaseModel):
    change_request_id: uuid.UUID
    sequence_order: int = 0
    depends_on: list[uuid.UUID] = []


class ProjectMemberUpdate(BaseModel):
    sequence_order: int | None = None
    depends_on: list[uuid.UUID] | None = None


class ProjectMemberRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    project_id: uuid.UUID
    change_request_id: uuid.UUID
    sequence_order: int
    depends_on: list[uuid.UUID]
    change_request: ChangeRequestSummary
    eligible: bool = False


class ProjectDetailRead(ProjectRead):
    members: list[ProjectMemberRead] = []
```

- [ ] **Step 4: Run tests — expect 8 PASSED**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/test_project_schemas.py -v"
```

Expected: 8 tests PASSED.

- [ ] **Step 5: Run full test suite — expect 89 PASSED**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short" 2>&1 | tail -5
```

Expected: 89 passed (81 + 8 new).

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/project.py backend/app/tests/test_project_schemas.py
git commit -m "feat: add project schemas and tests"
```

---

### Task 3: Alembic migration — add projects tables

**Files:**
- Create: `backend/alembic/versions/003_add_projects.py`

- [ ] **Step 1: Create the migration file**

```python
# backend/alembic/versions/003_add_projects.py
"""add projects tables

Revision ID: 003
Revises: 002
Create Date: 2026-04-26 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("goal", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.Enum("draft", "in_progress", "completed", "cancelled", name="project_status"),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"])

    op.create_table(
        "project_change_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), nullable=False
        ),
        sa.Column("sequence_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("depends_on", sa.JSON, nullable=False, server_default="[]"),
    )
    op.create_index(
        "ix_project_change_requests_project_id", "project_change_requests", ["project_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_project_change_requests_project_id")
    op.drop_table("project_change_requests")
    op.drop_index("ix_projects_organization_id")
    op.drop_table("projects")
    op.execute("DROP TYPE IF EXISTS project_status")
```

- [ ] **Step 2: Run the migration**

```bash
docker compose exec backend alembic upgrade head
```

Expected output ends with: `Running upgrade 002 -> 003, add projects tables`

- [ ] **Step 3: Verify tables exist**

```bash
docker compose exec db psql -U nexplane -d nexplane -c "\dt" | grep project
```

Expected: `projects` and `project_change_requests` appear.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic/versions/003_add_projects.py
git commit -m "feat: add projects migration"
```

---

### Task 4: Projects router

**Files:**
- Create: `backend/app/routers/projects.py`

- [ ] **Step 1: Create `backend/app/routers/projects.py`**

```python
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.project import Project, ProjectChangeRequest, ProjectStatus
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.user import User
from app.routers import current_user
from app.schemas.project import (
    ProjectCreate, ProjectUpdate, ProjectRead, ProjectSummary,
    ProjectMemberCreate, ProjectMemberUpdate, ProjectMemberRead, ProjectDetailRead,
)
from app.services.audit_service import record_event

router = APIRouter(prefix="/projects", tags=["Projects"])

_MEMBER_OPTIONS = [
    selectinload(ProjectChangeRequest.change_request).selectinload(
        ChangeRequest.requester
    )
]


def _normalize_uuid(val) -> uuid.UUID:
    return uuid.UUID(val) if isinstance(val, str) else val


def _detect_cycle(
    target_pcr_id: uuid.UUID,
    proposed_depends_on: list[uuid.UUID],
    all_members: list[ProjectChangeRequest],
) -> bool:
    """Returns True if the proposed deps would create a dependency cycle."""
    deps_map: dict[uuid.UUID, list[uuid.UUID]] = {}
    for m in all_members:
        raw = m.depends_on or []
        deps = [_normalize_uuid(d) for d in raw]
        if m.id == target_pcr_id:
            deps_map[m.id] = [_normalize_uuid(d) for d in proposed_depends_on]
        else:
            deps_map[m.id] = deps

    def dfs(node: uuid.UUID, visited: set) -> bool:
        if node == target_pcr_id:
            return True
        if node in visited:
            return False
        visited.add(node)
        return any(dfs(dep, visited) for dep in deps_map.get(node, []))

    return any(dfs(dep, set()) for dep in proposed_depends_on)


def _compute_eligible(pcr: ProjectChangeRequest, all_members: list[ProjectChangeRequest]) -> bool:
    if not pcr.depends_on:
        return True
    pcr_map = {m.id: m for m in all_members}
    for dep_raw in pcr.depends_on:
        dep_id = _normalize_uuid(dep_raw)
        dep = pcr_map.get(dep_id)
        if not dep or dep.change_request.status != ChangeRequestStatus.completed:
            return False
    return True


def _build_member_read(pcr: ProjectChangeRequest, all_members: list[ProjectChangeRequest]) -> dict:
    return {
        "id": pcr.id,
        "project_id": pcr.project_id,
        "change_request_id": pcr.change_request_id,
        "sequence_order": pcr.sequence_order,
        "depends_on": [_normalize_uuid(d) for d in (pcr.depends_on or [])],
        "change_request": pcr.change_request,
        "eligible": _compute_eligible(pcr, all_members),
    }


async def _get_project(db: AsyncSession, project_id: uuid.UUID, org_id: uuid.UUID) -> Project:
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id, Project.organization_id == org_id)
        .options(selectinload(Project.members).options(*_MEMBER_OPTIONS))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("", response_model=list[ProjectSummary])
async def list_projects(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project)
        .where(Project.organization_id == user.organization_id)
        .options(selectinload(Project.members).options(*_MEMBER_OPTIONS))
        .order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()
    out = []
    for p in projects:
        total = len(p.members)
        completed = sum(
            1 for m in p.members if m.change_request.status == ChangeRequestStatus.completed
        )
        out.append(ProjectSummary(
            **ProjectRead.model_validate(p).model_dump(),
            member_count=total,
            completed_count=completed,
        ))
    return out


@router.post("", response_model=ProjectRead, status_code=201)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = Project(
        organization_id=user.organization_id,
        created_by=user.id,
        **body.model_dump(),
    )
    db.add(project)
    await db.flush()
    await record_event(
        db, user.organization_id, "project.created",
        {"project_id": str(project.id), "name": project.name},
        actor_id=user.id,
    )
    await db.commit()
    await db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectDetailRead)
async def get_project(
    project_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)
    members_read = [_build_member_read(m, project.members) for m in project.members]
    return ProjectDetailRead(
        **ProjectRead.model_validate(project).model_dump(),
        members=members_read,
    )


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.goal is not None:
        project.goal = body.goal
    if body.status is not None:
        project.status = body.status
    project.updated_at = datetime.now(timezone.utc)
    await record_event(
        db, user.organization_id, "project.updated",
        {"project_id": str(project.id), "changes": body.model_dump(exclude_none=True)},
        actor_id=user.id,
    )
    await db.commit()
    await db.refresh(project)
    return project


@router.post("/{project_id}/members", response_model=ProjectMemberRead, status_code=201)
async def add_project_member(
    project_id: uuid.UUID,
    body: ProjectMemberCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)

    if project.status != ProjectStatus.draft:
        raise HTTPException(status_code=409, detail="Members can only be added to draft projects")

    # Verify CR belongs to org
    cr = await db.get(ChangeRequest, body.change_request_id)
    if not cr or cr.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Change request not found")

    # Cycle detection
    if body.depends_on and _detect_cycle(uuid.uuid4(), body.depends_on, project.members):
        raise HTTPException(status_code=400, detail="Dependency would create a cycle")

    pcr = ProjectChangeRequest(
        project_id=project_id,
        change_request_id=body.change_request_id,
        sequence_order=body.sequence_order or len(project.members),
        depends_on=[str(d) for d in body.depends_on],
    )
    db.add(pcr)
    await db.flush()

    # Reload with CR relationship
    result = await db.execute(
        select(ProjectChangeRequest)
        .where(ProjectChangeRequest.id == pcr.id)
        .options(*_MEMBER_OPTIONS)
    )
    pcr = result.scalar_one()

    await record_event(
        db, user.organization_id, "project.member_added",
        {"project_id": str(project_id), "change_request_id": str(body.change_request_id)},
        actor_id=user.id,
    )
    await db.commit()
    # Reload all members for eligibility
    project = await _get_project(db, project_id, user.organization_id)
    return _build_member_read(
        next(m for m in project.members if m.id == pcr.id),
        project.members,
    )


@router.delete("/{project_id}/members/{pcr_id}", status_code=204)
async def remove_project_member(
    project_id: uuid.UUID,
    pcr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)

    if project.status != ProjectStatus.draft:
        raise HTTPException(status_code=409, detail="Members can only be removed from draft projects")

    pcr = next((m for m in project.members if m.id == pcr_id), None)
    if not pcr:
        raise HTTPException(status_code=404, detail="Project member not found")

    await db.delete(pcr)
    await record_event(
        db, user.organization_id, "project.member_removed",
        {"project_id": str(project_id), "pcr_id": str(pcr_id)},
        actor_id=user.id,
    )
    await db.commit()


@router.patch("/{project_id}/members/{pcr_id}", response_model=ProjectMemberRead)
async def update_project_member(
    project_id: uuid.UUID,
    pcr_id: uuid.UUID,
    body: ProjectMemberUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project(db, project_id, user.organization_id)

    if project.status not in (ProjectStatus.draft, ProjectStatus.in_progress):
        raise HTTPException(status_code=409, detail="Members can only be updated in draft or in-progress projects")

    pcr = next((m for m in project.members if m.id == pcr_id), None)
    if not pcr:
        raise HTTPException(status_code=404, detail="Project member not found")

    if body.depends_on is not None:
        new_deps = [_normalize_uuid(d) for d in body.depends_on]
        if _detect_cycle(pcr_id, new_deps, project.members):
            raise HTTPException(status_code=400, detail="Dependency would create a cycle")
        pcr.depends_on = [str(d) for d in new_deps]

    if body.sequence_order is not None:
        pcr.sequence_order = body.sequence_order

    await db.commit()
    project = await _get_project(db, project_id, user.organization_id)
    return _build_member_read(
        next(m for m in project.members if m.id == pcr_id),
        project.members,
    )
```

- [ ] **Step 2: Run full test suite (no regressions)**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short" 2>&1 | tail -5
```

Expected: 89 passed.

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/projects.py
git commit -m "feat: add projects router with CRUD and member management"
```

---

### Task 5: Register projects router in main.py + rebuild

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add projects router import and registration**

In `backend/app/main.py`, add `projects` to the router import line and add `app.include_router(projects.router)`:

```python
from app.routers import auth, assets, connectors, change_requests, audit, projects
```

Add after `app.include_router(change_requests.router)`:
```python
app.include_router(projects.router)
```

- [ ] **Step 2: Rebuild backend and smoke-test**

```bash
docker compose up --build -d backend
```

Wait 8 seconds, then:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login -H 'Content-Type: application/json' -d '{"email":"operator@acme.example","password":"operator123"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/projects | python3 -c "import sys,json; print(f'Projects: {json.load(sys.stdin)}')"
```

Expected: `Projects: []` (empty list, no error).

Then create a project:
```bash
curl -s -X POST http://localhost:8000/projects \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Project", "goal": "Test the API"}' | python3 -c "import sys,json; p=json.load(sys.stdin); print(f'Created: {p[\"name\"]} status={p[\"status\"]}')"
```

Expected: `Created: Test Project status=draft`

- [ ] **Step 3: Run full test suite**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short" 2>&1 | tail -5
```

Expected: 89 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat: register projects router in FastAPI app"
```

---

### Task 6: Frontend types

**Files:**
- Modify: `frontend/src/types/api.ts`

- [ ] **Step 1: Add project types to `frontend/src/types/api.ts`**

Append these types after the `AssetListParams` / `BulkTagBody` block and before `ConnectorType`:

```typescript
export type ProjectStatus = "draft" | "in_progress" | "completed" | "cancelled";

export interface Project {
  id: string;
  organization_id: string;
  created_by: string;
  name: string;
  description: string;
  goal: string;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
}

export interface ProjectSummary extends Project {
  member_count: number;
  completed_count: number;
}

export interface ProjectMember {
  id: string;
  project_id: string;
  change_request_id: string;
  sequence_order: number;
  depends_on: string[];
  change_request: ChangeRequestSummary;
  eligible: boolean;
}

export interface ProjectDetail extends Project {
  members: ProjectMember[];
}

export interface ProjectCreate {
  name: string;
  description?: string;
  goal?: string;
}

export interface ProjectUpdate {
  name?: string;
  description?: string;
  goal?: string;
  status?: ProjectStatus;
}

export interface ProjectMemberCreate {
  change_request_id: string;
  sequence_order?: number;
  depends_on?: string[];
}

export interface ProjectMemberUpdate {
  sequence_order?: number;
  depends_on?: string[];
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/types/api.ts
git commit -m "feat: add project types to frontend API types"
```

---

### Task 7: Frontend API client

**Files:**
- Modify: `frontend/src/api/endpoints.ts`

- [ ] **Step 1: Add project type imports and `projectsApi` to `frontend/src/api/endpoints.ts`**

Add to the import line at the top:
```typescript
import type {
  Token, User, Asset, AssetCreate, AssetUpdate, AssetListParams, BulkTagBody,
  Project, ProjectSummary, ProjectDetail, ProjectMember,
  ProjectCreate, ProjectUpdate, ProjectMemberCreate, ProjectMemberUpdate,
  Connector, ConnectorCreate, ConnectorTestResult,
  ChangeRequest, ChangeRequestSummary, ChangeRequestCreate,
  ChangePlan, Approval, ApprovalCreate, ExecutionRun, AuditEvent,
} from "../types/api";
```

Add `projectsApi` before the `// Connectors` section:

```typescript
// Projects
export const projectsApi = {
  list: () =>
    apiClient.get<ProjectSummary[]>("/projects").then((r) => r.data),
  get: (id: string) =>
    apiClient.get<ProjectDetail>(`/projects/${id}`).then((r) => r.data),
  create: (data: ProjectCreate) =>
    apiClient.post<Project>("/projects", data).then((r) => r.data),
  update: (id: string, data: ProjectUpdate) =>
    apiClient.patch<Project>(`/projects/${id}`, data).then((r) => r.data),
  addMember: (id: string, data: ProjectMemberCreate) =>
    apiClient.post<ProjectMember>(`/projects/${id}/members`, data).then((r) => r.data),
  removeMember: (id: string, pcrId: string) =>
    apiClient.delete(`/projects/${id}/members/${pcrId}`),
  updateMember: (id: string, pcrId: string, data: ProjectMemberUpdate) =>
    apiClient.patch<ProjectMember>(`/projects/${id}/members/${pcrId}`, data).then((r) => r.data),
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/api/endpoints.ts
git commit -m "feat: add projectsApi to frontend API client"
```

---

### Task 8: Projects list page

**Files:**
- Create: `frontend/src/pages/Projects.tsx`

- [ ] **Step 1: Create `frontend/src/pages/Projects.tsx`**

```tsx
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Plus, FolderOpen } from "lucide-react";
import { projectsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import { StatusBadge } from "../components/StatusBadge";

const STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  in_progress: "In Progress",
  completed: "Completed",
  cancelled: "Cancelled",
};

export function Projects() {
  const navigate = useNavigate();

  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: () => projectsApi.list(),
  });

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-8">
      <PageHeader
        title="Projects"
        subtitle={`${projects?.length ?? 0} projects`}
        actions={
          <button
            onClick={() => navigate("/projects/new")}
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
          >
            <Plus className="w-4 h-4" />
            New Project
          </button>
        }
      />

      {projects?.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <FolderOpen className="w-12 h-12 text-slate-300 mb-4" />
          <p className="text-slate-500 text-sm">No projects yet.</p>
          <button
            onClick={() => navigate("/projects/new")}
            className="mt-4 text-brand-600 text-sm hover:underline"
          >
            Create your first project
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {(projects ?? []).map((project) => {
            const pct =
              project.member_count > 0
                ? Math.round((project.completed_count / project.member_count) * 100)
                : 0;
            return (
              <button
                key={project.id}
                onClick={() => navigate(`/projects/${project.id}`)}
                className="text-left bg-white border border-slate-200 rounded-lg p-5 hover:border-brand-300 transition-colors"
              >
                <div className="flex items-start justify-between mb-2">
                  <h3 className="text-sm font-semibold text-slate-900 truncate pr-2">
                    {project.name}
                  </h3>
                  <StatusBadge status={project.status} size="sm" />
                </div>
                {project.goal && (
                  <p className="text-xs text-slate-500 mb-3 line-clamp-2">{project.goal}</p>
                )}
                <div className="mt-auto">
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>{project.completed_count} / {project.member_count} change requests</span>
                    <span>{pct}%</span>
                  </div>
                  <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-brand-500 rounded-full transition-all"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <div className="text-xs text-slate-400 mt-2">
                    {new Date(project.created_at).toLocaleDateString()}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/Projects.tsx
git commit -m "feat: add Projects list page"
```

---

### Task 9: ProjectDetail page

**Files:**
- Create: `frontend/src/pages/ProjectDetail.tsx`

This is the main page. It handles create mode (`/projects/new`), edit mode (draft projects), and execution mode (in-progress projects) all in one component.

- [ ] **Step 1: Create `frontend/src/pages/ProjectDetail.tsx`**

```tsx
import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Plus, X, ChevronUp, ChevronDown, Search, Play, ExternalLink,
} from "lucide-react";
import { projectsApi } from "../api/endpoints";
import { changeRequestsApi } from "../api/endpoints";
import { assetsApi } from "../api/endpoints";
import { PageLoading } from "../components/LoadingSpinner";
import { StatusBadge } from "../components/StatusBadge";
import { RiskBadge } from "../components/RiskBadge";
import type {
  ProjectDetail, ProjectMember, ProjectStatus,
  ChangeType, AssetType, Environment, Criticality,
} from "../types/api";

const STATUS_OPTIONS: ProjectStatus[] = ["draft", "in_progress", "completed", "cancelled"];

const CHANGE_TYPES: ChangeType[] = [
  "dns_update", "snapshot_asset", "security_group_update",
  "key_rotation", "telemetry_agent_deploy", "remote_command", "microsegmentation_policy",
];

// ── Helpers ────────────────────────────────────────────────────────────────

function statusColor(status: string): string {
  const map: Record<string, string> = {
    draft: "text-slate-400",
    in_progress: "text-brand-600",
    completed: "text-emerald-600",
    cancelled: "text-slate-300",
  };
  return map[status] ?? "text-slate-400";
}

function crActionLabel(member: ProjectMember, isDraft: boolean): string {
  if (isDraft) return "View →";
  const s = member.change_request.status;
  if (s === "draft") return "Generate Plan";
  if (s === "planned") return "Submit for Approval";
  if (s === "awaiting_approval") return "View Approvals";
  if (s === "approved" && member.eligible) return "Execute";
  if (["executing", "verifying"].includes(s)) return "Executing…";
  if (s === "completed") return "Completed ✓";
  return "—";
}

function crActionDisabled(member: ProjectMember, isDraft: boolean): boolean {
  if (isDraft) return false;
  const s = member.change_request.status;
  return (
    ["executing", "verifying", "completed", "rolled_back", "failed"].includes(s) ||
    (s === "approved" && !member.eligible)
  );
}

// ── Main component ─────────────────────────────────────────────────────────

export function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const isNew = id === "new";
  const navigate = useNavigate();
  const qc = useQueryClient();

  // Header fields (saved explicitly)
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [status, setStatus] = useState<ProjectStatus>("draft");
  const [headerDirty, setHeaderDirty] = useState(false);

  // UI state
  const [showAddExisting, setShowAddExisting] = useState(false);
  const [addSearch, setAddSearch] = useState("");
  const [showNewCrForm, setShowNewCrForm] = useState(false);
  const [depsPopoverFor, setDepsPopoverFor] = useState<string | null>(null);

  // New CR form state
  const [newCrTitle, setNewCrTitle] = useState("");
  const [newCrType, setNewCrType] = useState<ChangeType>("dns_update");
  const [newCrAssets, setNewCrAssets] = useState<string[]>([]);
  const [newCrOutcome, setNewCrOutcome] = useState("{}");
  const [newCrJsonError, setNewCrJsonError] = useState("");

  // ── Data fetching ──

  const { data: project, isLoading } = useQuery({
    queryKey: ["project", id],
    queryFn: () => projectsApi.get(id!),
    enabled: !isNew && !!id,
    refetchInterval: (data) => {
      if (!data) return false;
      const active = data.members.some((m) =>
        ["executing", "verifying"].includes(m.change_request.status)
      );
      return active ? 5000 : false;
    },
  });

  const { data: allCRs } = useQuery({
    queryKey: ["change-requests"],
    queryFn: () => changeRequestsApi.list(),
    enabled: !isNew,
  });

  const { data: assets } = useQuery({
    queryKey: ["assets"],
    queryFn: () => assetsApi.list(),
    enabled: showNewCrForm,
  });

  // Sync project data to header fields on load
  useEffect(() => {
    if (project) {
      setName(project.name);
      setGoal(project.goal);
      setStatus(project.status);
      setHeaderDirty(false);
    }
  }, [project?.id]);

  const isDraft = !isNew && project?.status === "draft";
  const isInProgress = project?.status === "in_progress";

  // ── Mutations ──

  const createProject = useMutation({
    mutationFn: () => projectsApi.create({ name: name.trim(), goal: goal.trim() }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/projects/${p.id}`, { replace: true });
    },
  });

  const updateProject = useMutation({
    mutationFn: () => projectsApi.update(id!, { name: name.trim(), goal: goal.trim(), status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["project", id] });
      setHeaderDirty(false);
    },
  });

  const addMember = useMutation({
    mutationFn: (crId: string) =>
      projectsApi.addMember(id!, {
        change_request_id: crId,
        sequence_order: project?.members.length ?? 0,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", id] });
      setShowAddExisting(false);
      setAddSearch("");
    },
  });

  const removeMember = useMutation({
    mutationFn: (pcrId: string) => projectsApi.removeMember(id!, pcrId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  const reorderMember = useMutation({
    mutationFn: ({ pcrId, order }: { pcrId: string; order: number }) =>
      projectsApi.updateMember(id!, pcrId, { sequence_order: order }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  const setDeps = useMutation({
    mutationFn: ({ pcrId, deps }: { pcrId: string; deps: string[] }) =>
      projectsApi.updateMember(id!, pcrId, { depends_on: deps }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", id] });
      setDepsPopoverFor(null);
    },
  });

  const createAndAddCr = useMutation({
    mutationFn: async () => {
      let outcome: Record<string, unknown>;
      try {
        outcome = JSON.parse(newCrOutcome);
      } catch {
        setNewCrJsonError("Invalid JSON");
        throw new Error("Invalid JSON");
      }
      const cr = await changeRequestsApi.create({
        title: newCrTitle.trim(),
        change_type: newCrType,
        target_asset_ids: newCrAssets,
        desired_outcome: outcome,
      });
      await projectsApi.addMember(id!, {
        change_request_id: cr.id,
        sequence_order: project?.members.length ?? 0,
      });
      return cr;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", id] });
      qc.invalidateQueries({ queryKey: ["change-requests"] });
      setShowNewCrForm(false);
      setNewCrTitle("");
      setNewCrOutcome("{}");
      setNewCrJsonError("");
    },
  });

  const executeCr = useMutation({
    mutationFn: (crId: string) => changeRequestsApi.execute(crId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  const submitCr = useMutation({
    mutationFn: (crId: string) => changeRequestsApi.submitForApproval(crId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  // ── Computed ──

  const existingCrIds = new Set(project?.members.map((m) => m.change_request_id) ?? []);
  const availableCRs = (allCRs ?? []).filter(
    (cr) => !existingCrIds.has(cr.id) &&
      (addSearch === "" || cr.title.toLowerCase().includes(addSearch.toLowerCase()))
  );

  // ── Render ──

  if (!isNew && isLoading) return <PageLoading />;

  const members = project?.members ?? [];

  function handleSave() {
    if (isNew) {
      if (name.trim()) createProject.mutate();
    } else {
      updateProject.mutate();
    }
  }

  function handleMoveUp(member: ProjectMember, idx: number) {
    if (idx === 0) return;
    const prev = members[idx - 1];
    reorderMember.mutate({ pcrId: member.id, order: prev.sequence_order });
    reorderMember.mutate({ pcrId: prev.id, order: member.sequence_order });
  }

  function handleMoveDown(member: ProjectMember, idx: number) {
    if (idx === members.length - 1) return;
    const next = members[idx + 1];
    reorderMember.mutate({ pcrId: member.id, order: next.sequence_order });
    reorderMember.mutate({ pcrId: next.id, order: member.sequence_order });
  }

  function handleCrAction(member: ProjectMember) {
    const s = member.change_request.status;
    if (s === "draft" || isDraft) {
      navigate(`/change-requests/${member.change_request_id}`);
    } else if (s === "planned") {
      submitCr.mutate(member.change_request_id);
    } else if (s === "awaiting_approval") {
      navigate(`/change-requests/${member.change_request_id}`);
    } else if (s === "approved" && member.eligible) {
      executeCr.mutate(member.change_request_id);
    } else {
      navigate(`/change-requests/${member.change_request_id}`);
    }
  }

  const completedCount = members.filter((m) => m.change_request.status === "completed").length;
  const executingCount = members.filter((m) =>
    ["executing", "verifying"].includes(m.change_request.status)
  ).length;
  const blockedCount = members.filter(
    (m) => !m.eligible && !["completed", "executing", "verifying"].includes(m.change_request.status)
  ).length;
  const pendingCount = members.length - completedCount - executingCount - blockedCount;

  return (
    <div className="p-8 max-w-4xl">
      {/* Header */}
      <div className="flex items-start gap-3 mb-6">
        <button
          onClick={() => navigate("/projects")}
          className="mt-1 p-1.5 text-slate-400 hover:text-slate-600 rounded hover:bg-slate-100 shrink-0"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className="flex-1 min-w-0">
          <input
            value={name}
            onChange={(e) => { setName(e.target.value); setHeaderDirty(true); }}
            placeholder="Project name…"
            className="w-full text-xl font-semibold text-slate-900 bg-transparent border-b border-transparent hover:border-slate-200 focus:border-brand-400 focus:outline-none pb-0.5 mb-1"
          />
          <input
            value={goal}
            onChange={(e) => { setGoal(e.target.value); setHeaderDirty(true); }}
            placeholder="Describe the goal of this project…"
            className="w-full text-sm text-slate-500 bg-transparent border-b border-transparent hover:border-slate-200 focus:border-brand-400 focus:outline-none pb-0.5"
          />
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!isNew && (
            <select
              value={status}
              onChange={(e) => { setStatus(e.target.value as ProjectStatus); setHeaderDirty(true); }}
              className="text-sm border border-slate-200 rounded-md px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{s.replace("_", " ")}</option>
              ))}
            </select>
          )}
          <button
            onClick={handleSave}
            disabled={!name.trim() || (!headerDirty && !isNew) || createProject.isPending || updateProject.isPending}
            className="px-3 py-1.5 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700 disabled:opacity-40"
          >
            {isNew ? (createProject.isPending ? "Creating…" : "Create Project") : (updateProject.isPending ? "Saving…" : "Save")}
          </button>
        </div>
      </div>

      {/* Only show member list after project exists */}
      {!isNew && (
        <>
          {/* Status summary bar (execution mode) */}
          {isInProgress && members.length > 0 && (
            <div className="flex gap-4 mb-4 text-sm">
              <span className="text-emerald-600">● {completedCount} completed</span>
              <span className="text-brand-600">⟳ {executingCount} executing</span>
              <span className="text-amber-500">⏳ {blockedCount} blocked</span>
              <span className="text-slate-400">○ {pendingCount} pending</span>
            </div>
          )}

          {/* Member list */}
          <div className="bg-white border border-slate-200 rounded-lg divide-y divide-slate-100">
            {members.length === 0 && (
              <div className="p-8 text-center text-slate-400 text-sm">
                No change requests yet. Add existing ones or create new ones below.
              </div>
            )}

            {members.map((member, idx) => (
              <div key={member.id} className="px-4 py-3">
                <div className="flex items-center gap-3">
                  {/* Reorder (draft only) */}
                  {isDraft && (
                    <div className="flex flex-col gap-0.5 shrink-0">
                      <button
                        onClick={() => handleMoveUp(member, idx)}
                        disabled={idx === 0}
                        className="text-slate-300 hover:text-slate-500 disabled:opacity-20"
                      >
                        <ChevronUp className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => handleMoveDown(member, idx)}
                        disabled={idx === members.length - 1}
                        className="text-slate-300 hover:text-slate-500 disabled:opacity-20"
                      >
                        <ChevronDown className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}

                  {/* Sequence number */}
                  <span className="text-xs text-slate-400 w-5 shrink-0 text-right">
                    {idx + 1}
                  </span>

                  {/* CR info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-900 truncate">
                        {member.change_request.title}
                      </span>
                      <span className="text-xs text-slate-400 shrink-0">
                        [{member.change_request.change_type.replace(/_/g, " ")}]
                      </span>
                      <StatusBadge status={member.change_request.status} size="sm" />
                    </div>

                    {/* Dependency line */}
                    <div className="text-xs mt-0.5">
                      {member.depends_on.length === 0 ? (
                        <span className="text-slate-300">Depends on: —</span>
                      ) : member.eligible ? (
                        <span className="text-emerald-500">✓ Dependencies met</span>
                      ) : (
                        <span className="text-amber-500">
                          ⏳ Waiting on:{" "}
                          {member.depends_on
                            .map((depId) => {
                              const dep = members.find((m) => m.id === depId);
                              return dep ? dep.change_request.title : depId.slice(0, 8);
                            })
                            .join(", ")}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 shrink-0">
                    {/* Set deps (draft and in_progress) */}
                    {(isDraft || isInProgress) && (
                      <div className="relative">
                        <button
                          onClick={() =>
                            setDepsPopoverFor(depsPopoverFor === member.id ? null : member.id)
                          }
                          className="text-xs text-slate-400 hover:text-slate-600 px-2 py-1 border border-slate-200 rounded hover:bg-slate-50"
                        >
                          Set deps ▾
                        </button>
                        {depsPopoverFor === member.id && (
                          <div className="absolute right-0 top-8 z-20 bg-white border border-slate-200 rounded-lg shadow-lg p-3 w-64">
                            <p className="text-xs text-slate-500 mb-2">
                              This CR depends on:
                            </p>
                            <div className="space-y-1 max-h-48 overflow-y-auto">
                              {members
                                .filter((m) => m.id !== member.id)
                                .map((other) => {
                                  const checked = member.depends_on.includes(other.id);
                                  return (
                                    <label
                                      key={other.id}
                                      className="flex items-center gap-2 text-xs cursor-pointer hover:bg-slate-50 p-1 rounded"
                                    >
                                      <input
                                        type="checkbox"
                                        checked={checked}
                                        onChange={() => {
                                          const current = member.depends_on;
                                          const next = checked
                                            ? current.filter((d) => d !== other.id)
                                            : [...current, other.id];
                                          setDeps.mutate({ pcrId: member.id, deps: next });
                                        }}
                                        className="rounded border-slate-300 text-brand-600"
                                      />
                                      <span className="truncate">{other.change_request.title}</span>
                                    </label>
                                  );
                                })}
                            </div>
                            <button
                              onClick={() => setDepsPopoverFor(null)}
                              className="mt-2 text-xs text-slate-400 hover:text-slate-600"
                            >
                              Close
                            </button>
                          </div>
                        )}
                      </div>
                    )}

                    {/* CR action button */}
                    <button
                      onClick={() => handleCrAction(member)}
                      disabled={crActionDisabled(member, isDraft ?? false)}
                      className={`text-xs px-2.5 py-1 rounded font-medium transition-colors ${
                        member.change_request.status === "approved" && member.eligible && !isDraft
                          ? "bg-brand-600 text-white hover:bg-brand-700"
                          : member.change_request.status === "completed"
                          ? "text-emerald-600 bg-emerald-50 cursor-default"
                          : "text-slate-600 border border-slate-200 hover:bg-slate-50 disabled:opacity-40"
                      }`}
                    >
                      {crActionLabel(member, isDraft ?? false)}
                    </button>

                    {/* Remove (draft only) */}
                    {isDraft && (
                      <button
                        onClick={() => removeMember.mutate(member.id)}
                        className="text-slate-300 hover:text-red-400 p-1"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}

            {/* Add controls (draft only) */}
            {isDraft && (
              <div className="px-4 py-3 flex gap-2 bg-slate-50">
                {/* Add existing CR */}
                <div className="relative">
                  <button
                    onClick={() => { setShowAddExisting(!showAddExisting); setShowNewCrForm(false); }}
                    className="inline-flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-900 px-3 py-1.5 border border-slate-200 rounded-md hover:bg-white"
                  >
                    <Search className="w-3.5 h-3.5" />
                    Add existing CR
                  </button>
                  {showAddExisting && (
                    <div className="absolute left-0 top-9 z-20 bg-white border border-slate-200 rounded-lg shadow-lg w-80">
                      <div className="p-2 border-b border-slate-100">
                        <input
                          autoFocus
                          value={addSearch}
                          onChange={(e) => setAddSearch(e.target.value)}
                          placeholder="Search change requests…"
                          className="w-full text-sm px-2 py-1 focus:outline-none"
                        />
                      </div>
                      <div className="max-h-56 overflow-y-auto">
                        {availableCRs.length === 0 && (
                          <div className="p-3 text-xs text-slate-400 text-center">No results</div>
                        )}
                        {availableCRs.map((cr) => (
                          <button
                            key={cr.id}
                            onClick={() => addMember.mutate(cr.id)}
                            className="w-full text-left px-3 py-2 hover:bg-slate-50 text-sm"
                          >
                            <div className="font-medium text-slate-900 truncate">{cr.title}</div>
                            <div className="text-xs text-slate-400">
                              {cr.change_type.replace(/_/g, " ")} · {cr.status}
                            </div>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Create new CR */}
                <button
                  onClick={() => { setShowNewCrForm(!showNewCrForm); setShowAddExisting(false); }}
                  className="inline-flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-900 px-3 py-1.5 border border-slate-200 rounded-md hover:bg-white"
                >
                  <Plus className="w-3.5 h-3.5" />
                  Create new CR
                </button>
              </div>
            )}
          </div>

          {/* Inline new CR form */}
          {showNewCrForm && isDraft && (
            <div className="mt-3 bg-white border border-slate-200 rounded-lg p-5">
              <h3 className="text-sm font-semibold text-slate-900 mb-4">Create & Add Change Request</h3>
              <div className="grid grid-cols-2 gap-3 mb-3">
                <div className="col-span-2">
                  <label className="block text-xs text-slate-500 mb-1">Title</label>
                  <input
                    value={newCrTitle}
                    onChange={(e) => setNewCrTitle(e.target.value)}
                    placeholder="e.g. Update firewall policy for payments subnet"
                    className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Change Type</label>
                  <select
                    value={newCrType}
                    onChange={(e) => setNewCrType(e.target.value as ChangeType)}
                    className="w-full text-sm border border-slate-200 rounded px-3 py-1.5"
                  >
                    {CHANGE_TYPES.map((t) => (
                      <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">
                    Target Assets ({newCrAssets.length} selected)
                  </label>
                  <select
                    multiple
                    value={newCrAssets}
                    onChange={(e) =>
                      setNewCrAssets(Array.from(e.target.selectedOptions, (o) => o.value))
                    }
                    className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 h-20"
                  >
                    {(assets ?? []).map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} ({a.environment})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="col-span-2">
                  <label className="block text-xs text-slate-500 mb-1">Desired Outcome (JSON)</label>
                  <textarea
                    value={newCrOutcome}
                    onChange={(e) => { setNewCrOutcome(e.target.value); setNewCrJsonError(""); }}
                    rows={4}
                    className={`w-full text-xs font-mono border rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 ${newCrJsonError ? "border-red-400" : "border-slate-200"}`}
                  />
                  {newCrJsonError && <p className="text-xs text-red-500 mt-1">{newCrJsonError}</p>}
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => createAndAddCr.mutate()}
                  disabled={!newCrTitle.trim() || createAndAddCr.isPending}
                  className="px-3 py-1.5 bg-brand-600 text-white text-sm rounded hover:bg-brand-700 disabled:opacity-50"
                >
                  {createAndAddCr.isPending ? "Creating…" : "Create & Add to Project"}
                </button>
                <button
                  onClick={() => { setShowNewCrForm(false); setNewCrTitle(""); setNewCrOutcome("{}"); }}
                  className="px-3 py-1.5 border border-slate-200 text-slate-600 text-sm rounded hover:bg-slate-50"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/pages/ProjectDetail.tsx
git commit -m "feat: add ProjectDetail page with assembly and execution view"
```

---

### Task 10: Routes + Sidebar

**Files:**
- Modify: `frontend/src/routes/index.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

- [ ] **Step 1: Add project status styles to `frontend/src/components/StatusBadge.tsx`**

In `STATUS_STYLES`, add:
```typescript
  in_progress: "bg-brand-50 text-brand-700",
  cancelled: "bg-slate-100 text-slate-500",
```

In `STATUS_LABELS`, add:
```typescript
  in_progress: "In Progress",
  cancelled: "Cancelled",
```

- [ ] **Step 3: Update `frontend/src/routes/index.tsx`**

Add imports:
```tsx
import { Projects } from "../pages/Projects";
import { ProjectDetail } from "../pages/ProjectDetail";
```

Add routes inside the `<Route element={<Layout />}>` block, before `/assets`:
```tsx
<Route path="/projects" element={<Projects />} />
<Route path="/projects/new" element={<ProjectDetail />} />
<Route path="/projects/:id" element={<ProjectDetail />} />
```

- [ ] **Step 4: Update `frontend/src/components/Sidebar.tsx`**

Add `FolderOpen` to the lucide-react import:
```tsx
import {
  LayoutDashboard, FileStack, CheckSquare, Plug, Server, ShieldCheck, FolderOpen,
} from "lucide-react";
```

Add to `navItems` array, before Change Requests:
```tsx
{ to: "/projects", label: "Projects", icon: FolderOpen },
```

- [ ] **Step 5: Verify in browser**

Open http://localhost:3000 — verify:
1. "Projects" appears in the sidebar navigation between Dashboard and Change Requests
2. Navigating to `/projects` shows an empty state with a "New Project" button
3. Clicking "New Project" goes to `/projects/new` with an editable name/goal header and a "Create Project" button
4. Creating a project saves it and redirects to `/projects/{id}`
5. The project detail page shows the empty member list with "Add existing CR" and "Create new CR" buttons
6. Adding an existing CR appears in the member list with reorder arrows and remove button
7. Setting a dependency on a CR shows the popover and updates the "Depends on:" line
8. Creating a new CR inline creates it and adds it to the project
9. Changing project status to "in_progress" shows the status summary bar and replaces add/remove controls with action buttons

- [ ] **Step 6: Run final backend test suite**

```bash
docker run --rm -v "F:/Nexplane/nexplane/backend:/app" --workdir "//app" python:3.12-slim sh -c "pip install -r requirements.txt -q && python -m pytest app/tests/ -q --tb=short" 2>&1 | tail -5
```

Expected: 89 passed.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/index.tsx frontend/src/components/Sidebar.tsx frontend/src/components/StatusBadge.tsx
git commit -m "feat: add Projects routes, sidebar navigation, and status badge styles"
```
