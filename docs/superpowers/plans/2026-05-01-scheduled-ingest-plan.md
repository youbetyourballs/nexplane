# Scheduled Ingest Implementation Plan (Sub-project B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add recurring ingest for connectors via a simple interval picker (1h/6h/24h/weekly) using APScheduler.

**Architecture:** New `ScheduledIngest` DB table is the source of truth. `SchedulerService` wraps APScheduler (AsyncIOScheduler), started in FastAPI lifespan. Schedule CRUD endpoints in `connectors.py`. Frontend adds a Schedule button + modal per connector card.

**Tech Stack:** Python APScheduler 3.x, FastAPI lifespan, SQLAlchemy async, React + TanStack Query.

**Working directory:** `f:\Nexplane\nexplane\.worktrees\remaining-features`

---

### Task 1: `ScheduledIngest` model and migration

**Files:**
- Create: `backend/app/models/scheduled_ingest.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/008_scheduled_ingest.py`

- [ ] **Step 1: Create `backend/app/models/scheduled_ingest.py`**

```python
import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, func, ForeignKey, Integer, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ScheduledIngest(Base):
    __tablename__ = "scheduled_ingests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connectors.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    action_id: Mapped[str] = mapped_column(String(255), nullable=False)
    interval_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_run_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    connector: Mapped["Connector"] = relationship("Connector")
```

- [ ] **Step 2: Add import to `backend/app/models/__init__.py`**

Add `from app.models.scheduled_ingest import ScheduledIngest` alongside other model imports.

- [ ] **Step 3: Create `backend/alembic/versions/008_scheduled_ingest.py`**

```python
"""add scheduled_ingests table

Revision ID: 008
Revises: 007
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'scheduled_ingests',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connector_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action_id', sa.String(255), nullable=False),
        sa.Column('interval_hours', sa.Integer(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_run_status', sa.String(50), nullable=True),
        sa.Column('last_run_error', sa.Text(), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('connector_id'),
    )


def downgrade() -> None:
    op.drop_table('scheduled_ingests')
```

- [ ] **Step 4: Write failing test**

```python
# backend/app/tests/test_scheduled_ingest.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_create_schedule(client: AsyncClient, operator_token, seeded_connector_id):
    resp = await client.put(
        f"/connectors/{seeded_connector_id}/schedule",
        json={"interval_hours": 6, "action_id": "ingest_computers"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["interval_hours"] == 6
    assert data["action_id"] == "ingest_computers"

@pytest.mark.asyncio
async def test_get_schedule(client: AsyncClient, operator_token, seeded_connector_id):
    resp = await client.get(
        f"/connectors/{seeded_connector_id}/schedule",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 200

@pytest.mark.asyncio
async def test_delete_schedule(client: AsyncClient, operator_token, seeded_connector_id):
    # Create then delete
    await client.put(
        f"/connectors/{seeded_connector_id}/schedule",
        json={"interval_hours": 24, "action_id": "ingest_computers"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    resp = await client.delete(
        f"/connectors/{seeded_connector_id}/schedule",
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 204

@pytest.mark.asyncio
async def test_invalid_interval_rejected(client: AsyncClient, operator_token, seeded_connector_id):
    resp = await client.put(
        f"/connectors/{seeded_connector_id}/schedule",
        json={"interval_hours": 5, "action_id": "ingest_computers"},
        headers={"Authorization": f"Bearer {operator_token}"},
    )
    assert resp.status_code == 422
```

Run: `docker compose exec backend python -m pytest app/tests/test_scheduled_ingest.py -v`
Expected: FAIL (no endpoints yet)

- [ ] **Step 5: Run migration**

```
docker compose exec backend alembic upgrade head
```

Expected: migration 008 applied

- [ ] **Step 6: Commit**

```
git add backend/app/models/scheduled_ingest.py backend/app/models/__init__.py backend/alembic/versions/008_scheduled_ingest.py backend/app/tests/test_scheduled_ingest.py
git commit -m "feat(schedule): add ScheduledIngest model and migration"
```

---

### Task 2: Scheduler service

**Files:**
- Create: `backend/app/services/scheduler_service.py`
- Create: `backend/app/schemas/scheduled_ingest.py`

- [ ] **Step 1: Create `backend/app/schemas/scheduled_ingest.py`**

