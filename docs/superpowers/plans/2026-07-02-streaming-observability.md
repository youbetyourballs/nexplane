# Streaming Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add step-level execution log storage, an SSE log-tail endpoint, an execution metrics endpoint, and a real-time LogViewer UI component to the CR detail page.

**Architecture:** A new `cr_execution_logs` table captures per-step log rows written directly from `activities.py`. Two new GET endpoints expose these logs — one as an SSE stream (`/logs/stream`) and one as a JSON metrics snapshot (`/metrics`). A React `LogViewer` component connects to both when a CR is active, showing a live scrolling log pane with a progress bar.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, PostgreSQL (JSONB), Alembic, sse-starlette, React 18, TypeScript, Tailwind CSS, @tanstack/react-query, native browser EventSource API.

## Global Constraints

- All new Python files start with SPDX header: `# SPDX-License-Identifier: AGPL-3.0-only` / `# Copyright (C) 2024-2026 Nexplane, Inc.`
- All new TypeScript/TSX files start with: `// SPDX-License-Identifier: AGPL-3.0-only` / `// Copyright (C) 2024-2026 Nexplane, Inc.`
- Alembic migration revision ID: `obs001`; `down_revision` must be a tuple `("tunnel002", "notif001")` to merge both open heads
- `sse-starlette>=1.6.5` must be added to `backend/requirements.txt`
- No mocks for infra — unit tests mock DB only via `AsyncMock`/`MagicMock` as established in `backend/tests/test_change_execution_service.py`
- Frontend restart required after any change to `frontend/src/`: `docker compose stop frontend && docker compose up frontend -d`

---

## File Map

**Created:**
- `backend/app/models/cr_execution_log.py` — SQLAlchemy model for `cr_execution_logs`
- `backend/alembic/versions/obs001_cr_execution_logs.py` — Alembic migration
- `backend/app/services/log_service.py` — `emit_log()` async helper
- `backend/tests/test_cr_execution_logs.py` — unit tests for log emission and metrics calculation
- `frontend/src/components/LogViewer.tsx` — React SSE log viewer + progress bar component

**Modified:**
- `backend/requirements.txt` — add `sse-starlette>=1.6.5`
- `backend/app/models/__init__.py` — import `CRExecutionLog`
- `backend/app/workflows/activities.py` — call `emit_log()` at step boundaries
- `backend/app/routers/change_requests.py` — add `/logs/stream` and `/metrics` endpoints
- `frontend/src/api/endpoints.ts` — add `getMetrics()` to `changeRequestsApi`
- `frontend/src/pages/ChangeRequestDetail.tsx` — mount `<LogViewer>` below the Execution Run section

---

## Task 1: Add sse-starlette to requirements and create the log model + migration

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/app/models/cr_execution_log.py`
- Create: `backend/alembic/versions/obs001_cr_execution_logs.py`
- Modify: `backend/app/models/__init__.py`

**Interfaces:**
- Produces: `CRExecutionLog` SQLAlchemy model importable as `from app.models.cr_execution_log import CRExecutionLog`
- Columns available downstream: `id` (str UUID PK), `change_request_id` (str UUID FK), `step_name` (str), `level` (str: "info"|"warning"|"error"), `message` (str), `timestamp` (datetime with tz), `metadata` (dict|None)

- [ ] **Step 1: Add sse-starlette to requirements.txt**

Open `backend/requirements.txt`. After the `mcp[cli]>=1.0.0` line, add:

```
sse-starlette>=1.6.5
```

- [ ] **Step 2: Create the CRExecutionLog model**

Create `backend/app/models/cr_execution_log.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime
from sqlalchemy import String, Text, DateTime, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class CRExecutionLog(Base):
    __tablename__ = "cr_execution_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    change_request_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("change_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

- [ ] **Step 3: Register the model in __init__.py**

Open `backend/app/models/__init__.py`. After the last import line (`from app.models.asset_dependency import AssetDependency  # noqa: F401`), add:

```python
from app.models.cr_execution_log import CRExecutionLog  # noqa: F401
```

- [ ] **Step 4: Write the Alembic migration**

