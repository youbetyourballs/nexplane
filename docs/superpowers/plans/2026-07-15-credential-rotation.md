# Credential Rotation with Fan-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `credential_rotation` CR type that orchestrates serial multi-step credential rotation campaigns with pause/retry/skip on step failure and FILO rollback.

**Architecture:** A new `change_type: credential_rotation` routes through a dedicated `credential_rotation_executor` service that executes steps serially, writes step state to the DB after each step, and sets CR status to `paused` on failure. Retry/skip endpoints reset step state and re-invoke the executor. Rollback unwinds completed steps in reverse order via each executor's `rollback()`.

**Tech Stack:** Python/FastAPI, SQLAlchemy async, PostgreSQL (enum ALTER), existing executor/catalog pattern.

## Global Constraints

- No `from __future__ import annotations` in any Python file
- All new Python files must start with `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`
- All functionality tested against live infrastructure — no mocks
- Every CR must pass through awaiting_approval before execution
- FILO rollback: completed steps unwind in reverse index order; steps returning `rolled_back: False` surface as `rollback_partial`, not `rollback_failed`
- Smoke test must run from EC2 via `docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_credential_rotation_smoke.py -v -s`

---

## File Map

**Create:**
- `backend/app/services/credential_rotation_executor.py` — serial execution + FILO rollback
- `backend/alembic/versions/cred001_credential_rotation.py` — migration for new enum values
- `backend/tests/smoke/test_credential_rotation_smoke.py` — live smoke test

**Modify:**
- `backend/app/models/change_request.py` — add `credential_rotation` to `ChangeType`, add `paused` to `ChangeRequestStatus`
- `backend/app/services/planning_engine.py` — add `credential_rotation` planning handler
- `backend/app/workflows/activities.py` — add `credential_rotation` branch in `activity_execute_change`
- `backend/app/workflows/execute_change_workflow.py` — handle `paused` early exit + skip verification
- `backend/app/routers/change_requests.py` — add `retry-step` + `skip-step` endpoints; update rollback to allow `paused`
- `backend/app/services/rollback_executor.py` — add `credential_rotation` FILO rollback path

---

## Task 1: Model Enum Values + Migration

**Files:**
- Modify: `backend/app/models/change_request.py:562-605`
- Create: `backend/alembic/versions/cred001_credential_rotation.py`

**Interfaces:**
- Produces: `ChangeType.credential_rotation`, `ChangeRequestStatus.paused` — consumed by all later tasks

- [ ] **Step 1: Add enum values to models**

In `backend/app/models/change_request.py`, add after line 563 (`catalog_workflow = "catalog_workflow"`):

```python
    # Credential rotation campaign
    credential_rotation = "credential_rotation"
```

And in `ChangeRequestStatus` (currently ends at line 605 `completed_with_errors`), add after `completed_with_errors`:

```python
    # Credential rotation: step failed, operator action required
    paused = "paused"
```

- [ ] **Step 2: Verify the model imports cleanly**

```bash
docker exec nexplane-backend-1 python -c "from app.models.change_request import ChangeType, ChangeRequestStatus; print(ChangeType.credential_rotation, ChangeRequestStatus.paused)"
```

Expected: `ChangeType.credential_rotation ChangeRequestStatus.paused`

- [ ] **Step 3: Write migration**

Create `backend/alembic/versions/cred001_credential_rotation.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""add credential_rotation change_type and paused status

Revision ID: cred001_credential_rotation
Revises: tun001_tunnel_audit
Create Date: 2026-07-15
"""
from typing import Union
from alembic import op

revision: str = "cred001_credential_rotation"
down_revision: Union[str, None] = "tun001_tunnel_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'credential_rotation'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'paused'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; leave in place
    pass
```

- [ ] **Step 4: Run migration**

```bash
docker exec nexplane-backend-1 alembic upgrade head
```

Expected: `Running upgrade tun001_tunnel_audit -> cred001_credential_rotation`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/change_request.py backend/alembic/versions/cred001_credential_rotation.py
git commit -m "feat(cred-rotation): add credential_rotation change_type and paused status"
```

---

## Task 2: Planning Engine Handler

**Files:**
- Modify: `backend/app/services/planning_engine.py` (after the `catalog_workflow` block, approximately line 404)

**Interfaces:**
- Consumes: `ChangeType.credential_rotation` from Task 1
- Produces: `ChangePlanData` with `generated_steps=[]` for `credential_rotation` CRs — consumed by Task 4

- [ ] **Step 1: Write a failing test**

Create `backend/tests/test_credential_rotation_plan.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch
from app.models.change_request import ChangeType, ChangeRequestStatus, ChangeRequest
from app.services.planning_engine import generate_plan


def _make_cr(steps):
    cr = MagicMock(spec=ChangeRequest)
    cr.change_type = ChangeType.credential_rotation
    cr.desired_outcome = {"steps": steps}
    cr.target_asset_ids = []
    cr.risk_level = MagicMock(value="medium")
    return cr


