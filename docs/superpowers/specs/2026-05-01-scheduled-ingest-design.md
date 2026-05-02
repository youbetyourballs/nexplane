# Sub-project B: Scheduled Ingest — Design Spec

**Date:** 2026-05-01
**Status:** Approved
**Scope:** Recurring ingest for connectors via a simple interval picker (1h/6h/24h/weekly). APScheduler runs in-process. No external job queue needed at this scale.

---

## Design Decisions

- **In-process scheduler:** APScheduler (AsyncIOScheduler) started at FastAPI startup. Simple, no Redis/Celery dependency. Swap-ready if scale requires it.
- **DB-backed schedule store:** `ScheduledIngest` table is the source of truth. APScheduler loads schedules on startup and syncs on CRUD. No APScheduler job store — schedules are derived from DB on startup.
- **Interval-only (no cron):** Operator picks from fixed intervals. Stored as `interval_hours` int. Simpler UI, simpler validation, meets 95% of use cases.
- **Reuses IngestService:** Scheduled jobs call the existing `IngestService.run_ingest_action()` — no new ingest logic.

---

## Data Model

### New table: `ScheduledIngest`

```python
class ScheduledIngest(Base):
    __tablename__ = "scheduled_ingests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("connectors.id"), unique=True, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    action_id: Mapped[str] = mapped_column(String(255), nullable=False)  # which ingest action to run
    interval_hours: Mapped[int] = mapped_column(Integer, nullable=False)  # 1, 6, 24, 168 (weekly)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "success" | "error"
    last_run_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

---

## Backend API

New endpoints in `connectors.py`:

```
GET    /connectors/{id}/schedule        → {scheduled: bool, interval_hours: int, action_id: str, last_run_at, next_run_at, last_run_status}
PUT    /connectors/{id}/schedule        → body: {interval_hours: int, action_id: str}  → upserts schedule, starts/updates APScheduler job
DELETE /connectors/{id}/schedule        → removes schedule, cancels APScheduler job
```

Valid `interval_hours` values: `1, 6, 24, 168`. Any other value returns 422.

The `action_id` must be a valid ingest action for the connector type (validated against catalog).

---

## Scheduler Service (`backend/app/services/scheduler_service.py`)

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

async def start_scheduler(db_factory):
    """Load all enabled schedules from DB and register APScheduler jobs."""
    scheduler.start()
    async with db_factory() as db:
        schedules = await db.execute(select(ScheduledIngest).where(ScheduledIngest.enabled == True))
        for schedule in schedules.scalars():
            _register_job(schedule)

def _register_job(schedule: ScheduledIngest):
    scheduler.add_job(
        _run_ingest_job,
        trigger="interval",
        hours=schedule.interval_hours,
        id=str(schedule.id),
        args=[schedule.id],
        replace_existing=True,
        next_run_time=schedule.next_run_at or datetime.now(timezone.utc),
    )

async def _run_ingest_job(schedule_id: uuid.UUID):
    """Execute the scheduled ingest and update last_run_at / next_run_at."""
    async with db_factory() as db:
        schedule = await db.get(ScheduledIngest, schedule_id)
        connector = await db.get(Connector, schedule.connector_id)
        try:
            await ingest_service.run_ingest_action(schedule.action_id, connector, schedule.organization_id, db)
            schedule.last_run_status = "success"
            schedule.last_run_error = None
        except Exception as e:
            schedule.last_run_status = "error"
            schedule.last_run_error = str(e)
        schedule.last_run_at = datetime.now(timezone.utc)
        schedule.next_run_at = datetime.now(timezone.utc) + timedelta(hours=schedule.interval_hours)
        await db.commit()
```

Scheduler started in `main.py` lifespan:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await scheduler_service.start_scheduler(get_db)
    yield
    scheduler_service.scheduler.shutdown()
```

---

## Frontend

**Connector cards (`Connectors.tsx`):**

Each card with ingest actions gets a "Schedule" section below the "Run Discovery" button:
- If no schedule: "Set up recurring sync" link → opens `ScheduleModal`
- If scheduled: "Syncs every {interval}" badge + "Edit" link + last run status (✓ or ✗ with timestamp) + "Disable" button

**`ScheduleModal` component (`frontend/src/components/ScheduleModal.tsx`):**
```
Select interval:
○ Every hour
○ Every 6 hours  
● Every 24 hours
○ Weekly

Action: [dropdown of available ingest actions for this connector]

[Save Schedule]  [Cancel]
```

- On save: `PUT /connectors/{id}/schedule`
- "Remove schedule" link at bottom: `DELETE /connectors/{id}/schedule`

---

## New Files

| File | Purpose |
|------|---------|
| `backend/app/models/scheduled_ingest.py` | `ScheduledIngest` model |
| `backend/app/services/scheduler_service.py` | APScheduler wrapper + job management |
| `backend/app/schemas/scheduled_ingest.py` | `ScheduledIngestRead`, `ScheduledIngestWrite` |
| `backend/alembic/versions/008_scheduled_ingest.py` | Migration |
| `backend/app/tests/test_scheduled_ingest.py` | CRUD + scheduler tests |
| `frontend/src/components/ScheduleModal.tsx` | Schedule interval picker modal |

**Modified files:**
- `backend/app/routers/connectors.py` — schedule endpoints
- `backend/app/main.py` — start scheduler in lifespan
- `backend/requirements.txt` — add `apscheduler>=3.10.0`
- `frontend/src/pages/Connectors.tsx` — schedule status + button per card
- `frontend/src/types/api.ts` — `ScheduledIngest` type