Create `backend/alembic/versions/obs001_cr_execution_logs.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add cr_execution_logs table

Revision ID: obs001
Revises: tunnel002, notif001
Create Date: 2026-07-02
"""
from typing import Union
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "obs001"
down_revision: Union[tuple, None] = ("tunnel002", "notif001")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cr_execution_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "change_request_id",
            sa.String(36),
            sa.ForeignKey("change_requests.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("step_name", sa.String(255), nullable=False),
        sa.Column("level", sa.String(16), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
    )
    op.create_index(
        "ix_cr_execution_logs_cr_id_id",
        "cr_execution_logs",
        ["change_request_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_cr_execution_logs_cr_id_id", table_name="cr_execution_logs")
    op.drop_table("cr_execution_logs")
```

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/app/models/cr_execution_log.py backend/app/models/__init__.py backend/alembic/versions/obs001_cr_execution_logs.py
git commit -m "feat(obs): cr_execution_logs model and migration obs001"
```

---

## Task 2: log_service — emit_log() helper

**Files:**
- Create: `backend/app/services/log_service.py`

**Interfaces:**
- Consumes: `CRExecutionLog` from `app.models.cr_execution_log`
- Produces: `async def emit_log(change_request_id: str, step_name: str, level: str, message: str, metadata: dict | None = None) -> None`
  - Opens its own `AsyncSessionLocal` session, inserts one `CRExecutionLog` row, commits, closes. Never raises — swallows exceptions and logs to Python `logging` so a log failure never kills a step.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_cr_execution_logs.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_emit_log_inserts_row():
    """emit_log creates a CRExecutionLog row and commits."""
    cr_id = str(uuid.uuid4())

    mock_log = MagicMock()
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.log_service.AsyncSessionLocal", return_value=mock_db), \
         patch("app.services.log_service.CRExecutionLog", return_value=mock_log) as MockCRLog:
        from app.services.log_service import emit_log
        await emit_log(cr_id, "apply_sg_rule", "info", "Step started")
        MockCRLog.assert_called_once_with(
            change_request_id=cr_id,
            step_name="apply_sg_rule",
            level="info",
            message="Step started",
            metadata=None,
        )
        mock_db.add.assert_called_once_with(mock_log)
        mock_db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_emit_log_swallows_exceptions():
    """emit_log never raises — DB errors are swallowed."""
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock(side_effect=RuntimeError("db down"))

    with patch("app.services.log_service.AsyncSessionLocal", return_value=mock_db):
        from app.services.log_service import emit_log
        # Must not raise
        await emit_log("cr-id", "step", "error", "boom")


@pytest.mark.asyncio
async def test_emit_log_with_metadata():
    """emit_log passes metadata dict through to the model."""
    cr_id = str(uuid.uuid4())
    meta = {"elapsed_ms": 1234}

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    with patch("app.services.log_service.AsyncSessionLocal", return_value=mock_db), \
         patch("app.services.log_service.CRExecutionLog") as MockCRLog:
        from app.services.log_service import emit_log
        await emit_log(cr_id, "step", "info", "done", metadata=meta)
        _, kwargs = MockCRLog.call_args
        assert kwargs["metadata"] == meta
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd f:/Nexplane/nexplane/backend && python -m pytest tests/test_cr_execution_logs.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError` or `ImportError` for `app.services.log_service`.

- [ ] **Step 3: Implement log_service.py**

Create `backend/app/services/log_service.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
log_service — lightweight helper to persist per-step execution logs.

Never raises: a logging failure must never abort a CR step.
"""
import logging
from typing import Optional

from app.database import AsyncSessionLocal
from app.models.cr_execution_log import CRExecutionLog

logger = logging.getLogger(__name__)


async def emit_log(
    change_request_id: str,
    step_name: str,
    level: str,
    message: str,
    metadata: Optional[dict] = None,
) -> None:
    """Insert a single CRExecutionLog row. Swallows all exceptions."""
    try:
        async with AsyncSessionLocal() as db:
            row = CRExecutionLog(
                change_request_id=change_request_id,
                step_name=step_name,
                level=level,
                message=message,
                metadata=metadata,
            )
            db.add(row)
            await db.commit()
    except Exception as exc:
        logger.warning("emit_log failed (cr=%s step=%s): %s", change_request_id, step_name, exc)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd f:/Nexplane/nexplane/backend && python -m pytest tests/test_cr_execution_logs.py::test_emit_log_inserts_row tests/test_cr_execution_logs.py::test_emit_log_swallows_exceptions tests/test_cr_execution_logs.py::test_emit_log_with_metadata -v
```