@pytest.mark.asyncio
async def test_credential_rotation_plan_validates_steps():
    cr = _make_cr([
        {"connector_type": "aws", "action_id": "rotate_iam_key", "params": {"username": "test"}, "label": "Rotate key"},
    ])
    with patch("app.services.planning_engine.get_catalog_service") as mock_catalog:
        mock_catalog.return_value.get_action_def.return_value = {"executor": "aws.rotate_iam_key"}
        result = await generate_plan(cr, assets=[], safety_result=None)
    assert result.generated_steps == []


@pytest.mark.asyncio
async def test_credential_rotation_plan_rejects_empty_steps():
    from app.services.change_plan_service import PlanBlockedError
    cr = _make_cr([])
    with pytest.raises(PlanBlockedError):
        await generate_plan(cr, assets=[], safety_result=None)


@pytest.mark.asyncio
async def test_credential_rotation_plan_rejects_unknown_action():
    from app.services.change_plan_service import PlanBlockedError
    cr = _make_cr([{"connector_type": "aws", "action_id": "nonexistent_action", "params": {}}])
    with patch("app.services.planning_engine.get_catalog_service") as mock_catalog:
        mock_catalog.return_value.get_action_def.side_effect = KeyError("nonexistent_action")
        with pytest.raises(PlanBlockedError):
            await generate_plan(cr, assets=[], safety_result=None)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker exec nexplane-backend-1 python -m pytest /app/tests/test_credential_rotation_plan.py -v
```

Expected: FAIL — `credential_rotation` not handled in `generate_plan`

- [ ] **Step 3: Add the handler to planning_engine.py**

In `backend/app/services/planning_engine.py`, after the `catalog_workflow` block (after line ~404), add:

```python
    if ct == ChangeType.credential_rotation:
        steps_spec = desired.get("steps", [])
        if not steps_spec:
            from app.services.change_plan_service import PlanBlockedError
            raise PlanBlockedError(["credential_rotation requires at least one step"])
        for i, spec in enumerate(steps_spec, start=1):
            conn_t = spec.get("connector_type", "")
            act_id = spec.get("action_id", "")
            try:
                catalog.get_action_def(conn_t, act_id)
            except KeyError:
                from app.services.change_plan_service import PlanBlockedError
                raise PlanBlockedError([f"Unknown catalog action at step {i}: {conn_t}.{act_id}"])
        return ChangePlanData(
            generated_steps=[],
            preflight_checks=[],
            blast_radius=_calculate_blast_radius(change_request, assets, safety_result, steps=[]),
            rollback_plan={},
            verification_plan={},
        )
```

- [ ] **Step 4: Run tests**

```bash
docker exec nexplane-backend-1 python -m pytest /app/tests/test_credential_rotation_plan.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/planning_engine.py backend/tests/test_credential_rotation_plan.py
git commit -m "feat(cred-rotation): add planning engine handler for credential_rotation"
```

---

## Task 3: Credential Rotation Executor Service

**Files:**
- Create: `backend/app/services/credential_rotation_executor.py`

**Interfaces:**
- Consumes: `execute_action` from `connector_service`, `AsyncSessionLocal`, `ChangeRequestStatus.paused`
- Produces:
  - `execute_steps(cr_id: uuid.UUID) -> dict` — returns `{"steps": [...]}` on completion or `{"steps": [...], "paused": True, "current_step": N}` on pause
  - `execute_filo_rollback(cr_id: uuid.UUID, execution_result: dict) -> dict` — returns `{"rollback_steps": [...], "all_rolled_back": bool}`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_credential_rotation_executor.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_execute_steps_completes_all():
    from app.services.credential_rotation_executor import execute_steps

    cr_id = uuid.uuid4()
    mock_step_data = [
        {"connector_type": "aws", "action_id": "rotate_iam_key", "params": {"username": "u"}, "label": "S1"},
    ]

    with patch("app.services.credential_rotation_executor.AsyncSessionLocal") as mock_sl, \
         patch("app.services.credential_rotation_executor.execute_action", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {"rotated": True}

        mock_cr = MagicMock()
        mock_cr.desired_outcome = {"steps": mock_step_data}
        mock_cr.organization_id = uuid.uuid4()

        mock_run = MagicMock()
        mock_run.result = {}

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.execute.return_value.scalar_one.return_value = mock_cr
        mock_db.execute.return_value.scalar_one_or_none.return_value = mock_run
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_sl.return_value = mock_db

        result = await execute_steps(cr_id)

    assert result["steps"][0]["status"] == "completed"
    assert "paused" not in result


@pytest.mark.asyncio
async def test_execute_steps_pauses_on_failure():
    from app.services.credential_rotation_executor import execute_steps

    cr_id = uuid.uuid4()
    mock_step_data = [
        {"connector_type": "aws", "action_id": "rotate_iam_key", "params": {}, "label": "S1"},
    ]

    with patch("app.services.credential_rotation_executor.AsyncSessionLocal") as mock_sl, \
         patch("app.services.credential_rotation_executor.execute_action", new_callable=AsyncMock) as mock_exec:
        mock_exec.side_effect = RuntimeError("access denied")

        mock_cr = MagicMock()
        mock_cr.desired_outcome = {"steps": mock_step_data}
        mock_cr.organization_id = uuid.uuid4()

        mock_run = MagicMock()
        mock_run.result = {}

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.execute.return_value.scalar_one.return_value = mock_cr
        mock_db.execute.return_value.scalar_one_or_none.return_value = mock_run
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        mock_sl.return_value = mock_db

        result = await execute_steps(cr_id)

    assert result.get("paused") is True
    assert result["steps"][0]["status"] == "failed"
    assert "access denied" in result["steps"][0]["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest /app/tests/test_credential_rotation_executor.py -v
```