```python
import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator


VALID_INTERVALS = {1, 6, 24, 168}


class ScheduledIngestWrite(BaseModel):
    interval_hours: int
    action_id: str

    @field_validator("interval_hours")
    @classmethod
    def validate_interval(cls, v: int) -> int:
        if v not in VALID_INTERVALS:
            raise ValueError(f"interval_hours must be one of {sorted(VALID_INTERVALS)}")
        return v


class ScheduledIngestRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    connector_id: uuid.UUID
    action_id: str
    interval_hours: int
    enabled: bool
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_error: str | None
    next_run_at: datetime | None
    created_at: datetime
```

- [ ] **Step 2: Create `backend/app/services/scheduler_service.py`**

```python
import uuid
import logging
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.scheduled_ingest import ScheduledIngest
from app.models.connector import Connector

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")
_db_factory = None


def init_scheduler(db_factory):
    """Call once at startup with the db session factory."""
    global _db_factory
    _db_factory = db_factory


async def start():
    """Load all enabled schedules from DB and start scheduler."""
    scheduler.start()
    logger.info("APScheduler started")
    if _db_factory is None:
        return
    async with _db_factory() as db:
        result = await db.execute(
            select(ScheduledIngest).where(ScheduledIngest.enabled == True)
        )
        schedules = result.scalars().all()
        for s in schedules:
            _register_job(s)
    logger.info(f"Loaded {len(schedules)} scheduled ingest jobs")


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def _register_job(schedule: ScheduledIngest):
    """Add or replace an APScheduler job for this schedule."""
    scheduler.add_job(
        _run_ingest_job,
        trigger="interval",
        hours=schedule.interval_hours,
        id=str(schedule.id),
        args=[str(schedule.id)],
        replace_existing=True,
        next_run_time=schedule.next_run_at or datetime.now(timezone.utc),
    )


def _remove_job(schedule_id: uuid.UUID):
    job_id = str(schedule_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def upsert_schedule(schedule: ScheduledIngest):
    """Call after creating/updating a schedule row."""
    _register_job(schedule)


async def remove_schedule(schedule_id: uuid.UUID):
    """Call after deleting a schedule row."""
    _remove_job(schedule_id)


async def _run_ingest_job(schedule_id: str):
    """Execute the scheduled ingest and update last_run metadata."""
    from app.services.ingest_service import IngestService

    if _db_factory is None:
        return

    async with _db_factory() as db:
        schedule = await db.get(ScheduledIngest, uuid.UUID(schedule_id))
        if not schedule or not schedule.enabled:
            return

        connector = await db.get(Connector, schedule.connector_id)
        if not connector:
            return

        try:
            ingest_svc = IngestService(db)
            await ingest_svc.run_ingest_action(
                action_id=schedule.action_id,
                connector=connector,
                organization_id=schedule.organization_id,
            )
            schedule.last_run_status = "success"
            schedule.last_run_error = None
        except Exception as e:
            schedule.last_run_status = "error"
            schedule.last_run_error = str(e)[:500]
            logger.error(f"Scheduled ingest {schedule_id} failed: {e}")

        schedule.last_run_at = datetime.now(timezone.utc)
        schedule.next_run_at = datetime.now(timezone.utc) + timedelta(hours=schedule.interval_hours)
        await db.commit()
```

- [ ] **Step 3: Commit**

```
git add backend/app/services/scheduler_service.py backend/app/schemas/scheduled_ingest.py
git commit -m "feat(schedule): add scheduler service and schema"
```

---

### Task 3: Schedule CRUD endpoints and FastAPI lifespan wiring

**Files:**
- Modify: `backend/app/routers/connectors.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add schedule endpoints to `backend/app/routers/connectors.py`**

Add after existing connector endpoints:

```python
from sqlalchemy import select
from app.models.scheduled_ingest import ScheduledIngest
from app.schemas.scheduled_ingest import ScheduledIngestRead, ScheduledIngestWrite
from app import scheduler_service


@router.get("/{connector_id}/schedule", response_model=ScheduledIngestRead | None)
async def get_schedule(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_operator),
):
    connector = await _get_connector_or_404(connector_id, current_user.organization_id, db)
    result = await db.execute(
        select(ScheduledIngest).where(ScheduledIngest.connector_id == connector.id)
    )
    return result.scalar_one_or_none()