Expected: all 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/log_service.py backend/tests/test_cr_execution_logs.py
git commit -m "feat(obs): emit_log() helper + unit tests"
```

---

## Task 3: Emit logs in activities.py step loop

**Files:**
- Modify: `backend/app/workflows/activities.py`

**Interfaces:**
- Consumes: `emit_log` from `app.services.log_service`
- Produces: log rows written to `cr_execution_logs` at four points per step: start, complete (with elapsed ms), error (with traceback), and rollback

- [ ] **Step 1: Add the import to activities.py**

Open `backend/app/workflows/activities.py`. After the existing imports block (after `from app.services import connector_service`), add:

```python
from app.services.log_service import emit_log
```

- [ ] **Step 2: Instrument the execute_steps step loop**

In `activity_execute_steps` (line ~174), the step loop starts at `for step in generated_steps:`. Replace the `try/except` block that calls `execute_action` (lines ~230–238) so it records timing and emits log events. The full modified loop body (replacing from `for step in generated_steps:` through `logger.info("Step %s (%s) completed"...)` at line ~259) should be:

```python
    for step in generated_steps:
        connector_type = step.get("connector_type", "")
        action_id = step.get("action_id", "")
        plan_params = step.get("parameters", {})
        parameters = {
            **plan_params,
            **{k: v for k, v in carried_context.items() if not plan_params.get(k)},
        }
        step_connector_id = step.get("connector_id")
        step_label = f"{step.get('step_number', '?')}:{action_id}"

        connector = None
        if step_connector_id:
            result = await db.execute(
                select(Connector).where(Connector.id == uuid.UUID(step_connector_id))
            )
            connector = result.scalar_one_or_none()

        if connector is None and connector_type and connector_type not in ("", "unknown"):
            try:
                _ct_enum = ConnectorType(connector_type)
                _cr_result = await db.execute(
                    select(ChangeRequest).where(ChangeRequest.id == uuid.UUID(change_request_id))
                )
                _cr_obj = _cr_result.scalar_one_or_none()
                if _cr_obj:
                    _conn_result = await db.execute(
                        select(Connector).where(
                            Connector.organization_id == _cr_obj.organization_id,
                            Connector.connector_type == _ct_enum,
                        ).order_by(Connector.created_at.desc()).limit(1)
                    )
                    _fallback = _conn_result.scalar_one_or_none()
                    if _fallback:
                        connector = _fallback
                        await connector_service._attach_credentials(connector, db)
                        logger.info("Step %s: using fallback connector %s (type=%s)",
                                    step.get("step_number"), connector.id, connector_type)
            except Exception as _lookup_exc:
                logger.debug("Fallback connector lookup failed: %s", _lookup_exc)

        if connector and (not connector_type or connector_type == "unknown"):
            connector_type = connector.connector_type.value

        await emit_log(change_request_id, step_label, "info",
                       f"Starting step: {action_id}")

        import time as _time
        _t0 = _time.monotonic()
        try:
            result = await execute_action(
                connector_type, action_id, parameters, asset_ids,
                connector=connector, db=db if connector is not None else None,
            )
        except Exception as exc:
            import traceback as _tb
            _tb_str = _tb.format_exc()
            logger.error("Step %s failed: %s\n%s", step.get("step_number"), exc, _tb_str)
            await emit_log(change_request_id, step_label, "error",
                           f"Step failed: {exc}",
                           metadata={"traceback": _tb_str})
            raise

        elapsed_ms = int((_time.monotonic() - _t0) * 1000)

        # Commit any flushed _auto_asset upserts from execute_action
        await db.commit()

        step_results.append({
            "step_number": step["step_number"],
            "generic_action": step.get("generic_action"),
            "action_id": action_id,
            "connector_type": connector_type,
            "connector_id": str(connector.id) if connector else step_connector_id,
            "result": result,
        })
        if isinstance(result, dict) and "error" not in result:
            carried_context.update({
                k: v for k, v in result.items()
                if k not in ("action", "_auto_asset") and isinstance(v, (str, int, float, bool, list))
            })

        await emit_log(change_request_id, step_label, "info",
                       f"Step completed: {action_id} in {elapsed_ms}ms",
                       metadata={"elapsed_ms": elapsed_ms})
        logger.info("Step %s (%s) completed", step.get("step_number"), action_id)