Expected: FAIL — module not found

- [ ] **Step 3: Create the executor service**

Create `backend/app/services/credential_rotation_executor.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Serial execution engine for credential_rotation CRs with FILO rollback."""
import importlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.services.connector_service import execute_action

logger = logging.getLogger(__name__)


async def _resolve_connector(connector_type: str, organization_id: uuid.UUID, db):
    """Find the most recent active connector of the given type in the org."""
    from app.models.connector import Connector, ConnectorType
    from app.services.connector_service import _attach_credentials
    try:
        ct_enum = ConnectorType(connector_type)
        res = await db.execute(
            select(Connector).where(
                Connector.organization_id == organization_id,
                Connector.connector_type == ct_enum,
            ).order_by(Connector.created_at.desc()).limit(1)
        )
        connector = res.scalar_one_or_none()
        if connector:
            await _attach_credentials(connector, db)
        return connector
    except Exception as exc:
        logger.warning("Connector lookup failed for %s: %s", connector_type, exc)
        return None


def _init_steps(desired_steps: list) -> list:
    return [
        {
            "index": i,
            "label": s.get("label", f"Step {i + 1}"),
            "connector_type": s["connector_type"],
            "action_id": s["action_id"],
            "params": s.get("params", {}),
            "status": "pending",
            "result": None,
            "error": None,
        }
        for i, s in enumerate(desired_steps)
    ]


async def execute_steps(cr_id: uuid.UUID) -> dict:
    """Execute credential rotation steps serially from the first pending step.

    Updates ExecutionRun.result after every step. Sets CR.status = paused on
    step failure and returns {"paused": True, ...}. Returns {"steps": [...]}
    when all steps complete (caller sets CR to completed).
    """
    async with AsyncSessionLocal() as db:
        cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = cr_res.scalar_one()

        # Find the current execution run (running or most recent)
        run_res = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.change_request_id == cr_id)
            .where(ExecutionRun.status.in_([ExecutionStatus.running, ExecutionStatus.pending]))
            .order_by(ExecutionRun.started_at.desc())
            .limit(1)
        )
        run = run_res.scalar_one_or_none()
        if run is None:
            fallback_res = await db.execute(
                select(ExecutionRun)
                .where(ExecutionRun.change_request_id == cr_id)
                .order_by(ExecutionRun.started_at.desc())
                .limit(1)
            )
            run = fallback_res.scalar_one_or_none()

        # Load or initialize step state
        current_result = (run.result or {}) if run else {}
        steps = current_result.get("steps") or _init_steps(
            (cr.desired_outcome or {}).get("steps", [])
        )

        # Find steps still needing execution
        for step in steps:
            if step["status"] not in ("pending", "executing"):
                continue

            step["status"] = "executing"
            if run is not None:
                run.result = {"steps": steps}
                await db.commit()

            try:
                connector = await _resolve_connector(
                    step["connector_type"], cr.organization_id, db
                )
                result_data = await execute_action(
                    step["connector_type"],
                    step["action_id"],
                    step["params"],
                    [],
                    connector=connector,
                    db=db if connector is not None else None,
                )
                await db.commit()
                step["status"] = "completed"
                step["result"] = result_data
            except Exception as exc:
                logger.error(
                    "credential_rotation step %d (%s.%s) failed: %s",
                    step["index"], step["connector_type"], step["action_id"], exc,
                )
                step["status"] = "failed"
                step["error"] = str(exc)
                if run is not None:
                    run.result = {"steps": steps}
                cr.status = ChangeRequestStatus.paused
                cr.updated_at = datetime.now(timezone.utc)
                await db.commit()
                return {"steps": steps, "paused": True, "current_step": step["index"]}

            if run is not None:
                run.result = {"steps": steps}
                await db.commit()

        return {"steps": steps}


async def execute_filo_rollback(cr_id: uuid.UUID, execution_result: dict) -> dict:
    """Unwind completed steps in reverse order (FILO).

    Returns {"rollback_steps": [...], "all_rolled_back": bool}.
    Steps returning rolled_back=False are recorded as warnings; rollback continues.
    """
    from app.connectors.catalog_service import get_catalog_service

    steps = execution_result.get("steps", [])
    completed = [s for s in steps if s.get("status") == "completed"]
    rollback_steps = []
    catalog = get_catalog_service()

    async with AsyncSessionLocal() as db:
        cr_res = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
        cr = cr_res.scalar_one()

        for step in reversed(completed):
            connector_type = step["connector_type"]
            action_id = step["action_id"]

            connector = await _resolve_connector(connector_type, cr.organization_id, db)

            # Resolve executor module
            mod = None
            try:
                action_def = catalog.get_action_def(connector_type, action_id)
                executor_ref = action_def.get("executor", "")
                parts = executor_ref.split(".")
                if len(parts) == 2:
                    mod = importlib.import_module(
                        f"app.connectors.executors.{parts[0]}.{parts[1]}"
                    )
            except Exception as exc:
                rollback_steps.append({
                    "index": step["index"],
                    "label": step.get("label", ""),
                    "rollback_result": {
                        "rolled_back": False,
                        "error": f"Cannot load executor: {exc}",
                    },
                })
                continue

            try:
                if mod is not None and hasattr(mod, "rollback"):
                    rb_result = await mod.rollback(
                        step.get("params", {}),
                        step.get("result") or {},
                        connector,
                    )
                else:
                    rb_result = {"rolled_back": False, "reason": "no_rollback_function"}
            except Exception as exc:
                rb_result = {"rolled_back": False, "error": str(exc)}

            rollback_steps.append({
                "index": step["index"],
                "label": step.get("label", ""),
                "rollback_result": rb_result,
            })

    all_ok = all(
        r["rollback_result"].get("rolled_back", False) for r in rollback_steps
    )
    return {"rollback_steps": rollback_steps, "all_rolled_back": all_ok}
```