@router.put("/{connector_id}/schedule", response_model=ScheduledIngestRead)
async def upsert_schedule(
    connector_id: uuid.UUID,
    body: ScheduledIngestWrite,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_operator),
):
    from datetime import datetime, timezone, timedelta
    connector = await _get_connector_or_404(connector_id, current_user.organization_id, db)

    result = await db.execute(
        select(ScheduledIngest).where(ScheduledIngest.connector_id == connector.id)
    )
    schedule = result.scalar_one_or_none()

    if schedule:
        schedule.interval_hours = body.interval_hours
        schedule.action_id = body.action_id
        schedule.next_run_at = datetime.now(timezone.utc) + timedelta(hours=body.interval_hours)
    else:
        schedule = ScheduledIngest(
            connector_id=connector.id,
            organization_id=current_user.organization_id,
            action_id=body.action_id,
            interval_hours=body.interval_hours,
            next_run_at=datetime.now(timezone.utc) + timedelta(hours=body.interval_hours),
        )
        db.add(schedule)

    await db.commit()
    await db.refresh(schedule)
    await scheduler_service.upsert_schedule(schedule)
    return schedule


@router.delete("/{connector_id}/schedule", status_code=204)
async def delete_schedule(
    connector_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(require_operator),
):
    connector = await _get_connector_or_404(connector_id, current_user.organization_id, db)
    result = await db.execute(
        select(ScheduledIngest).where(ScheduledIngest.connector_id == connector.id)
    )
    schedule = result.scalar_one_or_none()
    if schedule:
        await scheduler_service.remove_schedule(schedule.id)
        await db.delete(schedule)
        await db.commit()
```

- [ ] **Step 2: Update `backend/app/main.py` lifespan**

Find the existing lifespan or startup event and add scheduler:

```python
from contextlib import asynccontextmanager
from app.services import scheduler_service
from app.database import AsyncSessionLocal

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_service.init_scheduler(lambda: AsyncSessionLocal())
    await scheduler_service.start()
    yield
    scheduler_service.stop()

app = FastAPI(lifespan=lifespan, ...)
```

If `main.py` already has a lifespan, add the scheduler calls inside the existing context manager.

- [ ] **Step 3: Add `apscheduler>=3.10.0` to `backend/requirements.txt`**

- [ ] **Step 4: Run tests**

```
docker compose exec backend python -m pytest app/tests/test_scheduled_ingest.py -v
```

Expected: all 4 tests PASS

- [ ] **Step 5: Run full backend test suite**

```
docker compose exec backend python -m pytest app/tests/ -q
```

Expected: 116+ passed

- [ ] **Step 6: Commit**

```
git add backend/app/routers/connectors.py backend/app/main.py backend/requirements.txt
git commit -m "feat(schedule): add schedule CRUD endpoints and APScheduler lifespan wiring"
```

---

### Task 4: Frontend — ScheduleModal and connector card schedule status

**Files:**
- Create: `frontend/src/components/ScheduleModal.tsx`
- Modify: `frontend/src/pages/Connectors.tsx`
- Modify: `frontend/src/types/api.ts`

- [ ] **Step 1: Add `ScheduledIngest` type to `frontend/src/types/api.ts`**

```typescript
export interface ScheduledIngest {
  id: string;
  connector_id: string;
  action_id: string;
  interval_hours: 1 | 6 | 24 | 168;
  enabled: boolean;
  last_run_at: string | null;
  last_run_status: 'success' | 'error' | null;
  last_run_error: string | null;
  next_run_at: string | null;
  created_at: string;
}
```

- [ ] **Step 2: Create `frontend/src/components/ScheduleModal.tsx`**

```tsx
import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { ConnectorRead, ScheduledIngest } from '../types/api';

const INTERVAL_OPTIONS = [
  { value: 1, label: 'Every hour' },
  { value: 6, label: 'Every 6 hours' },
  { value: 24, label: 'Every 24 hours' },
  { value: 168, label: 'Weekly' },
];

interface Props {
  connector: ConnectorRead;
  existing: ScheduledIngest | null;
  ingestActions: Array<{ action_id: string; display_name: string }>;
  onClose: () => void;
  token: string;
}