```

- [ ] **Step 3: Instrument the rollback loop**

In `activity_execute_rollback` (line ~293), inside the `for step in reversed(generated_steps):` loop, after `rollback_action` and `rollback_connector` are checked and the `execute_action` call succeeds (around line ~381), add emit calls. Find the section where `rollback_results.append(...)` happens (line ~402) and insert the emit calls immediately before and after the `execute_action` call:

Locate this block (around lines 378–408 in the original file):
```python
            rollback_params = {
                ...
            }
                result = await execute_action(
                    rollback_connector, rollback_action, rollback_params, [],
                    ...
                )
```

Add `emit_log` calls wrapping the `execute_action`:

```python
            step_label = f"rollback:{step.get('step_number', '?')}:{rollback_action}"
            await emit_log(change_request_id, step_label, "info",
                           f"Rolling back step: {rollback_action}")
            try:
                result = await execute_action(
                    rollback_connector, rollback_action, rollback_params, [],
                    connector=connector, db=db,
                )
            except Exception as _rb_exc:
                import traceback as _tb
                await emit_log(change_request_id, step_label, "error",
                               f"Rollback step failed: {_rb_exc}",
                               metadata={"traceback": _tb.format_exc()})
                raise
            await emit_log(change_request_id, step_label, "info",
                           f"Rollback step completed: {rollback_action}")
```

Note: the `import time as _time` is already added inside the execute loop above; for rollback you don't need timing, just start/complete/error events.

- [ ] **Step 4: Verify the file compiles**

```bash
cd f:/Nexplane/nexplane/backend && python -c "import app.workflows.activities; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Run existing activity-related tests**

```bash
cd f:/Nexplane/nexplane/backend && python -m pytest tests/test_change_execution_service.py -v
```

Expected: all tests PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflows/activities.py
git commit -m "feat(obs): emit step logs from activities.py execute and rollback loops"
```

---

## Task 4: SSE log-tail endpoint and metrics endpoint

**Files:**
- Modify: `backend/app/routers/change_requests.py`

**Interfaces:**
- Consumes: `CRExecutionLog` model, `ChangeRequest` model, `ExecutionRun` model
- Produces:
  - `GET /change-requests/{cr_id}/logs/stream` — SSE stream; each event: `data: {"id": str, "step_name": str, "level": str, "message": str, "timestamp": str}` (ISO 8601); terminal event: `event: done\ndata: {}`
  - `GET /change-requests/{cr_id}/metrics` — JSON: `{"steps_total": int, "steps_completed": int, "steps_failed": int, "elapsed_seconds": float | null, "estimated_remaining_seconds": float | null, "current_step": str | null}`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_cr_execution_logs.py`:

```python
@pytest.mark.asyncio
async def test_metrics_steps_completed_and_failed():
    """Metrics counts completed and failed steps from log rows."""
    from app.routers.change_requests import _compute_metrics

    logs = [
        MagicMock(step_name="1:step_a", level="info", message="Step completed: step_a in 100ms"),
        MagicMock(step_name="1:step_a", level="info", message="Starting step: step_a"),
        MagicMock(step_name="2:step_b", level="error", message="Step failed: something"),
    ]
    result = _compute_metrics(
        logs=logs,
        total_steps=3,
        started_at=None,
    )
    assert result["steps_total"] == 3
    assert result["steps_completed"] == 1
    assert result["steps_failed"] == 1
    assert result["current_step"] is None  # last log is an error, not a "Starting step"


@pytest.mark.asyncio
async def test_metrics_current_step_from_starting_log():
    """current_step is extracted from the most recent 'Starting step:' log."""
    from app.routers.change_requests import _compute_metrics

    logs = [
        MagicMock(step_name="1:step_a", level="info", message="Step completed: step_a in 100ms"),
        MagicMock(step_name="2:step_b", level="info", message="Starting step: step_b"),
    ]
    result = _compute_metrics(logs=logs, total_steps=5, started_at=None)
    assert result["current_step"] == "step_b"
    assert result["steps_completed"] == 1
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd f:/Nexplane/nexplane/backend && python -m pytest tests/test_cr_execution_logs.py::test_metrics_steps_completed_and_failed tests/test_cr_execution_logs.py::test_metrics_current_step_from_starting_log -v 2>&1 | head -20
```