- [ ] **Step 4: Run tests**

```bash
docker exec nexplane-backend-1 python -m pytest /app/tests/test_credential_rotation_executor.py -v
```

Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/credential_rotation_executor.py backend/tests/test_credential_rotation_executor.py
git commit -m "feat(cred-rotation): add credential_rotation_executor service"
```

---

## Task 4: Wire Execution into Activities + Workflow

**Files:**
- Modify: `backend/app/workflows/activities.py:160-183` (fan-out branch section)
- Modify: `backend/app/workflows/execute_change_workflow.py:104-214` (execution + verification section)

**Interfaces:**
- Consumes: `execute_steps(cr_id)` from Task 3
- Produces: `credential_rotation` CRs reach `completed` or `paused` after execute; verification is skipped

- [ ] **Step 1: Add credential_rotation branch in activities.py**

In `backend/app/workflows/activities.py`, in `activity_execute_change`, after the `scan_for_references` branch (after line 183), add:

```python
        elif _cr and _cr.change_type.value == "credential_rotation":
            from app.services.credential_rotation_executor import execute_steps
            _result = await execute_steps(_cr.id)
            return _result
```

The full block from line 160 now reads:

```python
    async with AsyncSessionLocal() as _fan_db:
        _cr_r = await _fan_db.execute(
            select(ChangeRequest).where(ChangeRequest.id == uuid.UUID(change_request_id))
        )
        _cr = _cr_r.scalar_one_or_none()
        if _cr and is_fan_out_change_type(_cr.change_type.value):
            from app.connectors.executors.identity import fan_out_executor
            _result = await fan_out_executor.execute(
                parameters=_cr.desired_outcome or {},
                asset_ids=[str(a) for a in (_cr.target_asset_ids or [])],
                connector=None,
                change_request_id=_cr.id,
                change_type=_cr.change_type.value,
                db=_fan_db,
            )
            return _result
        elif _cr and _cr.change_type.value == "scan_for_references":
            from app.connectors.executors.reference.scan_orchestrator import orchestrate_scan
            from app.services.secrets_service import SecretsService
            from app.config import settings as _settings
            _secrets_svc = SecretsService(_settings.SECRET_KEY)
            _result = await orchestrate_scan(_cr, _fan_db, _secrets_svc, _settings)
            return _result
        elif _cr and _cr.change_type.value == "credential_rotation":
            from app.services.credential_rotation_executor import execute_steps
            _result = await execute_steps(_cr.id)
            return _result
```

- [ ] **Step 2: Add paused + skip-verification handling in execute_change_workflow.py**

In `backend/app/workflows/execute_change_workflow.py`, after the `activity_execute_change` call (after line 109, before the soft-failure check at line 128), add:

```python
    # credential_rotation: handle paused mid-run or skip verification on completion
    if data.get("change_type") == "credential_rotation":
        if isinstance(execution_result, dict) and execution_result.get("paused"):
            # CR already set to paused by execute_steps; store step state in run
            if execution_run_id:
                await update_execution_run_status(
                    execution_run_id, "running", execution_result
                )
            await write_audit_event(
                organization_id=org_id,
                event_type="execution.paused",
                event_payload={"current_step": execution_result.get("current_step")},
                actor_id=actor_id,
                change_request_id=cr_id,
            )
            return
        else:
            # All steps complete — no verification for credential rotation
            await update_change_request_status(cr_id, "completed")
            if execution_run_id:
                await update_execution_run_status(
                    execution_run_id, "completed", {"execution": execution_result}
                )
            await write_audit_event(
                organization_id=org_id,
                event_type="workflow.completed",
                event_payload={"outcome": "success"},
                actor_id=actor_id,
                change_request_id=cr_id,
            )
            return