export default function ScheduleModal({ connector, existing, ingestActions, onClose, token }: Props) {
  const [intervalHours, setIntervalHours] = useState<number>(existing?.interval_hours ?? 24);
  const [actionId, setActionId] = useState<string>(existing?.action_id ?? ingestActions[0]?.action_id ?? '');
  const queryClient = useQueryClient();

  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };

  const saveMutation = useMutation({
    mutationFn: () =>
      fetch(`/connectors/${connector.id}/schedule`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ interval_hours: intervalHours, action_id: actionId }),
      }).then(r => { if (!r.ok) throw new Error('Failed to save schedule'); return r.json(); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedule', connector.id] });
      onClose();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () =>
      fetch(`/connectors/${connector.id}/schedule`, { method: 'DELETE', headers }).then(r => {
        if (!r.ok && r.status !== 204) throw new Error('Failed to remove schedule');
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedule', connector.id] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 className="text-lg font-semibold mb-4">Schedule Discovery — {connector.name}</h2>

        <div className="mb-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">Sync interval</label>
          <div className="space-y-2">
            {INTERVAL_OPTIONS.map(opt => (
              <label key={opt.value} className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="interval"
                  value={opt.value}
                  checked={intervalHours === opt.value}
                  onChange={() => setIntervalHours(opt.value)}
                  className="accent-indigo-600"
                />
                <span className="text-sm text-gray-700">{opt.label}</span>
              </label>
            ))}
          </div>
        </div>

        {ingestActions.length > 1 && (
          <div className="mb-4">
            <label className="block text-sm font-medium text-gray-700 mb-1">Action</label>
            <select
              value={actionId}
              onChange={e => setActionId(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              {ingestActions.map(a => (
                <option key={a.action_id} value={a.action_id}>{a.display_name}</option>
              ))}
            </select>
          </div>
        )}

        <div className="flex gap-3 mt-6">
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex-1 bg-indigo-600 text-white rounded-md py-2 text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {saveMutation.isPending ? 'Saving...' : 'Save Schedule'}
          </button>
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900">
            Cancel
          </button>
        </div>

        {existing && (
          <div className="mt-4 pt-4 border-t border-gray-100">
            <button
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
              className="text-sm text-red-500 hover:text-red-700"
            >
              Remove schedule
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Update `frontend/src/pages/Connectors.tsx` to add schedule status per card**

For each connector card that has ingest actions, add:

```tsx
import { useQuery } from '@tanstack/react-query';
import ScheduleModal from '../components/ScheduleModal';

// Inside connector card render, alongside existing "Run Discovery" button:
const { data: schedule } = useQuery({
  queryKey: ['schedule', connector.id],
  queryFn: () => fetch(`/connectors/${connector.id}/schedule`, {
    headers: { Authorization: `Bearer ${token}` }
  }).then(r => r.json()),
  enabled: hasIngestActions,
});

// Schedule status display:
{hasIngestActions && (
  <div className="mt-2 flex items-center gap-2">
    {schedule ? (
      <>
        <span className="text-xs text-green-600">
          ⏰ Syncs {INTERVAL_OPTIONS.find(o => o.value === schedule.interval_hours)?.label.toLowerCase()}
        </span>
        {schedule.last_run_status === 'error' && (
          <span className="text-xs text-red-500" title={schedule.last_run_error ?? ''}>✗ Last run failed</span>
        )}
        {schedule.last_run_status === 'success' && (
          <span className="text-xs text-gray-400">✓ {new Date(schedule.last_run_at!).toLocaleString()}</span>
        )}
        <button onClick={() => setScheduleModalConnector(connector)} className="text-xs text-indigo-600 hover:underline">
          Edit
        </button>
      </>
    ) : (
      <button
        onClick={() => setScheduleModalConnector(connector)}
        className="text-xs text-gray-500 hover:text-indigo-600"
      >
        + Schedule recurring sync
      </button>
    )}
  </div>
)}
```

Add `scheduleModalConnector` state and modal rendering at the bottom of the component.

- [ ] **Step 4: Build frontend to verify no TypeScript errors**

```
cd frontend && npm run build 2>&1 | tail -10
```

Expected: Build succeeds (or only pre-existing warnings)

- [ ] **Step 5: Commit**

```
git add frontend/src/components/ScheduleModal.tsx frontend/src/pages/Connectors.tsx frontend/src/types/api.ts
git commit -m "feat(schedule): add ScheduleModal and connector card schedule status UI"
```

---

### Task 5: Final verification

- [ ] **Step 1: Run all backend tests**

```
docker compose exec backend python -m pytest app/tests/ -v --tb=short 2>&1 | tail -15
```

Expected: all pass

- [ ] **Step 2: Verify frontend builds**

```
cd frontend && npm run build 2>&1 | tail -5
```

- [ ] **Step 3: Commit any final fixes**

```
git add -A && git commit -m "feat(schedule): scheduled ingest complete — APScheduler + CRUD + UI" --allow-empty
```