Expected: `ImportError` — `_compute_metrics` doesn't exist yet.

- [ ] **Step 3: Add imports and _compute_metrics helper to change_requests.py**

Open `backend/app/routers/change_requests.py`. Add to the imports block at the top:

```python
import asyncio
from datetime import timezone
from sqlalchemy import select, update, func as sa_func
from sse_starlette.sse import EventSourceResponse
from app.models.cr_execution_log import CRExecutionLog
```

Note: `select` and `update` are already imported — only add the new ones (`asyncio`, `EventSourceResponse`, `CRExecutionLog`, `sa_func`). The `timezone` import may already exist; add if missing.

Then add the `_compute_metrics` helper function anywhere before the route definitions (e.g., after `_get_cr`):

```python
def _compute_metrics(
    logs: list,
    total_steps: int,
    started_at,
) -> dict:
    """Derive execution metrics from a list of CRExecutionLog rows (or mocks with same attrs)."""
    steps_completed = 0
    steps_failed = 0
    current_step = None
    step_durations: list[float] = []

    for row in logs:
        msg = row.message or ""
        if msg.startswith("Step completed:"):
            steps_completed += 1
            # Extract elapsed_ms from "Step completed: action_id in NNNms"
            try:
                ms_part = msg.rsplit(" in ", 1)[-1].rstrip("ms")
                step_durations.append(float(ms_part) / 1000)
            except (ValueError, IndexError):
                pass
        elif row.level == "error" and msg.startswith("Step failed:"):
            steps_failed += 1
        elif msg.startswith("Starting step:"):
            # Extract action_id from "Starting step: action_id"
            current_step = msg.removeprefix("Starting step:").strip()

    # If the last event was a completion or failure, current_step should be None
    # (we already iterated in order, so current_step is whatever the last Starting log set)
    # If the step that was "starting" subsequently completed, reset current_step:
    # We need to check if current_step was completed in the logs after its "Starting" entry.
    # Simple approach: walk logs in order, track current_step, clear it on completion/error.
    current_step = None
    for row in logs:
        msg = row.message or ""
        if msg.startswith("Starting step:"):
            current_step = msg.removeprefix("Starting step:").strip()
        elif msg.startswith("Step completed:") or (row.level == "error" and msg.startswith("Step failed:")):
            current_step = None

    elapsed_seconds = None
    if started_at is not None:
        now = datetime.now(timezone.utc)
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        elapsed_seconds = (now - started_at).total_seconds()

    estimated_remaining = None
    steps_done = steps_completed + steps_failed
    steps_pending = total_steps - steps_done
    if step_durations and steps_pending > 0 and elapsed_seconds is not None:
        avg_step_s = sum(step_durations) / len(step_durations)
        estimated_remaining = avg_step_s * steps_pending

    return {
        "steps_total": total_steps,
        "steps_completed": steps_completed,
        "steps_failed": steps_failed,
        "elapsed_seconds": elapsed_seconds,
        "estimated_remaining_seconds": estimated_remaining,
        "current_step": current_step,
    }
```

Also add the missing `from datetime import datetime, timezone` if `timezone` isn't already imported — check line 5 of the file; the existing import is `from datetime import datetime, timezone`.

- [ ] **Step 4: Run metrics tests to confirm they pass**

```bash
cd f:/Nexplane/nexplane/backend && python -m pytest tests/test_cr_execution_logs.py::test_metrics_steps_completed_and_failed tests/test_cr_execution_logs.py::test_metrics_current_step_from_starting_log -v
```

Expected: both PASS.

- [ ] **Step 5: Add the /metrics endpoint**

At the end of `backend/app/routers/change_requests.py` (before or after any existing fleet endpoints), add:

```python
@router.get("/{cr_id}/metrics")
async def get_execution_metrics(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a snapshot of execution progress metrics for a CR."""
    cr = await _get_cr(db, cr_id, user.organization_id)

    # Count total planned steps
    total_steps = 0
    if cr.change_plan and cr.change_plan.generated_steps:
        total_steps = len(cr.change_plan.generated_steps)

    # Fetch all logs for this CR ordered by id (insertion order)
    log_result = await db.execute(
        select(CRExecutionLog)
        .where(CRExecutionLog.change_request_id == str(cr_id))
        .order_by(CRExecutionLog.id)
    )
    logs = log_result.scalars().all()

    # Find started_at from the most recent execution run
    started_at = None
    if cr.execution_runs:
        latest_run = sorted(cr.execution_runs, key=lambda r: r.started_at, reverse=True)[0]
        started_at = latest_run.started_at

    return _compute_metrics(logs=logs, total_steps=total_steps, started_at=started_at)
```

- [ ] **Step 6: Add the /logs/stream SSE endpoint**

After the `/metrics` endpoint, add:

```python
_TERMINAL_STATUSES = frozenset([
    "completed", "failed", "rolled_back", "cancelled", "rejected",
    "completed_with_errors", "batch_aborted",
])


@router.get("/{cr_id}/logs/stream")
async def stream_execution_logs(
    cr_id: uuid.UUID,
    since_id: str | None = None,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """SSE endpoint — streams new CRExecutionLog rows as they are written.

    Sends a terminal 'done' event and closes when the CR reaches a terminal
    state and no new rows have arrived for 5 seconds.
    """
    # Auth check — raises 404 if CR not found in org
    await _get_cr(db, cr_id, user.organization_id)

    async def event_generator():
        last_id: str | None = since_id
        idle_since: float | None = None
        IDLE_TIMEOUT = 5.0
        POLL_INTERVAL = 0.5

        while True:
            # Fresh DB session per poll to avoid stale reads
            async with AsyncSessionLocal() as poll_db:
                # Check CR status
                cr_result = await poll_db.execute(
                    select(ChangeRequest).where(ChangeRequest.id == cr_id)
                )
                cr = cr_result.scalar_one_or_none()
                if cr is None:
                    break
                is_terminal = cr.status.value in _TERMINAL_STATUSES

                # Fetch new log rows
                q = select(CRExecutionLog).where(
                    CRExecutionLog.change_request_id == str(cr_id)
                ).order_by(CRExecutionLog.id)
                if last_id is not None:
                    q = q.where(CRExecutionLog.id > last_id)
                log_result = await poll_db.execute(q)
                rows = log_result.scalars().all()

            if rows:
                idle_since = None
                for row in rows:
                    last_id = row.id
                    payload = {
                        "id": row.id,
                        "step_name": row.step_name,
                        "level": row.level,
                        "message": row.message,
                        "timestamp": row.timestamp.isoformat(),
                    }
                    yield {"data": __import__("json").dumps(payload)}
            else:
                if is_terminal:
                    if idle_since is None:
                        idle_since = asyncio.get_event_loop().time()
                    elif asyncio.get_event_loop().time() - idle_since >= IDLE_TIMEOUT:
                        yield {"event": "done", "data": "{}"}
                        break

            await asyncio.sleep(POLL_INTERVAL)

    return EventSourceResponse(event_generator())
```

Also add `from app.database import AsyncSessionLocal` to the imports in `change_requests.py` if it isn't already imported. Check the top of the file — if not present, add it after the other `app` imports.

- [ ] **Step 7: Verify the file imports correctly**

```bash
cd f:/Nexplane/nexplane/backend && python -c "from app.routers.change_requests import router; print('OK')"
```

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add backend/app/routers/change_requests.py backend/tests/test_cr_execution_logs.py
git commit -m "feat(obs): /metrics and /logs/stream endpoints"
```

---

## Task 5: Frontend — API client, LogViewer component, CR detail integration

**Files:**
- Modify: `frontend/src/api/endpoints.ts`
- Create: `frontend/src/components/LogViewer.tsx`
- Modify: `frontend/src/pages/ChangeRequestDetail.tsx`

**Interfaces:**
- Consumes: `GET /change-requests/{id}/metrics` (JSON), `GET /change-requests/{id}/logs/stream` (SSE)
- Produces: `<LogViewer crId={string} crStatus={string} />` — self-contained component; connects/disconnects based on crStatus; renders log lines and progress bar

- [ ] **Step 1: Add getMetrics to the API client**

Open `frontend/src/api/endpoints.ts`. In the `changeRequestsApi` object, after the `getProgress` line, add:

```typescript
  getMetrics: (id: string) =>
    apiClient.get<{
      steps_total: number;
      steps_completed: number;
      steps_failed: number;
      elapsed_seconds: number | null;
      estimated_remaining_seconds: number | null;
      current_step: string | null;
    }>(`/change-requests/${id}/metrics`).then((r) => r.data),