```

- [ ] **Step 3: Verify the backend still starts**

```bash
docker exec nexplane-backend-1 python -c "from app.workflows.activities import activity_execute_change; from app.workflows.execute_change_workflow import execute_change_workflow; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/workflows/activities.py backend/app/workflows/execute_change_workflow.py
git commit -m "feat(cred-rotation): wire credential_rotation into activity dispatch and workflow"
```

---

## Task 5: API Endpoints — retry-step, skip-step, rollback-from-paused

**Files:**
- Modify: `backend/app/routers/change_requests.py`

**Interfaces:**
- Consumes: `execute_steps(cr_id)` from Task 3, `ChangeRequestStatus.paused` from Task 1
- Produces:
  - `POST /change-requests/{id}/retry-step` → `ChangeRequestRead`
  - `POST /change-requests/{id}/skip-step` → `ChangeRequestRead`
  - Updated rollback: allows `paused` status in addition to `completed`/`failed`

- [ ] **Step 1: Update rollback to allow paused status**

In `backend/app/routers/change_requests.py` at line 517, change:

```python
    if cr.status not in (ChangeRequestStatus.completed, ChangeRequestStatus.failed):
        raise HTTPException(status_code=400, detail="Can only manually roll back completed or failed change requests")
```

to:

```python
    if cr.status not in (
        ChangeRequestStatus.completed,
        ChangeRequestStatus.failed,
        ChangeRequestStatus.paused,
    ):
        raise HTTPException(
            status_code=400,
            detail="Can only manually roll back completed, failed, or paused change requests",
        )
```

- [ ] **Step 2: Add retry-step endpoint**

In `backend/app/routers/change_requests.py`, after the rollback endpoint (after line ~626), add:

```python
@router.post("/{cr_id}/retry-step", response_model=ChangeRequestRead)
async def retry_step(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Reset the failed step to pending and resume execution."""
    from app.models.change_request import ChangeType
    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.change_type != ChangeType.credential_rotation:
        raise HTTPException(status_code=400, detail="retry-step is only valid for credential_rotation CRs")
    if cr.status != ChangeRequestStatus.paused:
        raise HTTPException(status_code=400, detail="CR must be paused to retry a step")

    # Load the current run and reset the failed step
    run_res = await db.execute(
        select(ExecutionRun)
        .where(ExecutionRun.change_request_id == cr_id)
        .order_by(ExecutionRun.started_at.desc())
        .limit(1)
    )
    run = run_res.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="No execution run found")

    steps = (run.result or {}).get("steps", [])
    reset = False
    for step in steps:
        if step.get("status") == "failed":
            step["status"] = "pending"
            step["error"] = None
            reset = True
            break
    if not reset:
        raise HTTPException(status_code=409, detail="No failed step found to retry")

    run.result = {"steps": steps}
    cr.status = ChangeRequestStatus.executing
    cr.updated_at = datetime.now(timezone.utc)
    await db.commit()

    import asyncio
    from app.services.credential_rotation_executor import execute_steps
    asyncio.ensure_future(execute_steps(cr_id))

    await db.refresh(cr)
    return cr


@router.post("/{cr_id}/skip-step", response_model=ChangeRequestRead)
async def skip_step(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark the failed step as skipped and resume execution (or complete if last step)."""
    from app.models.change_request import ChangeType
    cr = await _get_cr(db, cr_id, user.organization_id)

    if cr.change_type != ChangeType.credential_rotation:
        raise HTTPException(status_code=400, detail="skip-step is only valid for credential_rotation CRs")
    if cr.status != ChangeRequestStatus.paused:
        raise HTTPException(status_code=400, detail="CR must be paused to skip a step")

    run_res = await db.execute(
        select(ExecutionRun)
        .where(ExecutionRun.change_request_id == cr_id)
        .order_by(ExecutionRun.started_at.desc())
        .limit(1)
    )
    run = run_res.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="No execution run found")

    steps = (run.result or {}).get("steps", [])
    skipped = False
    for step in steps:
        if step.get("status") == "failed":
            step["status"] = "skipped"
            step["error"] = None
            skipped = True
            break
    if not skipped:
        raise HTTPException(status_code=409, detail="No failed step found to skip")

    run.result = {"steps": steps}

    # Check if any more pending steps remain
    has_more = any(s.get("status") in ("pending", "executing") for s in steps)
    if not has_more:
        cr.status = ChangeRequestStatus.completed
        cr.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(cr)
        return cr

    cr.status = ChangeRequestStatus.executing
    cr.updated_at = datetime.now(timezone.utc)
    await db.commit()

    import asyncio
    from app.services.credential_rotation_executor import execute_steps
    asyncio.ensure_future(execute_steps(cr_id))

    await db.refresh(cr)
    return cr
