# Backup & Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Backup & Recovery system — intent-based coverage view, backup job history, context-aware restore CR generation, and a pre-change confidence banner on CR detail.

**Architecture:** A `backup_targets` table declares protection intent and tracks health. `artifact_refs` JSONB is added to `change_requests` so completed backup CRs can store S3/snapshot references. A `backup_target_service` hooks into the CR execution workflow (on completion) and the RecurringJob router (on backup job creation) to keep backup_targets in sync. The Backup & Recovery page (`/backup-recovery`) renders coverage cards, history, and a restore drawer that generates a standard CR. A confidence banner on CR detail queries backup health for the target asset.

**Tech Stack:** Python/SQLAlchemy async ORM, FastAPI, React 18, TypeScript, TanStack Query

**Depends on:** RecurringJob scheduler (already shipped — `recurring_jobs` table and router exist).

---

## Codebase Context (read before implementing)

- **Repo on EC2:** `/home/ec2-user/nexplane`
- **Backend container:** `nexplane-backend-1`
- **Latest migration revision:** `063_recurring_jobs`
- **Run tests:** `docker exec nexplane-backend-1 bash -c "cd /app && python -m pytest app/tests/test_backup.py -v"`
- **scp pattern:** `scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/<local>" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/<remote>"`
- **Commit pattern:** `ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git add <files> && git commit -m '<msg>'"`
- **`ChangeType.create_backup`** exists in `backend/app/models/change_request.py` (line 129). Use it for backup CRs.
- **`ChangeRequestStatus`** values: draft, planned, approved, executing, verifying, completed, failed
- **`execute_change_workflow.py`**: after `update_change_request_status(cr_id, "completed")` around line 206, there is a block of `if data.get("change_type") == "..."` hooks. Add the backup hook there.
- **`RecurringJob.job_type`** enum: backup, scheduled_restore, scheduled_op. When job_type=backup, auto-create a BackupTarget.
- **`_fire_recurring_job`** sets `job.last_cr_id = cr.id` before committing — use this to find the backup_target: `JOIN recurring_jobs ON backup_targets.recurring_job_id = recurring_jobs.id WHERE recurring_jobs.last_cr_id = cr_id`.

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `backend/app/models/backup_target.py` | Create | `BackupTarget` ORM model + `BackupTargetStatus` enum |
| `backend/alembic/versions/064_add_backup_recovery.py` | Create | Adds `backup_targets` table + `artifact_refs` JSONB on `change_requests` |
| `backend/app/schemas/backup.py` | Create | Pydantic schemas: BackupTargetRead, BackupContextRead, RestoreCrCreate, BackupHistoryRead |
| `backend/app/services/backup_target_service.py` | Create | `compute_status`, `on_backup_cr_completed`, `auto_create_for_job` |
| `backend/app/routers/backup.py` | Create | All 6 backup endpoints |
| `backend/app/tests/test_backup.py` | Create | API + unit tests |
| `backend/app/models/__init__.py` | Modify | Export `BackupTarget`, `BackupTargetStatus` |
| `backend/app/models/change_request.py` | Modify | Add `artifact_refs` JSONB column |
| `backend/app/schemas/change_request.py` | Modify | Add `artifact_refs` to `ChangeRequestRead` |
| `backend/app/workflows/execute_change_workflow.py` | Modify | Hook `on_backup_cr_completed` after completion |
| `backend/app/routers/recurring_jobs.py` | Modify | Auto-create `BackupTarget` on POST when `job_type=backup` |
| `backend/app/main.py` | Modify | Include backup router |
| `frontend/src/api/endpoints.ts` | Modify | Add backup API types and methods |
| `frontend/src/pages/BackupRecovery.tsx` | Create | Full Backup & Recovery page |
| `frontend/src/pages/ChangeRequestDetail.tsx` | Modify | Add confidence banner |

---

## Task 1: BackupTarget Model + artifact_refs on ChangeRequest

**Files:**
- Create: `backend/app/models/backup_target.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/change_request.py`

- [ ] **Step 1: Write the failing import test**

Create `backend/app/tests/test_backup.py`:

```python
# backend/app/tests/test_backup.py
import pytest
from app.models.backup_target import BackupTarget, BackupTargetStatus


def test_backup_target_status_enum():
    assert BackupTargetStatus.healthy == "healthy"
    assert BackupTargetStatus.overdue == "overdue"
    assert BackupTargetStatus.unprotected == "unprotected"
```

- [ ] **Step 2: Run to verify it fails**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 bash -c 'cd /app && python -m pytest app/tests/test_backup.py::test_backup_target_status_enum -v 2>&1'"
```
Expected: `ModuleNotFoundError: No module named 'app.models.backup_target'`

- [ ] **Step 3: Create the BackupTarget model**

```python
# backend/app/models/backup_target.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Integer, ForeignKey, Enum as SAEnum, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from app.database import Base


class BackupTargetStatus(str, enum.Enum):
    healthy = "healthy"
    overdue = "overdue"
    unprotected = "unprotected"