```

- [ ] **Step 2: Create LogViewer.tsx**

Create `frontend/src/components/LogViewer.tsx`:

```tsx
// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { changeRequestsApi } from "../api/endpoints";

interface LogLine {
  id: string;
  step_name: string;
  level: "info" | "warning" | "error";
  message: string;
  timestamp: string;
}

interface Props {
  crId: string;
  crStatus: string;
}

const ACTIVE_STATUSES = new Set(["executing", "rolling_back", "preflight_running"]);
const TERMINAL_STATUSES = new Set([
  "completed", "failed", "rolled_back", "cancelled", "rejected",
  "completed_with_errors", "batch_aborted",
]);

const levelClass: Record<string, string> = {
  info: "text-slate-100",
  warning: "text-amber-400",
  error: "text-red-400",
};

export function LogViewer({ crId, crStatus }: Props) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [done, setDone] = useState(false);
  const [paused, setPaused] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const isActive = ACTIVE_STATUSES.has(crStatus);
  const isTerminal = TERMINAL_STATUSES.has(crStatus);

  // Metrics — poll every 2s while active, stop when terminal
  const { data: metrics } = useQuery({
    queryKey: ["cr-metrics", crId],
    queryFn: () => changeRequestsApi.getMetrics(crId),
    refetchInterval: isActive ? 2000 : isTerminal ? false : 5000,
  });

  // SSE connection
  useEffect(() => {
    if (!isActive && !isTerminal) return;

    const lastId = lines.length > 0 ? lines[lines.length - 1].id : undefined;
    const url = `/api/change-requests/${crId}/logs/stream${lastId ? `?since_id=${lastId}` : ""}`;
    const es = new EventSource(url);

    es.onmessage = (e) => {
      try {
        const line: LogLine = JSON.parse(e.data);
        setLines((prev) => [...prev, line]);
      } catch {
        // ignore malformed events
      }
    };

    es.addEventListener("done", () => {
      setDone(true);
      es.close();
    });

    es.onerror = () => {
      es.close();
    };

    return () => es.close();
    // Re-connect only when crStatus changes to active
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [crId, crStatus]);

  // Auto-scroll
  useEffect(() => {
    if (!paused && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [lines, paused]);

  if (lines.length === 0 && !isActive && !isTerminal) return null;

  const stepsTotal = metrics?.steps_total ?? 0;
  const stepsDone = (metrics?.steps_completed ?? 0) + (metrics?.steps_failed ?? 0);
  const progressPct = stepsTotal > 0 ? Math.round((stepsDone / stepsTotal) * 100) : 0;
  const elapsed = metrics?.elapsed_seconds;
  const remaining = metrics?.estimated_remaining_seconds;

  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-slate-100 bg-slate-50">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" style={{ display: isActive ? undefined : "none" }} />
          <h3 className="text-sm font-semibold text-slate-700">Execution Logs</h3>
          {done && <span className="text-xs text-slate-400 ml-1">(stream closed)</span>}
        </div>
        <button
          onClick={() => setPaused((p) => !p)}
          className="text-xs text-slate-500 hover:text-slate-700 px-2 py-1 rounded border border-slate-200 hover:border-slate-300"
        >
          {paused ? "Resume scroll" : "Pause scroll"}
        </button>
      </div>

      {/* Progress bar */}
      {stepsTotal > 0 && (
        <div className="px-5 py-3 border-b border-slate-100 bg-slate-50 space-y-1.5">
          <div className="flex justify-between text-xs text-slate-500">
            <span>{stepsDone} / {stepsTotal} steps</span>
            <span className="flex gap-3">
              {elapsed !== null && elapsed !== undefined && (
                <span>Elapsed: {Math.round(elapsed)}s</span>
              )}
              {remaining !== null && remaining !== undefined && (
                <span>~{Math.round(remaining)}s remaining</span>
              )}
            </span>
          </div>
          <div className="w-full bg-slate-200 rounded-full h-1.5">
            <div
              className="bg-brand-500 h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          {metrics?.current_step && (
            <p className="text-xs text-slate-400 font-mono truncate">
              Running: {metrics.current_step}
            </p>
          )}
        </div>
      )}

      {/* Log output */}
      <div className="bg-slate-950 p-4 max-h-80 overflow-y-auto font-mono text-xs leading-5">
        {lines.length === 0 ? (
          <span className="text-slate-500">Waiting for log output…</span>
        ) : (
          lines.map((line) => (
            <div key={line.id} className={levelClass[line.level] ?? "text-slate-100"}>
              <span className="text-slate-500 mr-2 select-none">
                {new Date(line.timestamp).toLocaleTimeString()}
              </span>
              {line.message}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Mount LogViewer in ChangeRequestDetail.tsx**

Open `frontend/src/pages/ChangeRequestDetail.tsx`.

First, add the import after the existing component imports (e.g., after the `AutoMigrationStepper` import):

```typescript
import { LogViewer } from "../components/LogViewer";
```

Then, find the "Execution Run" section (around line 625):

```tsx
        {cr.execution_runs && cr.execution_runs.length > 0 && (
          <Section title="Execution Run" icon={Play}>
```

After the closing `</Section>` tag for the Execution Run section (around line 651), insert:

```tsx
        <LogViewer crId={cr.id} crStatus={cr.status} />
```

- [ ] **Step 4: Restart the frontend container**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Wait ~15 seconds for Vite to rebuild.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/endpoints.ts frontend/src/components/LogViewer.tsx frontend/src/pages/ChangeRequestDetail.tsx
git commit -m "feat(obs): LogViewer component with SSE log tail and metrics progress bar"
```

---

## Task 6: Final integration commit

**Files:**
- No new files

- [ ] **Step 1: Run the full backend test suite**

```bash
cd f:/Nexplane/nexplane/backend && python -m pytest tests/test_cr_execution_logs.py tests/test_change_execution_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 2: Verify activities.py and router import cleanly**

```bash
cd f:/Nexplane/nexplane/backend && python -c "
import app.workflows.activities
import app.routers.change_requests
print('all imports OK')
"
```

Expected: `all imports OK`

- [ ] **Step 3: Final commit tying it all together**

```bash
git add -A
git commit -m "feat: streaming observability — SSE log tail, execution metrics, log viewer UI"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task covering it |
|---|---|
| `cr_execution_logs` table with all specified columns | Task 1 |
| Alembic migration `obs001` | Task 1 |
| `emit_log()` helper that never raises | Task 2 |
| Step start log | Task 3 |
| Step complete log with elapsed ms | Task 3 |
| Step error log with traceback | Task 3 |
| Rollback step logs | Task 3 |
| `GET /{cr_id}/logs/stream` SSE endpoint | Task 4 |
| SSE `done` event on terminal + 5s idle | Task 4 |
| `GET /{cr_id}/metrics` JSON endpoint | Task 4 |
| Metrics: steps_total, steps_completed, steps_failed, elapsed, estimated_remaining, current_step | Task 4 |
| LogViewer component | Task 5 |
| SSE connects when executing/rolling_back | Task 5 |
| Dark monospace log output with level colors | Task 5 |
| Progress bar using metrics endpoint | Task 5 |
| Auto-scroll with pause button | Task 5 |
| Closes SSE on terminal status | Task 5 (EventSource closed on `done` event) |
| Tests for log emission and metrics | Tasks 2, 4 |
| `sse-starlette` added to requirements | Task 1 |

**Placeholder scan:** No TBDs, no "implement later", no "similar to Task N" — all steps contain complete code.

**Type consistency:** `emit_log(change_request_id: str, step_name: str, level: str, message: str, metadata: dict | None)` defined in Task 2 and called identically in Task 3. `_compute_metrics(logs, total_steps, started_at)` defined and called in Task 4. `changeRequestsApi.getMetrics(crId)` added in Task 5 step 1 and called in LogViewer.tsx step 2 — consistent.