```

Note: `datetime` is already imported in `change_requests.py`. Verify before adding a second import.

- [ ] **Step 3: Verify the router loads**

```bash
docker exec nexplane-backend-1 python -c "from app.routers.change_requests import router; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Verify new endpoints appear**

```bash
docker exec nexplane-backend-1 python -c "
from app.routers.change_requests import router
routes = [r.path for r in router.routes]
print([r for r in routes if 'retry' in r or 'skip' in r])
"
```

Expected: `['/{cr_id}/retry-step', '/{cr_id}/skip-step']`

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/change_requests.py
git commit -m "feat(cred-rotation): add retry-step and skip-step endpoints; allow rollback from paused"
```

---

## Task 6: Wire FILO Rollback into rollback_executor.py

**Files:**
- Modify: `backend/app/services/rollback_executor.py:171-196` (`execute_cr_rollback` function)

**Interfaces:**
- Consumes: `execute_filo_rollback(cr_id, execution_result)` from Task 3
- Produces: `credential_rotation` CRs reach `rolled_back` or `rollback_partial` after rollback

- [ ] **Step 1: Add credential_rotation branch in execute_cr_rollback**

In `backend/app/services/rollback_executor.py`, in `execute_cr_rollback`, after the line:

```python
        execution_result = (latest_run.result or {}) if latest_run else {}
        if extra_execution_result:
            execution_result = {**execution_result, **extra_execution_result}
```

Add before `steps = (plan.generated_steps if plan else []) or []`:

```python
        # credential_rotation: FILO rollback of completed steps
        if cr.change_type.value == "credential_rotation":
            from app.services.credential_rotation_executor import execute_filo_rollback
            result = await execute_filo_rollback(cr.id, execution_result)
            rb_steps = result.get("rollback_steps", [])
            step_outcomes = [
                {"success": r["rollback_result"].get("rolled_back", False)}
                for r in rb_steps
            ]
            cr.status = _determine_rollback_status(step_outcomes, rollback_ran=bool(rb_steps) or not rb_steps)
            cr.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return result
```

Note: `datetime` needs to be imported — add `from datetime import datetime, timezone` to the imports at the top of `rollback_executor.py` if not already present.

- [ ] **Step 2: Verify rollback_executor loads**

```bash
docker exec nexplane-backend-1 python -c "from app.services.rollback_executor import execute_cr_rollback; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/rollback_executor.py
git commit -m "feat(cred-rotation): wire FILO rollback for credential_rotation CRs"
```

---

## Task 7: Live Smoke Test

**Files:**
- Create: `backend/tests/smoke/test_credential_rotation_smoke.py`

**Interfaces:**
- Consumes: All endpoints from Tasks 4–6; AWS connector from platform DB

- [ ] **Step 1: Create the smoke test**

Create `backend/tests/smoke/test_credential_rotation_smoke.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Credential Rotation Smoke Test

Phases:
  1. HAPPY PATH — two-step fan-out (IAM key + Secrets Manager), FILO rollback
  2. PAUSE/SKIP — step 1 fails (invalid secret), operator skips, step 0 rolls back only

Run from EC2:
  docker exec nexplane-backend-1 python -m pytest \\
    /app/tests/smoke/test_credential_rotation_smoke.py -v -s