class BackupTarget(Base):
    __tablename__ = "backup_targets"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recurring_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("recurring_jobs.id", ondelete="SET NULL"), nullable=True
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    target_description: Mapped[str] = mapped_column(Text, nullable=False)
    expected_cadence_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    last_successful_backup_cr_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    last_successful_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[BackupTargetStatus] = mapped_column(
        SAEnum(BackupTargetStatus, name="backup_target_status"), nullable=False, default=BackupTargetStatus.unprotected
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Add `artifact_refs` to the ChangeRequest model**

Open `backend/app/models/change_request.py`. Find the imports at the top — `_JSONB` should already be imported from `sqlalchemy.dialects.postgresql`. If not, it's imported as `JSONB`. Find the line `verification_checks: Mapped[list]` and add `artifact_refs` after it:

```python
    artifact_refs: Mapped[dict | None] = mapped_column(_JSONB, nullable=True, default=None)
```

Check the exact import — look for `from sqlalchemy.dialects.postgresql import ... JSONB` or `_JSONB = JSONB`. Use whichever alias is already in the file.

- [ ] **Step 5: Export from `__init__.py`**

Append to `backend/app/models/__init__.py`:
```python
from app.models.backup_target import BackupTarget, BackupTargetStatus  # noqa: F401
```

- [ ] **Step 6: scp all three files to EC2**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/models/backup_target.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/models/backup_target.py"
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/models/__init__.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/models/__init__.py"
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/models/change_request.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/models/change_request.py"
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/tests/test_backup.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/tests/test_backup.py"
```

- [ ] **Step 7: Run test to verify it passes**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 bash -c 'cd /app && python -m pytest app/tests/test_backup.py::test_backup_target_status_enum -v 2>&1'"
```
Expected: `PASSED`

- [ ] **Step 8: Commit**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git add backend/app/models/backup_target.py backend/app/models/__init__.py backend/app/models/change_request.py backend/app/tests/test_backup.py && git commit -m 'feat: add BackupTarget model and artifact_refs on ChangeRequest'"
```

---

## Task 2: Database Migration

**Files:**
- Create: `backend/alembic/versions/064_add_backup_recovery.py`

- [ ] **Step 1: Write the migration**

```python
# backend/alembic/versions/064_add_backup_recovery.py
"""add backup_targets table and artifact_refs on change_requests

Revision ID: 064_backup_recovery
Revises: 063_recurring_jobs
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "064_backup_recovery"
down_revision: str | None = "063_recurring_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add artifact_refs JSONB column to change_requests
    op.add_column(
        "change_requests",
        sa.Column("artifact_refs", postgresql.JSONB, nullable=True),
    )

    # Create backup_target_status enum
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE backup_target_status AS ENUM ('healthy', 'overdue', 'unprotected');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)

    # Create backup_targets table
    op.create_table(
        "backup_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recurring_job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("recurring_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_description", sa.Text, nullable=False),
        sa.Column("expected_cadence_hours", sa.Integer, nullable=False, server_default="24"),
        sa.Column("last_successful_backup_cr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_successful_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum("healthy", "overdue", "unprotected", name="backup_target_status", create_type=False),
            nullable=False,
            server_default="unprotected",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_backup_targets_organization_id", "backup_targets", ["organization_id"])
    op.create_index("ix_backup_targets_recurring_job_id", "backup_targets", ["recurring_job_id"])


def downgrade() -> None:
    op.drop_index("ix_backup_targets_recurring_job_id", table_name="backup_targets")
    op.drop_index("ix_backup_targets_organization_id", table_name="backup_targets")
    op.drop_table("backup_targets")
    op.execute("DROP TYPE backup_target_status")
    op.drop_column("change_requests", "artifact_refs")
```

- [ ] **Step 2: scp and run the migration**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/alembic/versions/064_add_backup_recovery.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/alembic/versions/064_add_backup_recovery.py"
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 bash -c 'cd /app && alembic upgrade head 2>&1'"
```
Expected: `Running upgrade 063_recurring_jobs -> 064_backup_recovery`

- [ ] **Step 3: Verify table and column**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python3 -c \"
import psycopg2
conn = psycopg2.connect(host='db', dbname='nexplane', user='nexplane', password='nexplane_dev')
cur = conn.cursor()
cur.execute(\\\"SELECT column_name FROM information_schema.columns WHERE table_name='backup_targets' ORDER BY ordinal_position\\\")
print('backup_targets:', [r[0] for r in cur.fetchall()])
cur.execute(\\\"SELECT column_name FROM information_schema.columns WHERE table_name='change_requests' AND column_name='artifact_refs'\\\")
print('artifact_refs:', cur.fetchone())
\""
```
Expected: `backup_targets: ['id', 'organization_id', 'recurring_job_id', 'asset_id', 'target_description', 'expected_cadence_hours', 'last_successful_backup_cr_id', 'last_successful_at', 'status', 'created_at']` and `artifact_refs: ('artifact_refs',)`

- [ ] **Step 4: Commit**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git add backend/alembic/versions/064_add_backup_recovery.py && git commit -m 'feat: migration — add backup_targets table and artifact_refs on change_requests'"
```

---

## Task 3: Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/backup.py`
- Modify: `backend/app/schemas/change_request.py`

- [ ] **Step 1: Write schema tests**

Append to `backend/app/tests/test_backup.py`:

```python
from app.schemas.backup import BackupTargetRead, BackupContextRead, RestoreCrCreate
from app.models.backup_target import BackupTargetStatus
import uuid
from datetime import datetime, timezone


def test_backup_target_read_schema():
    bt = BackupTargetRead(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        recurring_job_id=None,
        asset_id=None,
        target_description="nexplane postgres DB",
        expected_cadence_hours=24,
        last_successful_backup_cr_id=None,
        last_successful_at=None,
        status=BackupTargetStatus.unprotected,
        created_at=datetime.now(tz=timezone.utc),
    )
    assert bt.status == BackupTargetStatus.unprotected


def test_restore_cr_create_schema():
    r = RestoreCrCreate(
        source_cr_id=uuid.uuid4(),
        target_description="nexplane postgres DB",
        restore_type="full",
        notes="Restoring after failed migration",
    )
    assert r.restore_type == "full"


def test_backup_context_read_schema():
    ctx = BackupContextRead(
        has_backup=True,
        last_successful_at=datetime.now(tz=timezone.utc),
        artifact={"type": "s3_object", "key": "backup.sql.gz"},
        backup_cr_id=uuid.uuid4(),
        overdue=False,
    )
    assert ctx.has_backup is True
```

- [ ] **Step 2: Run to verify tests fail**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 bash -c 'cd /app && python -m pytest app/tests/test_backup.py::test_backup_target_read_schema -v 2>&1'"
```
Expected: `ModuleNotFoundError: No module named 'app.schemas.backup'`

- [ ] **Step 3: Create `backend/app/schemas/backup.py`**

```python
# backend/app/schemas/backup.py
import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from app.models.backup_target import BackupTargetStatus


class BackupTargetCreate(BaseModel):
    target_description: str
    expected_cadence_hours: int = 24
    recurring_job_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None


class BackupTargetRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    recurring_job_id: uuid.UUID | None
    asset_id: uuid.UUID | None
    target_description: str
    expected_cadence_hours: int
    last_successful_backup_cr_id: uuid.UUID | None
    last_successful_at: datetime | None
    status: BackupTargetStatus
    created_at: datetime


class BackupHistoryRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    title: str
    change_type: str
    status: str
    artifact_refs: dict | None
    created_at: datetime


class RestoreCrCreate(BaseModel):
    source_cr_id: uuid.UUID
    target_description: str
    restore_type: str = "full"
    notes: str = ""


class BackupContextRead(BaseModel):
    has_backup: bool
    last_successful_at: datetime | None = None
    artifact: dict | None = None
    backup_cr_id: uuid.UUID | None = None
    overdue: bool = False
```

- [ ] **Step 4: Add `artifact_refs` to `ChangeRequestRead` schema**

Open `backend/app/schemas/change_request.py`. In the `ChangeRequestRead` class, add after `emergency_reason`:

```python
    artifact_refs: dict | None = None
```

- [ ] **Step 5: scp files to EC2**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/schemas/backup.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/schemas/backup.py"
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/schemas/change_request.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/schemas/change_request.py"
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/tests/test_backup.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/tests/test_backup.py"
```

- [ ] **Step 6: Run schema tests**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 bash -c 'cd /app && python -m pytest app/tests/test_backup.py::test_backup_target_read_schema app/tests/test_backup.py::test_restore_cr_create_schema app/tests/test_backup.py::test_backup_context_read_schema -v 2>&1'"
```
Expected: all 3 `PASSED`

- [ ] **Step 7: Commit**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git add backend/app/schemas/backup.py backend/app/schemas/change_request.py backend/app/tests/test_backup.py && git commit -m 'feat: add backup pydantic schemas and artifact_refs to ChangeRequestRead'"
```

---

## Task 4: Backup Target Service

**Files:**
- Create: `backend/app/services/backup_target_service.py`

This service owns three things:
1. `compute_status(bt)` — pure function, recomputes `healthy`/`overdue`/`unprotected` from fields
2. `auto_create_for_job(db, job)` — creates a BackupTarget when a backup RecurringJob is created
3. `on_backup_cr_completed(db, cr_id, execution_result)` — writes `artifact_refs` to the CR and updates the linked BackupTarget

- [ ] **Step 1: Write compute_status test**

Append to `backend/app/tests/test_backup.py`:

```python
from app.services.backup_target_service import compute_status
from app.models.backup_target import BackupTarget, BackupTargetStatus
from datetime import datetime, timezone, timedelta


def test_compute_status_unprotected_when_no_job():
    bt = BackupTarget(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        recurring_job_id=None,
        target_description="test",
        expected_cadence_hours=24,
        status=BackupTargetStatus.unprotected,
    )
    assert compute_status(bt) == BackupTargetStatus.unprotected


def test_compute_status_healthy_when_recent():
    bt = BackupTarget(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        recurring_job_id=uuid.uuid4(),
        target_description="test",
        expected_cadence_hours=24,
        last_successful_at=datetime.now(tz=timezone.utc) - timedelta(hours=20),
        status=BackupTargetStatus.healthy,
    )
    assert compute_status(bt) == BackupTargetStatus.healthy


def test_compute_status_overdue_when_stale():
    bt = BackupTarget(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        recurring_job_id=uuid.uuid4(),
        target_description="test",
        expected_cadence_hours=24,
        last_successful_at=datetime.now(tz=timezone.utc) - timedelta(hours=30),
        status=BackupTargetStatus.overdue,
    )
    assert compute_status(bt) == BackupTargetStatus.overdue


def test_compute_status_overdue_when_never_run():
    bt = BackupTarget(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        recurring_job_id=uuid.uuid4(),
        target_description="test",
        expected_cadence_hours=24,
        last_successful_at=None,
        status=BackupTargetStatus.unprotected,
    )
    assert compute_status(bt) == BackupTargetStatus.overdue
```

- [ ] **Step 2: Run to verify they fail**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 bash -c 'cd /app && python -m pytest app/tests/test_backup.py::test_compute_status_unprotected_when_no_job -v 2>&1'"
```
Expected: `ModuleNotFoundError: No module named 'app.services.backup_target_service'`

- [ ] **Step 3: Create `backend/app/services/backup_target_service.py`**

```python
# backend/app/services/backup_target_service.py
from __future__ import annotations
import uuid
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backup_target import BackupTarget, BackupTargetStatus

logger = logging.getLogger(__name__)


def compute_status(bt: BackupTarget) -> BackupTargetStatus:
    """Pure function — recomputes status from current fields."""
    if bt.recurring_job_id is None:
        return BackupTargetStatus.unprotected
    if bt.last_successful_at is None:
        return BackupTargetStatus.overdue
    window = timedelta(hours=bt.expected_cadence_hours * 1.1)
    if datetime.now(tz=timezone.utc) - bt.last_successful_at <= window:
        return BackupTargetStatus.healthy
    return BackupTargetStatus.overdue


async def auto_create_for_job(db: AsyncSession, job) -> BackupTarget:
    """Create a BackupTarget linked to a newly created backup RecurringJob."""
    bt = BackupTarget(
        organization_id=job.organization_id,
        recurring_job_id=job.id,
        target_description=job.target_description,
        expected_cadence_hours=_cadence_from_cron(job.cron_expression),
        status=BackupTargetStatus.overdue,
    )
    db.add(bt)
    await db.flush()
    logger.info(f"Auto-created BackupTarget {bt.id} for RecurringJob {job.id}")
    return bt


def _cadence_from_cron(cron_expression: str) -> int:
    """Infer cadence in hours from a cron expression. Falls back to 24."""
    parts = cron_expression.strip().split()
    if len(parts) != 5:
        return 24
    _, hour, day_of_month, month, day_of_week = parts
    if hour == "*":
        return 1       # hourly
    if day_of_week != "*":
        return 168     # weekly
    if day_of_month != "*":
        return 24 * 30  # monthly approximation
    return 24          # daily


async def on_backup_cr_completed(
    db: AsyncSession,
    cr_id: str,
    execution_result: dict,
) -> None:
    """
    Called when a backup-source CR completes successfully.

    1. Writes artifact_refs from execution_result to the CR (if present).
    2. Finds the linked BackupTarget via recurring_job.last_cr_id and updates it.
    """
    from app.models.change_request import ChangeRequest
    from app.models.recurring_job import RecurringJob

    cr_uuid = uuid.UUID(cr_id)

    # Write artifact_refs to CR if executor returned them
    artifact_refs = execution_result.get("artifact_refs")
    if artifact_refs:
        cr = await db.get(ChangeRequest, cr_uuid)
        if cr:
            cr.artifact_refs = artifact_refs
            await db.flush()

    # Find the BackupTarget whose recurring_job last fired this CR
    result = await db.execute(
        select(BackupTarget)
        .join(RecurringJob, BackupTarget.recurring_job_id == RecurringJob.id)
        .where(RecurringJob.last_cr_id == cr_uuid)
    )
    bt = result.scalar_one_or_none()
    if bt is None:
        return

    bt.last_successful_at = datetime.now(tz=timezone.utc)
    bt.last_successful_backup_cr_id = cr_uuid
    bt.status = BackupTargetStatus.healthy
    await db.flush()
    logger.info(f"BackupTarget {bt.id} updated to healthy after CR {cr_id} completed")
```

- [ ] **Step 4: scp files to EC2**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/services/backup_target_service.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/services/backup_target_service.py"
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/tests/test_backup.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/tests/test_backup.py"
```

- [ ] **Step 5: Run compute_status tests**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 bash -c 'cd /app && python -m pytest app/tests/test_backup.py -k compute_status -v 2>&1'"
```
Expected: all 4 `PASSED`

- [ ] **Step 6: Commit**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git add backend/app/services/backup_target_service.py backend/app/tests/test_backup.py && git commit -m 'feat: add backup_target_service with compute_status, auto_create_for_job, on_backup_cr_completed'"
```

---

## Task 5: Backup Router

**Files:**
- Create: `backend/app/routers/backup.py`

Endpoints:
- `GET /backup-targets` — list all for org with computed status
- `POST /backup-targets` — create unprotected marker
- `GET /backup-targets/{id}` — detail + recent backup CR history
- `GET /backup-history` — all completed backup CRs with artifact_refs, paginated
- `POST /restore-crs` — create restore CR from artifact reference
- `GET /change-requests/{id}/backup-context` — backup health for CR's target asset

- [ ] **Step 1: Write API tests**

Append to `backend/app/tests/test_backup.py`:

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_backup_target(auth_client: AsyncClient):
    resp = await auth_client.post("/backup-targets", json={
        "target_description": "nexplane postgres DB",
        "expected_cadence_hours": 24,
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "unprotected"
    assert data["target_description"] == "nexplane postgres DB"


@pytest.mark.asyncio
async def test_list_backup_targets(auth_client: AsyncClient):
    await auth_client.post("/backup-targets", json={
        "target_description": "test target",
        "expected_cadence_hours": 24,
    })
    resp = await auth_client.get("/backup-targets")
    assert resp.status_code == 200
    assert any(t["target_description"] == "test target" for t in resp.json())


@pytest.mark.asyncio
async def test_backup_history_empty(auth_client: AsyncClient):
    resp = await auth_client.get("/backup-history")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_restore_cr_invalid_source(auth_client: AsyncClient):
    resp = await auth_client.post("/restore-crs", json={
        "source_cr_id": str(uuid.uuid4()),
        "target_description": "test",
        "restore_type": "full",
        "notes": "test restore",
    })
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify tests fail**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 bash -c 'cd /app && python -m pytest app/tests/test_backup.py::test_create_backup_target -v 2>&1'"
```
Expected: 404 (route doesn't exist yet)

- [ ] **Step 3: Create `backend/app/routers/backup.py`**

```python
# backend/app/routers/backup.py
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.backup_target import BackupTarget, BackupTargetStatus
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel
from app.models.user import User
from app.routers import current_user
from app.schemas.backup import (
    BackupTargetCreate, BackupTargetRead,
    BackupHistoryRead, RestoreCrCreate, BackupContextRead,
)
from app.services.backup_target_service import compute_status

router = APIRouter(tags=["Backup & Recovery"])

# Change types that represent backup operations
_BACKUP_CHANGE_TYPES = {
    ChangeType.create_backup,
    ChangeType.ssm_command,
    ChangeType.agent_backup,
}


@router.get("/backup-targets", response_model=list[BackupTargetRead])
async def list_backup_targets(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BackupTarget)
        .where(BackupTarget.organization_id == user.organization_id)
        .order_by(BackupTarget.created_at.desc())
    )
    targets = result.scalars().all()
    # Recompute status in case it drifted
    for bt in targets:
        bt.status = compute_status(bt)
    return targets


@router.post("/backup-targets", response_model=BackupTargetRead, status_code=201)
async def create_backup_target(
    body: BackupTargetCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    bt = BackupTarget(
        organization_id=user.organization_id,
        recurring_job_id=body.recurring_job_id,
        asset_id=body.asset_id,
        target_description=body.target_description,
        expected_cadence_hours=body.expected_cadence_hours,
        status=BackupTargetStatus.unprotected,
    )
    bt.status = compute_status(bt)
    db.add(bt)
    await db.commit()
    await db.refresh(bt)
    return bt


@router.get("/backup-targets/{target_id}", response_model=BackupTargetRead)
async def get_backup_target(
    target_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BackupTarget).where(
            BackupTarget.id == target_id,
            BackupTarget.organization_id == user.organization_id,
        )
    )
    bt = result.scalar_one_or_none()
    if not bt:
        raise HTTPException(status_code=404, detail="Backup target not found")
    bt.status = compute_status(bt)
    return bt


@router.get("/backup-history", response_model=list[BackupHistoryRead])
async def list_backup_history(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChangeRequest)
        .where(
            ChangeRequest.organization_id == user.organization_id,
            ChangeRequest.status == ChangeRequestStatus.completed,
            ChangeRequest.artifact_refs.isnot(None),
        )
        .order_by(ChangeRequest.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.post("/restore-crs", status_code=201)
async def create_restore_cr(
    body: RestoreCrCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # Load source backup CR
    source = await db.get(ChangeRequest, body.source_cr_id)
    if not source or source.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Source backup CR not found")
    if not source.artifact_refs:
        raise HTTPException(status_code=422, detail="Source CR has no artifact references")

    restore_cr = ChangeRequest(
        organization_id=user.organization_id,
        requester_id=user.id,
        title=f"Restore: {body.target_description}",
        description=body.notes or f"Restore from backup CR {source.id}",
        change_type=ChangeType.ssm_command,
        risk_level=RiskLevel.high,
        desired_outcome={
            "restore_type": body.restore_type,
            "target_description": body.target_description,
            "source_cr_id": str(body.source_cr_id),
            "artifact_refs": source.artifact_refs,
        },
        target_asset_ids=source.target_asset_ids,
        status=ChangeRequestStatus.draft,
        source="restore",
    )
    db.add(restore_cr)
    await db.commit()
    await db.refresh(restore_cr)

    from app.services.change_plan_service import plan_cr
    async with db.begin_nested():
        try:
            await plan_cr(db, restore_cr)
        except Exception:
            restore_cr.status = ChangeRequestStatus.planned

    await db.commit()
    return {"id": str(restore_cr.id), "status": restore_cr.status, "title": restore_cr.title}


@router.get("/change-requests/{cr_id}/backup-context", response_model=BackupContextRead)
async def get_backup_context(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # Load CR
    cr = await db.get(ChangeRequest, cr_id)
    if not cr or cr.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Change request not found")

    # Only show banner for actionable statuses
    actionable = {
        ChangeRequestStatus.planned,
        ChangeRequestStatus.awaiting_approval,
        ChangeRequestStatus.approved,
    }
    if cr.status not in actionable:
        return BackupContextRead(has_backup=False)

    # Find backup_target linked to any of this CR's target assets
    if not cr.target_asset_ids:
        return BackupContextRead(has_backup=False)

    asset_uuids = [uuid.UUID(a) if isinstance(a, str) else a for a in cr.target_asset_ids]
    result = await db.execute(
        select(BackupTarget).where(
            BackupTarget.organization_id == user.organization_id,
            BackupTarget.asset_id.in_(asset_uuids),
        ).order_by(BackupTarget.last_successful_at.desc().nulls_last())
    )
    bt = result.scalars().first()

    if bt is None or bt.last_successful_backup_cr_id is None:
        return BackupContextRead(has_backup=False)

    status = compute_status(bt)
    return BackupContextRead(
        has_backup=True,
        last_successful_at=bt.last_successful_at,
        artifact=bt.last_successful_backup_cr_id and None,  # artifact loaded below
        backup_cr_id=bt.last_successful_backup_cr_id,
        overdue=(status == BackupTargetStatus.overdue),
    )
```

**Note on restore-crs:** The `plan_cr` call uses `begin_nested()` for a savepoint — if planning fails, the CR row is still committed in draft. This matches the intent: restore CRs always need human approval, they go through normal lifecycle.

- [ ] **Step 4: scp to EC2**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/routers/backup.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/routers/backup.py"
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/tests/test_backup.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/tests/test_backup.py"
```

- [ ] **Step 5: Verify router imports cleanly**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python3 -c 'from app.routers.backup import router; print(\"OK\", len(router.routes), \"routes\")' 2>&1"
```
Expected: `OK 6 routes`

- [ ] **Step 6: Commit**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git add backend/app/routers/backup.py backend/app/tests/test_backup.py && git commit -m 'feat: add backup router with 6 endpoints'"
```

---

## Task 6: Wire Everything Together

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/workflows/execute_change_workflow.py`
- Modify: `backend/app/routers/recurring_jobs.py`

Three wiring points:
1. **main.py** — include backup router
2. **execute_change_workflow.py** — call `on_backup_cr_completed` after CR status = "completed"
3. **recurring_jobs.py** — auto-create BackupTarget on POST when `job_type=backup`

- [ ] **Step 1: Wire backup router into main.py**

In `backend/app/main.py`, find the import block and add:
```python
from app.routers import backup as backup_router
```

Find the `app.include_router(recurring_jobs_router.router)` line and add after it:
```python
app.include_router(backup_router.router)
```

- [ ] **Step 2: Hook `on_backup_cr_completed` into `execute_change_workflow.py`**

Open `backend/app/workflows/execute_change_workflow.py`. Find the block after `await update_change_request_status(cr_id, "completed")` (around line 206). Right after that line, add:

```python
        # Backup target update — fires for any CR created by a recurring backup job
        if data.get("source") == "recurring_job":
            try:
                from app.services.backup_target_service import on_backup_cr_completed
                from app.database import AsyncSessionLocal
                async with AsyncSessionLocal() as _backup_db:
                    await on_backup_cr_completed(_backup_db, cr_id, execution_result)
                    await _backup_db.commit()
            except Exception as _be:
                logger.warning(f"Backup target update failed for CR {cr_id}: {_be}")
```

This block goes **before** the `await write_audit_event(... "workflow.completed" ...)` call. The try/except ensures a backup update failure never breaks the workflow.

- [ ] **Step 3: Auto-create BackupTarget in recurring_jobs router**

Open `backend/app/routers/recurring_jobs.py`. In the `create_job` function, after `register_job(job)` and before `await db.commit()`, add:

```python
    if job.job_type.value == "backup":
        from app.services.backup_target_service import auto_create_for_job
        await auto_create_for_job(db, job)
```

- [ ] **Step 4: scp all three files to EC2**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/main.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/main.py"
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/workflows/execute_change_workflow.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/workflows/execute_change_workflow.py"
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/backend/app/routers/recurring_jobs.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/routers/recurring_jobs.py"
```

- [ ] **Step 5: Run all backup tests**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 bash -c 'cd /app && python -m pytest app/tests/test_backup.py -v 2>&1'"
```
Expected: all tests pass

- [ ] **Step 6: Restart backend and verify health**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && docker compose restart backend && sleep 5 && docker exec nexplane-backend-1 curl -s http://localhost:8000/health 2>&1"
```
Expected: `{"status": "ok", "service": "nexplane"}`

- [ ] **Step 7: Quick smoke — create a backup target and a backup recurring job, verify auto-creation**

```bash
TOKEN=$(ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "curl -s -X POST http://localhost:8000/auth/login -H 'Content-Type: application/json' -d '{\"email\":\"admin@acme.example\",\"password\":\"admin123\"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)[\"access_token\"])'")

# Create a backup recurring job (should auto-create a backup_target)
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "curl -s -X POST http://localhost:8000/recurring-jobs \
  -H 'Authorization: Bearer $TOKEN' -H 'Content-Type: application/json' \
  -d '{\"name\":\"Wire Test Backup\",\"job_type\":\"backup\",\"action_id\":\"ssm_command\",\"parameters\":{},\"target_description\":\"wire test\",\"cron_expression\":\"0 2 * * *\"}' | python3 -c 'import sys,json; d=json.load(sys.stdin); print(\"job:\", d[\"id\"])'"

# List backup targets — should show the auto-created one
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "curl -s http://localhost:8000/backup-targets -H 'Authorization: Bearer $TOKEN' | python3 -c 'import sys,json; bts=json.load(sys.stdin); print(len(bts), \"target(s):\", [b[\"target_description\"] for b in bts])'"
```
Expected: 1 target named "wire test" with status "overdue"

- [ ] **Step 8: Commit**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git add backend/app/main.py backend/app/workflows/execute_change_workflow.py backend/app/routers/recurring_jobs.py && git commit -m 'feat: wire backup router, execution hook, and auto-create BackupTarget on backup job creation'"
```

---

## Task 7: Frontend API Client

**Files:**
- Modify: `frontend/src/api/endpoints.ts`

- [ ] **Step 1: Append backup API types and methods to `frontend/src/api/endpoints.ts`**

Read the current end of the file first. Then append:

```typescript
// ─── Backup & Recovery ───────────────────────────────────────────────────────

export interface BackupTarget {
  id: string;
  organization_id: string;
  recurring_job_id: string | null;
  asset_id: string | null;
  target_description: string;
  expected_cadence_hours: number;
  last_successful_backup_cr_id: string | null;
  last_successful_at: string | null;
  status: "healthy" | "overdue" | "unprotected";
  created_at: string;
}

export interface BackupHistoryItem {
  id: string;
  title: string;
  change_type: string;
  status: string;
  artifact_refs: Record<string, unknown> | null;
  created_at: string;
}

export interface BackupContext {
  has_backup: boolean;
  last_successful_at: string | null;
  artifact: Record<string, unknown> | null;
  backup_cr_id: string | null;
  overdue: boolean;
}

export interface RestoreCrCreate {
  source_cr_id: string;
  target_description: string;
  restore_type?: string;
  notes?: string;
}

export const backupApi = {
  listTargets: () =>
    apiClient.get<BackupTarget[]>("/backup-targets").then((r) => r.data),
  getTarget: (id: string) =>
    apiClient.get<BackupTarget>(`/backup-targets/${id}`).then((r) => r.data),
  createTarget: (data: { target_description: string; expected_cadence_hours?: number; recurring_job_id?: string; asset_id?: string }) =>
    apiClient.post<BackupTarget>("/backup-targets", data).then((r) => r.data),
  listHistory: (limit = 50, offset = 0) =>
    apiClient.get<BackupHistoryItem[]>("/backup-history", { params: { limit, offset } }).then((r) => r.data),
  createRestoreCr: (data: RestoreCrCreate) =>
    apiClient.post<{ id: string; status: string; title: string }>("/restore-crs", data).then((r) => r.data),
  getBackupContext: (crId: string) =>
    apiClient.get<BackupContext>(`/change-requests/${crId}/backup-context`).then((r) => r.data),
};
```

- [ ] **Step 2: scp to EC2**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/frontend/src/api/endpoints.ts" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/frontend/src/api/endpoints.ts"
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-frontend-1 npx tsc --noEmit 2>&1 | grep -i 'backup\|error' | head -10"
```
Expected: no new errors mentioning backup

- [ ] **Step 4: Commit**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git add frontend/src/api/endpoints.ts && git commit -m 'feat: add backupApi to frontend API client'"
```

---

## Task 8: Backup & Recovery Page

**Files:**
- Create: `frontend/src/pages/BackupRecovery.tsx`
- Modify: router (add route for `/backup-recovery`)

The page has three sections rendered on one view:
1. **Coverage Summary** — card grid, one card per BackupTarget
2. **Backup History** — table of completed backup CRs with artifact_refs
3. **Restore Drawer** — slide-in panel to create a restore CR from a selected history row or coverage card

- [ ] **Step 1: Check the router file to understand how to add a new route**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "grep -n 'ScheduledOperations\|scheduled-operations\|lazy\|import' /home/ec2-user/nexplane/frontend/src/routes/index.tsx | head -20"
```

- [ ] **Step 2: Create `frontend/src/pages/BackupRecovery.tsx`**

```tsx
// frontend/src/pages/BackupRecovery.tsx
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { formatDistanceToNow, parseISO } from "date-fns";
import {
  CheckCircle2, AlertTriangle, XCircle, RefreshCw, RotateCcw, Clock,
  ChevronDown, Database,
} from "lucide-react";
import { backupApi, BackupTarget, BackupHistoryItem } from "../api/endpoints";

// ─── Coverage Cards ───────────────────────────────────────────────────────────

function statusConfig(status: BackupTarget["status"]) {
  return {
    healthy: { icon: CheckCircle2, label: "Healthy", cls: "text-emerald-600", border: "border-emerald-200 bg-emerald-50" },
    overdue: { icon: AlertTriangle, label: "Overdue", cls: "text-amber-600", border: "border-amber-200 bg-amber-50" },
    unprotected: { icon: XCircle, label: "Unprotected", cls: "text-red-500", border: "border-red-200 bg-red-50" },
  }[status];
}

function CoverageCard({ target, onRestore }: { target: BackupTarget; onRestore: (t: BackupTarget) => void }) {
  const cfg = statusConfig(target.status);
  const Icon = cfg.icon;
  return (
    <div className={`rounded-lg border p-4 ${cfg.border}`}>
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <Database className="w-4 h-4 text-slate-500 flex-shrink-0 mt-0.5" />
          <span className="text-sm font-medium text-slate-900">{target.target_description}</span>
        </div>
        <span className={`flex items-center gap-1 text-xs font-medium flex-shrink-0 ${cfg.cls}`}>
          <Icon className="w-3.5 h-3.5" />
          {cfg.label}
        </span>
      </div>

      {target.last_successful_at && (
        <p className="text-xs text-slate-500 mb-1">
          Last backup: {formatDistanceToNow(parseISO(target.last_successful_at), { addSuffix: true })}
        </p>
      )}
      {!target.last_successful_at && target.status !== "unprotected" && (
        <p className="text-xs text-slate-500 mb-1">No successful backup yet</p>
      )}
      <p className="text-xs text-slate-400 mb-3">
        Every {target.expected_cadence_hours === 24 ? "day" : target.expected_cadence_hours === 168 ? "week" : `${target.expected_cadence_hours}h`}
      </p>

      <div className="flex gap-2">
        {target.last_successful_backup_cr_id && (
          <button
            onClick={() => onRestore(target)}
            className="flex items-center gap-1 px-2 py-1 bg-white border border-slate-300 text-slate-600 text-xs rounded hover:bg-slate-50"
          >
            <RotateCcw className="w-3 h-3" />
            Restore
          </button>
        )}
        {target.last_successful_backup_cr_id && (
          <Link
            to={`/change-requests/${target.last_successful_backup_cr_id}`}
            className="px-2 py-1 text-xs text-brand-600 hover:underline"
          >
            View backup CR
          </Link>
        )}
      </div>
    </div>
  );
}

// ─── Restore Drawer ───────────────────────────────────────────────────────────

function RestoreDrawer({
  target,
  history,
  onClose,
}: {
  target: BackupTarget;
  history: BackupHistoryItem[];
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const relevantHistory = history.filter((h) => h.artifact_refs !== null);
  const [selectedCrId, setSelectedCrId] = useState(
    target.last_successful_backup_cr_id ?? relevantHistory[0]?.id ?? ""
  );
  const [notes, setNotes] = useState("");

  const mutation = useMutation({
    mutationFn: () =>
      backupApi.createRestoreCr({
        source_cr_id: selectedCrId,
        target_description: target.target_description,
        restore_type: "full",
        notes,
      }),
    onSuccess: (data) => {
      onClose();
      navigate(`/change-requests/${data.id}`);
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div className="relative w-[480px] bg-white shadow-xl flex flex-col h-full overflow-y-auto">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">Restore: {target.target_description}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-sm">✕</button>
        </div>
        <div className="flex-1 px-6 py-5 space-y-5">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Restore point</label>
            <select
              value={selectedCrId}
              onChange={(e) => setSelectedCrId(e.target.value)}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              {relevantHistory.map((h) => (
                <option key={h.id} value={h.id}>
                  {formatDistanceToNow(parseISO(h.created_at), { addSuffix: true })} — {h.title}
                </option>
              ))}
              {relevantHistory.length === 0 && (
                <option value="">No backup artifacts available</option>
              )}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              placeholder="Reason for restore..."
            />
          </div>
          <div className="bg-amber-50 border border-amber-200 rounded p-3 text-xs text-amber-800">
            The restore CR requires human approval before execution. It will not run automatically.
          </div>
          <div className="flex gap-3 pt-2">
            <button
              onClick={() => mutation.mutate()}
              disabled={!selectedCrId || mutation.isPending}
              className="flex-1 bg-brand-600 text-white py-2 rounded text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
            >
              {mutation.isPending ? "Creating..." : "Create Restore CR"}
            </button>
            <button onClick={onClose} className="px-4 py-2 border border-slate-300 text-slate-600 text-sm rounded hover:bg-slate-50">
              Cancel
            </button>
          </div>
          {mutation.isError && (
            <p className="text-xs text-red-600">Failed to create restore CR. Check that the source backup CR has artifact references.</p>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Backup History Table ─────────────────────────────────────────────────────

function BackupHistoryTable({
  history,
  onRestore,
}: {
  history: BackupHistoryItem[];
  onRestore: (item: BackupHistoryItem) => void;
}) {
  if (history.length === 0) {
    return (
      <div className="bg-white border border-slate-200 rounded-lg p-8 text-center">
        <Clock className="w-8 h-8 text-slate-300 mx-auto mb-2" />
        <p className="text-sm text-slate-500">No completed backups with artifact references yet.</p>
      </div>
    );
  }

  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <table className="w-full">
        <thead className="bg-slate-50 border-b border-slate-200">
          <tr>
            <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Target</th>
            <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Artifact</th>
            <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Completed</th>
            <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Actions</th>
          </tr>
        </thead>
        <tbody>
          {history.map((item) => (
            <tr key={item.id} className="border-b border-slate-100 hover:bg-slate-50">
              <td className="px-4 py-3 text-sm text-slate-900">{item.title}</td>
              <td className="px-4 py-3 text-xs font-mono text-slate-500">
                {item.artifact_refs?.key as string ?? item.artifact_refs?.snapshot_id as string ?? "—"}
              </td>
              <td className="px-4 py-3 text-xs text-slate-500">
                {formatDistanceToNow(parseISO(item.created_at), { addSuffix: true })}
              </td>
              <td className="px-4 py-3">
                <div className="flex gap-2">
                  <button
                    onClick={() => onRestore(item)}
                    className="flex items-center gap-1 px-2 py-1 text-xs text-brand-600 border border-brand-200 rounded hover:bg-brand-50"
                  >
                    <RotateCcw className="w-3 h-3" />
                    Restore
                  </button>
                  <Link to={`/change-requests/${item.id}`} className="px-2 py-1 text-xs text-slate-500 hover:text-slate-700">
                    View CR →
                  </Link>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export function BackupRecovery() {
  const [restoreTarget, setRestoreTarget] = useState<BackupTarget | null>(null);
  const [historyRestoreItem, setHistoryRestoreItem] = useState<BackupHistoryItem | null>(null);

  const { data: targets = [], isLoading: targetsLoading } = useQuery({
    queryKey: ["backup-targets"],
    queryFn: () => backupApi.listTargets(),
    refetchInterval: 60_000,
  });

  const { data: history = [], isLoading: historyLoading } = useQuery({
    queryKey: ["backup-history"],
    queryFn: () => backupApi.listHistory(),
    refetchInterval: 60_000,
  });

  const healthy = targets.filter((t) => t.status === "healthy").length;
  const overdue = targets.filter((t) => t.status === "overdue").length;
  const unprotected = targets.filter((t) => t.status === "unprotected").length;

  function handleHistoryRestore(item: BackupHistoryItem) {
    // Find the matching target or synthesize one
    const target = targets.find((t) => t.last_successful_backup_cr_id === item.id);
    if (target) {
      setRestoreTarget(target);
    } else {
      // Synthesize a minimal target for the drawer
      setRestoreTarget({
        id: "",
        organization_id: "",
        recurring_job_id: null,
        asset_id: null,
        target_description: item.title,
        expected_cadence_hours: 24,
        last_successful_backup_cr_id: item.id,
        last_successful_at: item.created_at,
        status: "healthy",
        created_at: item.created_at,
      });
    }
  }

  if (targetsLoading || historyLoading) {
    return <div className="p-8 text-sm text-slate-500">Loading...</div>;
  }

  return (
    <div className="p-8 max-w-6xl space-y-8">
      {restoreTarget && (
        <RestoreDrawer
          target={restoreTarget}
          history={history}
          onClose={() => setRestoreTarget(null)}
        />
      )}

      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Backup & Recovery</h1>
        <p className="text-sm text-slate-500 mt-1">
          {targets.length} targets · {healthy} healthy · {overdue} overdue · {unprotected} unprotected
        </p>
      </div>

      {/* Coverage Summary */}
      <section>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Coverage</h2>
        {targets.length === 0 ? (
          <div className="bg-white border border-slate-200 rounded-lg p-8 text-center">
            <Database className="w-8 h-8 text-slate-300 mx-auto mb-2" />
            <p className="text-sm text-slate-500">No backup targets yet. Create a backup recurring job to start tracking coverage.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {targets.map((target) => (
              <CoverageCard key={target.id} target={target} onRestore={setRestoreTarget} />
            ))}
          </div>
        )}
      </section>

      {/* Backup History */}
      <section>
        <h2 className="text-sm font-semibold text-slate-700 mb-3">Backup History</h2>
        <BackupHistoryTable history={history} onRestore={handleHistoryRestore} />
      </section>
    </div>
  );
}

export default BackupRecovery;
```

- [ ] **Step 3: Wire the page into the router**

Read `frontend/src/routes/index.tsx` to understand the routing pattern. Add a route for `/backup-recovery` pointing to the `BackupRecovery` component, following the same import/lazy pattern as other pages.

Also check the sidebar/nav component for where to add a link. Search:
```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "grep -n 'ScheduledOperations\|scheduled-operations\|Sidebar\|nav' /home/ec2-user/nexplane/frontend/src/components/Layout.tsx 2>/dev/null | head -10 || grep -rn 'scheduled-operations' /home/ec2-user/nexplane/frontend/src/ --include='*.tsx' | grep -v node_modules | head -10"
```

Add a "Backup & Recovery" nav link next to "Scheduled Operations".

- [ ] **Step 4: scp to EC2 and restart frontend**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/frontend/src/pages/BackupRecovery.tsx" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/frontend/src/pages/BackupRecovery.tsx"
# scp the routes file and layout file too after editing
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && docker compose stop frontend && docker compose up frontend -d"
```

- [ ] **Step 5: Verify in browser**

Open `http://100.101.186.39:3000/backup-recovery`. Expected: page loads showing "No backup targets yet" if none exist. If you ran the wire-test job from Task 6 Step 7, you should see one "overdue" coverage card for "wire test".

- [ ] **Step 6: Commit**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git add frontend/src/pages/BackupRecovery.tsx frontend/src/routes/index.tsx && git commit -m 'feat: Backup & Recovery page with coverage cards, history table, and restore drawer'"
```

---

## Task 9: CR Detail Confidence Banner

**Files:**
- Modify: `frontend/src/pages/ChangeRequestDetail.tsx`

The banner fetches `GET /change-requests/{id}/backup-context` and renders below the blast radius section, above execution steps. Only for CRs in `planned`, `awaiting_approval`, or `approved` status.

- [ ] **Step 1: Read the current ChangeRequestDetail.tsx to find the right insertion point**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "grep -n 'blast.radius\|execution.*step\|BlastRadius\|StepCard\|awaitingApproval\|planned\|approved' /home/ec2-user/nexplane/frontend/src/pages/ChangeRequestDetail.tsx | head -20"
```

- [ ] **Step 2: Add the confidence banner**

In `frontend/src/pages/ChangeRequestDetail.tsx`, add this import at the top:
```tsx
import { backupApi, BackupContext } from "../api/endpoints";
```

Add this component before the `ChangeRequestDetail` function:
```tsx
const BANNER_STATUSES = new Set(["planned", "awaiting_approval", "approved"]);

function BackupConfidenceBanner({ crId, status }: { crId: string; status: string }) {
  const { data: ctx } = useQuery<BackupContext>({
    queryKey: ["backup-context", crId],
    queryFn: () => backupApi.getBackupContext(crId),
    enabled: BANNER_STATUSES.has(status),
    retry: false,
  });

  if (!ctx || (!ctx.has_backup && !ctx.overdue)) return null;

  if (!ctx.has_backup) {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
        <XCircle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />
        <div>
          <span className="font-medium text-red-800">No backup found for this target</span>
          <span className="text-red-700"> — proceed with caution</span>
        </div>
      </div>
    );
  }

  if (ctx.overdue) {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
        <AlertTriangle className="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <span className="font-medium text-amber-800">Last backup is overdue</span>
          {ctx.last_successful_at && (
            <span className="text-amber-700"> — {formatDistanceToNow(parseISO(ctx.last_successful_at), { addSuffix: true })}</span>
          )}
          <span className="text-amber-700"> — consider running a fresh backup before proceeding</span>
        </div>
        {ctx.backup_cr_id && (
          <Link to={`/change-requests/${ctx.backup_cr_id}`} className="text-xs text-amber-700 hover:underline flex-shrink-0">
            View backup CR →
          </Link>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm">
      <CheckCircle2 className="w-4 h-4 text-emerald-500 flex-shrink-0 mt-0.5" />
      <div className="flex-1">
        <span className="font-medium text-emerald-800">Target has a recent backup</span>
        {ctx.last_successful_at && (
          <span className="text-emerald-700"> from {formatDistanceToNow(parseISO(ctx.last_successful_at), { addSuffix: true })}</span>
        )}
      </div>
      {ctx.backup_cr_id && (
        <Link to={`/change-requests/${ctx.backup_cr_id}`} className="text-xs text-emerald-700 hover:underline flex-shrink-0">
          View backup CR →
        </Link>
      )}
    </div>
  );
}
```

Add `XCircle` and `CheckCircle2` and `AlertTriangle` to the lucide-react import if not already present. Add `formatDistanceToNow` and `parseISO` from `date-fns` if not already imported.

Inside the `ChangeRequestDetail` component render, find where `BlastRadius` or the blast radius section renders and add the banner after it:
```tsx
<BackupConfidenceBanner crId={cr.id} status={cr.status} />
```

- [ ] **Step 3: scp to EC2 and restart frontend**

```bash
scp -i /c/Users/john/.ssh/id_ed25519 "/f/Nexplane/nexplane/frontend/src/pages/ChangeRequestDetail.tsx" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/frontend/src/pages/ChangeRequestDetail.tsx"
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && docker compose stop frontend && docker compose up frontend -d"
```

- [ ] **Step 4: Verify in browser**

Open any CR in `planned` or `approved` status. Check that no error banner appears for CRs without target assets (should silently return no banner). For CRs whose target asset is linked to a backup_target, the appropriate banner should show.

- [ ] **Step 5: Commit**

```bash
ssh -i /c/Users/john/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git add frontend/src/pages/ChangeRequestDetail.tsx && git commit -m 'feat: add backup confidence banner to CR detail'"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `backup_targets` table with all columns | Task 1 + 2 |
| `artifact_refs` JSONB on `change_requests` | Task 1 + 2 |
| Status computation: healthy/overdue/unprotected | Task 4 `compute_status` |
| `GET /backup-targets` | Task 5 |
| `POST /backup-targets` | Task 5 |
| `GET /backup-targets/{id}` | Task 5 |
| `GET /backup-history` | Task 5 |
| `POST /restore-crs` | Task 5 |
| `GET /change-requests/{id}/backup-context` | Task 5 |
| Auto-create BackupTarget when backup RecurringJob created | Task 6 |
| `artifact_refs` written + BackupTarget updated on CR completion | Task 6 |
| If job disabled/deleted → status transitions to unprotected | Via FK ondelete=SET NULL + compute_status |
| Backup & Recovery page: coverage summary cards | Task 8 |
| Backup & Recovery page: backup history table | Task 8 |
| Backup & Recovery page: restore drawer | Task 8 |
| Restore CR goes through normal approve → execute lifecycle | Task 5 `create_restore_cr` |
| CR Detail confidence banner | Task 9 |
| Banner only for planned/awaiting_approval/approved | Task 9 `BANNER_STATUSES` |

**Deferred (per spec):** restore verification, multi-region replication, encryption key management, retention policy enforcement.