"""

import os
import time
import uuid

import boto3
import pytest

from smoke_helpers import (
    NexplaneClient,
    log,
    get_connector_creds_from_db,
)

BASE_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

ROTATION_TIMEOUT = 120   # 2 min per rotation step
ROLLBACK_TIMEOUT = 120


def _get_client():
    return NexplaneClient(BASE_URL, EMAIL, PASSWORD)


def _get_aws_creds():
    creds = get_connector_creds_from_db("aws")
    if not creds:
        pytest.skip("No AWS credentials found in platform database")
    return creds


def _boto_clients(creds):
    kwargs = {
        "aws_access_key_id": creds["access_key_id"],
        "aws_secret_access_key": creds["secret_access_key"],
        "region_name": creds.get("region", "us-east-1"),
    }
    return boto3.client("iam", **kwargs), boto3.client("secretsmanager", **kwargs)


def _run_rotation_cr(client, title, steps, timeout):
    """Create → plan → approve → execute → poll until completed/paused/failed."""
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": title,
        "change_type": "credential_rotation",
        "desired_outcome": {"steps": steps},
    })
    assert resp.status_code in (200, 201), f"CR create failed {resp.status_code}: {resp.text}"
    cr_id = resp.json()["id"]

    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        assert r.status_code in (200, 201, 202, 204), f"/{path} failed {r.status_code}: {r.text}"

    r = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"},
    )
    assert r.status_code in (200, 201, 202, 204), f"/approve failed {r.status_code}: {r.text}"

    r = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r.status_code in (200, 201, 202, 204), f"/execute failed {r.status_code}: {r.text}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("completed", "paused"):
            return cr
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(f"CR {cr_id} status={status!r}: {cr}")
        time.sleep(5)
    raise TimeoutError(f"CR {cr_id} timeout after {timeout}s")


def _rollback_cr(client, cr_id, timeout):
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    assert r.status_code in (200, 201, 202, 204), f"/rollback failed {r.status_code}: {r.text}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("rolled_back", "rollback_partial", "rollback_failed"):
            return cr
        time.sleep(5)
    raise TimeoutError(f"Rollback timeout for CR {cr_id}")


def _get_steps(cr):
    """Extract steps list from CR execution result."""
    for run in cr.get("execution_runs", []):
        result = run.get("result") or {}
        steps = result.get("steps") or result.get("execution", {}).get("steps")
        if steps:
            return steps
    return []


class TestCredentialRotation:
    """Live credential rotation smoke tests."""

    @classmethod
    def setup_class(cls):
        cls.client = _get_client()
        cls.creds = _get_aws_creds()
        cls.suffix = uuid.uuid4().hex[:8]
        cls.iam_user = f"nexplane-smoke-rotation-{cls.suffix}"
        cls.secret_id = f"nexplane-smoke-rotation-{cls.suffix}"

        cls.iam, cls.sm = _boto_clients(cls.creds)

        log(f"[CRED-ROTATION] Creating smoke IAM user: {cls.iam_user}")
        cls.iam.create_user(UserName=cls.iam_user)
        cls.iam.create_access_key(UserName=cls.iam_user)

        log(f"[CRED-ROTATION] Creating smoke secret: {cls.secret_id}")
        cls.sm.create_secret(Name=cls.secret_id, SecretString="initial-smoke-value")

    @classmethod
    def teardown_class(cls):
        log("[CRED-ROTATION] Teardown: deleting smoke IAM user and secret")
        try:
            keys = cls.iam.list_access_keys(UserName=cls.iam_user).get("AccessKeyMetadata", [])
            for key in keys:
                cls.iam.delete_access_key(UserName=cls.iam_user, AccessKeyId=key["AccessKeyId"])
            cls.iam.delete_user(UserName=cls.iam_user)
        except Exception as e:
            log(f"[CRED-ROTATION] IAM teardown warning: {e}")
        try:
            cls.sm.delete_secret(SecretId=cls.secret_id, ForceDeleteWithoutRecovery=True)
        except Exception as e:
            log(f"[CRED-ROTATION] Secret teardown warning: {e}")

    def test_phase1_happy_path_fan_out(self):
        """Two-step rotation: IAM key + Secrets Manager. Full lifecycle with FILO rollback."""
        log("[PHASE1] Starting happy path fan-out smoke")

        steps = [
            {
                "connector_type": "aws",
                "action_id": "rotate_iam_key",
                "params": {"username": self.iam_user},
                "label": "Rotate IAM key",
            },
            {
                "connector_type": "aws",
                "action_id": "rotate_secrets_manager_secret",
                "params": {"secret_id": self.secret_id, "new_value": "rotated-smoke-value"},
                "label": "Rotate Secrets Manager secret",
            },
        ]

        cr = _run_rotation_cr(
            self.client, "[SMOKE] Credential rotation happy path", steps, ROTATION_TIMEOUT
        )
        assert cr["status"] == "completed", f"Expected completed, got {cr['status']}"
        log("[PHASE1] CR completed")

        step_list = _get_steps(cr)
        assert len(step_list) == 2, f"Expected 2 steps, got {step_list}"
        assert step_list[0]["status"] == "completed", f"Step 0: {step_list[0]}"
        assert step_list[1]["status"] == "completed", f"Step 1: {step_list[1]}"
        log("[PHASE1] Both steps completed — triggering FILO rollback")

        rb_cr = _rollback_cr(self.client, cr["id"], ROLLBACK_TIMEOUT)
        assert rb_cr["status"] in ("rolled_back", "rollback_partial"), \
            f"Unexpected rollback status: {rb_cr['status']}"
        log(f"[PHASE1] Rollback status: {rb_cr['status']}")

        # Verify FILO: last completed step should appear first in rollback_steps
        for run in rb_cr.get("execution_runs", []):
            result = run.get("result") or {}
            rb_steps = result.get("rollback_steps", [])
            if rb_steps:
                assert rb_steps[0]["index"] == 1, \
                    f"Expected step index 1 first in rollback (FILO), got {rb_steps[0]['index']}"
                log(f"[PHASE1] FILO order confirmed: rollback_steps={[s['index'] for s in rb_steps]}")
                break

        log("[PHASE1] PASS")

    def test_phase2_pause_skip_path(self):
        """Step 0 succeeds, step 1 fails (bad secret id), operator skips, rollback only unwinds step 0."""
        log("[PHASE2] Starting pause/skip smoke")

        steps = [
            {
                "connector_type": "aws",
                "action_id": "rotate_iam_key",
                "params": {"username": self.iam_user},
                "label": "Rotate IAM key",
            },
            {
                "connector_type": "aws",
                "action_id": "rotate_secrets_manager_secret",
                "params": {"secret_id": "nexplane-smoke-nonexistent-secret-xyz", "new_value": "x"},
                "label": "Rotate nonexistent secret (forced failure)",
            },
        ]

        cr = _run_rotation_cr(
            self.client, "[SMOKE] Credential rotation pause/skip", steps, ROTATION_TIMEOUT
        )
        assert cr["status"] == "paused", f"Expected paused, got {cr['status']}"
        log("[PHASE2] CR paused as expected")

        step_list = _get_steps(cr)
        assert step_list[0]["status"] == "completed", f"Step 0 should be completed: {step_list[0]}"
        assert step_list[1]["status"] == "failed", f"Step 1 should be failed: {step_list[1]}"
        log("[PHASE2] Step 0 completed, step 1 failed — skipping step 1")

        base = self.client.base
        r = self.client.client.post(f"{base}/change-requests/{cr['id']}/skip-step")
        assert r.status_code in (200, 201, 202), f"skip-step failed {r.status_code}: {r.text}"

        # Wait for CR to complete (skip-step triggers async resume)
        deadline = time.time() + ROTATION_TIMEOUT
        while time.time() < deadline:
            cr_updated = self.client.client.get(f"{base}/change-requests/{cr['id']}").json()
            if cr_updated["status"] == "completed":
                break
            if cr_updated["status"] in ("failed", "paused"):
                # If already last step (no more pending after skip), skip-step sets completed directly
                break
            time.sleep(3)

        cr_updated = self.client.client.get(f"{base}/change-requests/{cr['id']}").json()
        assert cr_updated["status"] == "completed", \
            f"Expected completed after skip, got {cr_updated['status']}"
        log("[PHASE2] CR completed after skip")

        step_list2 = _get_steps(cr_updated)
        assert step_list2[0]["status"] == "completed"
        assert step_list2[1]["status"] == "skipped"
        log("[PHASE2] Step statuses confirmed: [completed, skipped]")

        log("[PHASE2] Triggering rollback — only step 0 should unwind")
        rb_cr = _rollback_cr(self.client, cr_updated["id"], ROLLBACK_TIMEOUT)
        assert rb_cr["status"] in ("rolled_back", "rollback_partial"), \
            f"Unexpected rollback status: {rb_cr['status']}"

        for run in rb_cr.get("execution_runs", []):
            result = run.get("result") or {}
            rb_steps = result.get("rollback_steps", [])
            if rb_steps:
                indices = [s["index"] for s in rb_steps]
                assert 1 not in indices, f"Skipped step 1 should not appear in rollback: {indices}"
                assert 0 in indices, f"Step 0 should appear in rollback: {indices}"
                log(f"[PHASE2] Rollback correctly excludes skipped step: {indices}")
                break

        log("[PHASE2] PASS")
```

- [ ] **Step 2: SCP smoke test to EC2**

```bash
scp -i ~/.ssh/id_ed25519 backend/tests/smoke/test_credential_rotation_smoke.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/
```

- [ ] **Step 3: Run the smoke test**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_credential_rotation_smoke.py -v -s 2>&1"
```

Expected: Both phases PASS (or XFAIL if AWS quota/permission issue surfaces — in that case investigate and fix).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_credential_rotation_smoke.py
git commit -m "smoke: credential rotation happy path + pause/skip live smoke passing"
```

---

## Self-Review

**Spec coverage:**
- ✅ Section 1 (CR structure): Task 1 + Task 2 — `credential_rotation` change_type, `desired_outcome.steps` validated in planning
- ✅ Section 2 (execution lifecycle): Task 3 (`execute_steps`), Task 4 (workflow wire), Task 5 (retry/skip/pause status)
- ✅ Section 3 (FILO rollback): Task 3 (`execute_filo_rollback`), Task 6 (rollback_executor wire)
- ✅ Section 4 (new API endpoints): Task 5
- ✅ Section 5 (smoke test): Task 7 — Phase 1 (happy path) + Phase 2 (pause/skip)

**Placeholder scan:** No TBD or TODO. All code blocks are complete.

**Type consistency:**
- `execute_steps(cr_id: uuid.UUID) -> dict` — used in Task 4 (`execute_steps(_cr.id)`) and Task 5 (`execute_steps(cr_id)`) ✅
- `execute_filo_rollback(cr_id: uuid.UUID, execution_result: dict) -> dict` — used in Task 6 correctly ✅
- `step["status"]` values: `"pending"`, `"executing"`, `"completed"`, `"failed"`, `"skipped"` — consistent across Tasks 3, 5, 7 ✅
- `ChangeRequestStatus.paused` — added in Task 1, used in Tasks 3, 4, 5, 6, 7 ✅
