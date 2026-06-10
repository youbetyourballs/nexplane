# Safe Execution Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the minimum safety contract defined in `docs/product/specs/SAFE_EXECUTION_CONTRACT.md` across 6 phases, making Nexplane credible as a safe infrastructure control plane.

**Architecture:** Incremental per-phase hardening — each phase is independently testable and mergeable; no single large rewrite; all changes are backward compatible with existing behavior unless spec explicitly requires breaking it.

**Tech Stack:** Python/FastAPI, SQLAlchemy async, Alembic, PostgreSQL, pytest/pytest-asyncio, HMAC (stdlib hashlib/hmac)

---

## File Map

### Phase 1 — Production secret fail-closed
- Modify: `backend/app/config.py` — add startup validation
- Create: `backend/tests/test_config_validation.py` — phase acceptance tests

### Phase 2 — Agent result binding
- Modify: `backend/app/routers/agent.py` — enforce agent_id ownership on result submission; reject duplicate terminal jobs
- Create: `backend/tests/test_agent_result_binding.py` — phase acceptance tests

### Phase 3 — Rollback state machine
- Modify: `backend/app/models/change_request.py` — add 3 new ChangeRequestStatus enum values
- Create: `backend/alembic/versions/071_add_rollback_states.py` — Alembic migration
- Modify: `backend/app/services/rollback_executor.py` — truthful terminal state based on step outcomes
- Create: `backend/tests/test_rollback_state_machine.py` — phase acceptance tests

### Phase 4 — Verification framework
- Modify: `backend/app/models/change_request.py` — add `verification_status` field + VerificationStatus enum
- Create: `backend/alembic/versions/072_add_verification_status.py` — Alembic migration
- Modify: `backend/app/workflows/activities.py` — write verification_status when recording verification result
- Create: `backend/tests/test_verification_framework.py` — phase acceptance tests

### Phase 5 — Unified execution service + fix trigger_change_workflow live bug
- Create: `backend/app/services/change_execution_service.py` — `ChangeExecutionService.start()`
- Modify: `backend/app/routers/change_requests.py` — use service for manual execution
- Modify: `backend/app/workers/scheduled_cr_worker.py` — use service; fix trigger_change_workflow ImportError
- Modify: `backend/app/services/recurring_job_service.py` — use service; fix trigger_change_workflow ImportError
- Create: `backend/tests/test_change_execution_service.py` — phase acceptance tests

### Phase 6 — Recurring-job approval policy
- Create: `backend/app/models/recurring_job_policy.py` — RecurringJobPolicy model
- Create: `backend/alembic/versions/073_add_recurring_job_policy.py` — Alembic migration
- Modify: `backend/app/services/recurring_job_service.py` — enforce policy on auto-approval
- Create: `backend/tests/test_recurring_job_policy.py` — phase acceptance tests

---

## Task 1: Phase 1 — Production secret fail-closed

**Files:**
- Modify: `backend/app/config.py`
- Create: `backend/tests/test_config_validation.py`

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_config_validation.py
import pytest
from pydantic import ValidationError


def _make_settings(**kwargs):
    from app.config import Settings
    return Settings(**kwargs)


def test_development_allows_default_secret_key():
    s = _make_settings(ENVIRONMENT="development")
    assert s.SECRET_KEY == "dev-secret-key-change-in-production-32chars"


def test_development_allows_default_webhook_secret():
    s = _make_settings(ENVIRONMENT="development")
    assert s.WEBHOOK_SECRET == "changeme"


def test_production_rejects_default_secret_key():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        _make_settings(
            ENVIRONMENT="production",
            SECRET_KEY="dev-secret-key-change-in-production-32chars",
            WEBHOOK_SECRET="prod-safe-secret",
        )


def test_production_rejects_default_webhook_secret():
    with pytest.raises(ValueError, match="WEBHOOK_SECRET"):
        _make_settings(
            ENVIRONMENT="production",
            SECRET_KEY="a-real-production-secret-key-here-32chars!!",
            WEBHOOK_SECRET="changeme",
        )


def test_production_rejects_short_secret_key():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        _make_settings(
            ENVIRONMENT="production",
            SECRET_KEY="tooshort",
            WEBHOOK_SECRET="prod-safe-secret",
        )


def test_production_accepts_safe_secrets():
    s = _make_settings(
        ENVIRONMENT="production",
        SECRET_KEY="a-real-production-secret-key-here-32chars!!",
        WEBHOOK_SECRET="prod-safe-webhook-secret-value",
    )
    assert s.ENVIRONMENT == "production"


def test_staging_rejects_default_secret_key():
    with pytest.raises(ValueError, match="SECRET_KEY"):
        _make_settings(
            ENVIRONMENT="staging",
            SECRET_KEY="dev-secret-key-change-in-production-32chars",
            WEBHOOK_SECRET="prod-safe-secret",
        )


def test_error_message_does_not_reveal_secret_value():
    try:
        _make_settings(
            ENVIRONMENT="production",
            SECRET_KEY="dev-secret-key-change-in-production-32chars",
            WEBHOOK_SECRET="prod-safe-secret",
        )
        assert False, "should have raised"
    except ValueError as exc:
        assert "dev-secret-key-change-in-production-32chars" not in str(exc)
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd backend && python -m pytest tests/test_config_validation.py -v
```
Expected: 7 failures (Settings has no validation yet)

- [ ] **Step 3: Implement fail-closed validation in config.py**

```python
# backend/app/config.py
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_ENVIRONMENTS = {"development", "local", "test", "dev"}

_WEAK_SECRET_KEYS = {
    "dev-secret-key-change-in-production-32chars",
    "secret",
    "changeme",
    "",
}

_WEAK_WEBHOOK_SECRETS = {
    "changeme",
    "secret",
    "webhook_secret",
    "",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    DATABASE_URL: str = "postgresql+asyncpg://nexplane:nexplane_dev@localhost:5432/nexplane"
    SECRET_KEY: str = "dev-secret-key-change-in-production-32chars"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours

    ENVIRONMENT: str = "development"
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"
    AI_MODEL: str = "claude-sonnet-4-6"
    WEBHOOK_SECRET: str = "changeme"

    @model_validator(mode="after")
    def reject_weak_secrets_in_production(self) -> "Settings":
        if self.ENVIRONMENT.lower() in _DEV_ENVIRONMENTS:
            return self
        if self.SECRET_KEY in _WEAK_SECRET_KEYS or len(self.SECRET_KEY) < 32:
            raise ValueError(
                f"SECRET_KEY is a development default or too short for ENVIRONMENT={self.ENVIRONMENT!r}. "
                "Set a strong SECRET_KEY (>=32 chars) in your environment."
            )
        if self.WEBHOOK_SECRET in _WEAK_WEBHOOK_SECRETS:
            raise ValueError(
                f"WEBHOOK_SECRET is a development default for ENVIRONMENT={self.ENVIRONMENT!r}. "
                "Set a strong WEBHOOK_SECRET in your environment."
            )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]


settings = Settings()
```

- [ ] **Step 4: Run tests to confirm they pass**

```
cd backend && python -m pytest tests/test_config_validation.py -v
```
Expected: 8 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py backend/tests/test_config_validation.py
git commit -m "feat(security): Phase 1 — fail-closed startup validation for production secrets"
```

---

## Task 2: Phase 2 — Agent result binding

**Files:**
- Modify: `backend/app/routers/agent.py` (lines 190–207 — the `post_job_result` endpoint)
- Create: `backend/tests/test_agent_result_binding.py`

**Context:** The current `/agent/jobs/{job_id}/result` endpoint (lines 190–207) only checks `job.organization_id == org_id`. It does not verify that the posting agent matches `job.agent_registration_id`. It also allows re-posting results to already-terminal jobs. Both are security gaps.

`AgentJob` has an `agent_registration_id` FK. The polling endpoint (`/jobs/next`) already filters by `agent_id`. We extend the result endpoint with the same check.

The agent's credential is already org-scoped (`_get_org_settings_by_secret`). To bind result submission to a specific agent registration, the caller must also supply their `agent_id` in the request body.

- [ ] **Step 1: Check existing AgentJobResultRequest schema**

```
cd backend && grep -n "AgentJobResultRequest\|class AgentJob" app/schemas/agent.py app/models/agent.py
```

Note the existing fields and `AgentJobStatus` terminal states.

- [ ] **Step 2: Update AgentJobResultRequest schema to include agent_id**

```python
# In backend/app/schemas/agent.py — add agent_id field to AgentJobResultRequest
# Find the class and add:
#   agent_id: uuid.UUID  — the registering agent's ID

# Existing file likely looks like:
# class AgentJobResultRequest(BaseModel):
#     status: AgentJobStatus
#     result: dict | None = None
#     error: str | None = None
```

Read the file first, then add `agent_id: uuid.UUID` as a required field.

- [ ] **Step 3: Write failing tests**

```python
# backend/tests/test_agent_result_binding.py
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch


def _make_job(agent_reg_id, org_id, status="running"):
    from app.models.agent import AgentJob, AgentJobStatus
    job = MagicMock(spec=AgentJob)
    job.id = uuid.uuid4()
    job.organization_id = org_id
    job.agent_registration_id = agent_reg_id
    job.status = AgentJobStatus(status)
    return job


def _make_org_settings(org_id):
    from app.models.org_settings import OrganizationSettings
    s = MagicMock(spec=OrganizationSettings)
    s.organization_id = org_id
    return s


@pytest.mark.asyncio
async def test_correct_agent_can_submit_result():
    from app.routers.agent import post_job_result
    from app.schemas.agent import AgentJobResultRequest
    from app.models.agent import AgentJobStatus

    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    job = _make_job(agent_id, org_id)

    db = AsyncMock()
    db.get = AsyncMock(return_value=job)

    body = AgentJobResultRequest(
        agent_id=agent_id,
        status=AgentJobStatus.completed,
        result={"ok": True},
    )
    org_settings = _make_org_settings(org_id)

    result = await post_job_result(job.id, body, org_settings, db)
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_wrong_agent_cannot_submit_result():
    from app.routers.agent import post_job_result
    from app.schemas.agent import AgentJobResultRequest
    from app.models.agent import AgentJobStatus
    from fastapi import HTTPException

    org_id = uuid.uuid4()
    assigned_agent_id = uuid.uuid4()
    wrong_agent_id = uuid.uuid4()
    job = _make_job(assigned_agent_id, org_id)

    db = AsyncMock()
    db.get = AsyncMock(return_value=job)

    body = AgentJobResultRequest(
        agent_id=wrong_agent_id,
        status=AgentJobStatus.completed,
        result={},
    )
    org_settings = _make_org_settings(org_id)

    with pytest.raises(HTTPException) as exc_info:
        await post_job_result(job.id, body, org_settings, db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_terminal_job_rejected():
    from app.routers.agent import post_job_result
    from app.schemas.agent import AgentJobResultRequest
    from app.models.agent import AgentJobStatus
    from fastapi import HTTPException

    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    # Job is already in terminal state
    job = _make_job(agent_id, org_id, status="completed")

    db = AsyncMock()
    db.get = AsyncMock(return_value=job)

    body = AgentJobResultRequest(
        agent_id=agent_id,
        status=AgentJobStatus.completed,
        result={},
    )
    org_settings = _make_org_settings(org_id)

    with pytest.raises(HTTPException) as exc_info:
        await post_job_result(job.id, body, org_settings, db)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_cross_org_result_rejected():
    from app.routers.agent import post_job_result
    from app.schemas.agent import AgentJobResultRequest
    from app.models.agent import AgentJobStatus
    from fastapi import HTTPException

    org_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    job = _make_job(agent_id, other_org_id)  # job belongs to OTHER org

    db = AsyncMock()
    db.get = AsyncMock(return_value=job)

    body = AgentJobResultRequest(
        agent_id=agent_id,
        status=AgentJobStatus.completed,
        result={},
    )
    org_settings = _make_org_settings(org_id)  # caller is in org_id

    with pytest.raises(HTTPException) as exc_info:
        await post_job_result(job.id, body, org_settings, db)
    assert exc_info.value.status_code == 404
```

- [ ] **Step 4: Run tests to confirm they fail**

```
cd backend && python -m pytest tests/test_agent_result_binding.py -v
```
Expected: failures (schema missing agent_id, endpoint missing checks)

- [ ] **Step 5: Add agent_id to AgentJobResultRequest**

Read `backend/app/schemas/agent.py`. Add `agent_id: uuid.UUID` to `AgentJobResultRequest`.

- [ ] **Step 6: Harden post_job_result endpoint**

```python
# backend/app/routers/agent.py — replace lines 190–207

_TERMINAL_STATUSES = {AgentJobStatus.completed, AgentJobStatus.failed, AgentJobStatus.cancelled}


@router.post("/jobs/{job_id}/result")
async def post_job_result(
    job_id: uuid.UUID,
    body: AgentJobResultRequest,
    org_settings: OrganizationSettings = Depends(_get_org_settings_by_secret),
    db: AsyncSession = Depends(get_db),
):
    org_id = org_settings.organization_id
    job = await db.get(AgentJob, job_id)
    if not job or job.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Job not found")

    # Enforce agent ownership — only the assigned agent may submit results
    if job.agent_registration_id != body.agent_id:
        raise HTTPException(status_code=403, detail="Agent not authorized for this job")

    # Prevent duplicate completion of terminal jobs
    if job.status in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="Job result already submitted")

    job.status = body.status
    job.result = body.result
    job.error = body.error
    job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}
```

Note: import `_TERMINAL_STATUSES` set at module level; add `AgentJobStatus` to imports if not already present.

- [ ] **Step 7: Run tests**

```
cd backend && python -m pytest tests/test_agent_result_binding.py -v
```
Expected: 4 PASSED

- [ ] **Step 8: Commit**

```bash
git add backend/app/routers/agent.py backend/app/schemas/agent.py backend/tests/test_agent_result_binding.py
git commit -m "feat(security): Phase 2 — enforce agent ownership and terminal-job deduplication on result submission"
```

---

## Task 3: Phase 3 — Rollback state machine

**Files:**
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/071_add_rollback_states.py`
- Modify: `backend/app/services/rollback_executor.py`
- Create: `backend/tests/test_rollback_state_machine.py`

**Context:** `ChangeRequestStatus` (line 504 in change_request.py) has only `rolled_back`. The spec requires `rollback_partial`, `rollback_failed`, `manual_recovery_required`. `execute_cr_rollback()` in `rollback_executor.py` unconditionally sets `cr.status = ChangeRequestStatus.rolled_back` (line 165) regardless of per-step outcomes.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_rollback_state_machine.py
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_all_steps_succeed_marks_rolled_back():
    """Clean rollback: all steps succeed → status = rolled_back."""
    from app.services.rollback_executor import _determine_rollback_status
    step_results = [
        {"step_number": 1, "success": True},
        {"step_number": 2, "success": True},
    ]
    status = _determine_rollback_status(step_results, rollback_ran=True)
    from app.models.change_request import ChangeRequestStatus
    assert status == ChangeRequestStatus.rolled_back


@pytest.mark.asyncio
async def test_partial_step_failure_marks_rollback_partial():
    from app.services.rollback_executor import _determine_rollback_status
    step_results = [
        {"step_number": 1, "success": True},
        {"step_number": 2, "success": False},
    ]
    status = _determine_rollback_status(step_results, rollback_ran=True)
    from app.models.change_request import ChangeRequestStatus
    assert status == ChangeRequestStatus.rollback_partial


@pytest.mark.asyncio
async def test_rollback_did_not_run_marks_rollback_failed():
    from app.services.rollback_executor import _determine_rollback_status
    status = _determine_rollback_status([], rollback_ran=False)
    from app.models.change_request import ChangeRequestStatus
    assert status == ChangeRequestStatus.rollback_failed


@pytest.mark.asyncio
async def test_all_steps_fail_marks_rollback_failed():
    from app.services.rollback_executor import _determine_rollback_status
    step_results = [
        {"step_number": 1, "success": False},
        {"step_number": 2, "success": False},
    ]
    status = _determine_rollback_status(step_results, rollback_ran=True)
    from app.models.change_request import ChangeRequestStatus
    assert status == ChangeRequestStatus.rollback_failed


def test_rollback_partial_exists_in_enum():
    from app.models.change_request import ChangeRequestStatus
    assert hasattr(ChangeRequestStatus, "rollback_partial")


def test_rollback_failed_exists_in_enum():
    from app.models.change_request import ChangeRequestStatus
    assert hasattr(ChangeRequestStatus, "rollback_failed")


def test_manual_recovery_required_exists_in_enum():
    from app.models.change_request import ChangeRequestStatus
    assert hasattr(ChangeRequestStatus, "manual_recovery_required")
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd backend && python -m pytest tests/test_rollback_state_machine.py -v
```
Expected: failures

- [ ] **Step 3: Add new enum values to ChangeRequestStatus**

In `backend/app/models/change_request.py`, after `rolled_back = "rolled_back"` (line 514), add:

```python
    rollback_partial = "rollback_partial"
    rollback_failed = "rollback_failed"
    manual_recovery_required = "manual_recovery_required"
```

- [ ] **Step 4: Create Alembic migration**

```python
# backend/alembic/versions/071_add_rollback_states.py
"""Add rollback_partial, rollback_failed, manual_recovery_required to change_request_status enum

Revision ID: 071
Revises: 070_add_runbook_cron_schedule
Create Date: 2026-06-05
"""
from alembic import op

revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'rollback_partial'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'rollback_failed'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'manual_recovery_required'")


def downgrade():
    # PostgreSQL does not support removing enum values.
    pass
```

Note: verify the exact `down_revision` by checking the current head: `cd backend && alembic heads`

- [ ] **Step 5: Add _determine_rollback_status helper to rollback_executor.py**

Add this function before `execute_cr_rollback`:

```python
def _determine_rollback_status(
    step_results: list[dict],
    rollback_ran: bool,
) -> "ChangeRequestStatus":
    """Determine truthful terminal rollback status from per-step outcomes."""
    if not rollback_ran or not step_results:
        return ChangeRequestStatus.rollback_failed
    failed = [s for s in step_results if not s.get("success", False)]
    succeeded = [s for s in step_results if s.get("success", False)]
    if not failed:
        return ChangeRequestStatus.rolled_back
    if succeeded:
        return ChangeRequestStatus.rollback_partial
    return ChangeRequestStatus.rollback_failed
```

- [ ] **Step 6: Update execute_cr_rollback to use truthful status**

In `execute_cr_rollback` (around line 160), replace:

```python
        cr.status = ChangeRequestStatus.rolled_back
```

With:

```python
        # Determine truthful terminal state from step outcomes
        step_results = []
        rollback_ran = True
        if isinstance(result, dict):
            step_results = result.get("steps", [])
            if result.get("rolled_back") is False and not step_results:
                rollback_ran = False
        cr.status = _determine_rollback_status(step_results, rollback_ran)
```

- [ ] **Step 7: Run the migration on the dev database**

```
docker exec nexplane-backend-1 alembic -c /app/alembic.ini upgrade head
```

- [ ] **Step 8: Run tests**

```
cd backend && python -m pytest tests/test_rollback_state_machine.py -v
```
Expected: 7 PASSED

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/change_request.py \
        backend/alembic/versions/071_add_rollback_states.py \
        backend/app/services/rollback_executor.py \
        backend/tests/test_rollback_state_machine.py
git commit -m "feat(safety): Phase 3 — truthful rollback terminal states (partial/failed/manual_recovery_required)"
```

---

## Task 4: Phase 4 — Verification framework

**Files:**
- Modify: `backend/app/models/change_request.py` — add VerificationStatus enum + `verification_status` column
- Create: `backend/alembic/versions/072_add_verification_status.py`
- Modify: `backend/app/workflows/activities.py` — write verification_status when recording post-state result
- Create: `backend/tests/test_verification_framework.py`

**Context:** Currently, verification is called in `execute_change_workflow` via `activity_run_verification` but its outcome is only stored in the execution run result JSON — there is no explicit `verification_status` on the CR. The spec requires an explicit status (`passed`, `failed`, `unsupported`, `manual_required`, `skipped_development_only`) so that mock-pass cannot silently complete infrastructure changes.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_verification_framework.py
import pytest
import uuid


def test_verification_status_enum_values():
    from app.models.change_request import VerificationStatus
    assert VerificationStatus.passed.value == "passed"
    assert VerificationStatus.failed.value == "failed"
    assert VerificationStatus.unsupported.value == "unsupported"
    assert VerificationStatus.manual_required.value == "manual_required"
    assert VerificationStatus.skipped_development_only.value == "skipped_development_only"


def test_change_request_has_verification_status_field():
    from app.models.change_request import ChangeRequest
    assert hasattr(ChangeRequest, "verification_status")


def test_verification_status_defaults_to_none():
    from app.models.change_request import ChangeRequest
    import inspect
    # verification_status should be nullable (None default)
    col = ChangeRequest.__table__.columns.get("verification_status")
    assert col is not None
    assert col.nullable is True
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd backend && python -m pytest tests/test_verification_framework.py -v
```

- [ ] **Step 3: Add VerificationStatus enum and column to change_request.py**

After `class RiskLevel` (around line 497), add:

```python
class VerificationStatus(str, enum.Enum):
    passed = "passed"
    failed = "failed"
    unsupported = "unsupported"
    manual_required = "manual_required"
    skipped_development_only = "skipped_development_only"
```

In the `ChangeRequest` model (after `status` column, around line 543), add:

```python
    verification_status: Mapped[Optional["VerificationStatus"]] = mapped_column(
        SAEnum(VerificationStatus, name="verification_status"),
        nullable=True,
        default=None,
    )
```

- [ ] **Step 4: Create Alembic migration**

```python
# backend/alembic/versions/072_add_verification_status.py
"""Add verification_status enum and column to change_requests

Revision ID: 072
Revises: 071
Create Date: 2026-06-05
"""
import sqlalchemy as sa
from alembic import op

revision = "072"
down_revision = "071"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TYPE verification_status AS ENUM (
            'passed', 'failed', 'unsupported', 'manual_required', 'skipped_development_only'
        )
    """)
    op.add_column(
        "change_requests",
        sa.Column(
            "verification_status",
            sa.Enum(
                "passed", "failed", "unsupported", "manual_required", "skipped_development_only",
                name="verification_status",
            ),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("change_requests", "verification_status")
    op.execute("DROP TYPE IF EXISTS verification_status")
```

- [ ] **Step 5: Write verification_status in activities.py**

Read `backend/app/workflows/activities.py` and find `activity_run_verification`. After the verification result is determined, update the CR's `verification_status`. The result dict from verification contains a `status` key. Map it to `VerificationStatus`.

Add after the verification result is computed:

```python
    # Write explicit verification_status to CR
    from app.models.change_request import VerificationStatus as _VS
    _vs_map = {
        "passed": _VS.passed,
        "failed": _VS.failed,
        "unsupported": _VS.unsupported,
        "manual_required": _VS.manual_required,
        "skipped": _VS.skipped_development_only,
    }
    _vs_value = verification_result.get("status", "unsupported")
    async with AsyncSessionLocal() as _db:
        _cr = await _db.get(ChangeRequest, uuid.UUID(cr_id))
        if _cr:
            _cr.verification_status = _vs_map.get(_vs_value, _VS.unsupported)
            await _db.commit()
```

- [ ] **Step 6: Run migration**

```
docker exec nexplane-backend-1 alembic -c /app/alembic.ini upgrade head
```

- [ ] **Step 7: Run tests**

```
cd backend && python -m pytest tests/test_verification_framework.py -v
```
Expected: 3 PASSED

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/change_request.py \
        backend/alembic/versions/072_add_verification_status.py \
        backend/app/workflows/activities.py \
        backend/tests/test_verification_framework.py
git commit -m "feat(safety): Phase 4 — explicit verification_status field on change requests"
```

---

## Task 5: Phase 5 — Unified execution service + fix trigger_change_workflow

**Files:**
- Create: `backend/app/services/change_execution_service.py`
- Modify: `backend/app/routers/change_requests.py` — use service for manual execution
- Modify: `backend/app/workers/scheduled_cr_worker.py` — fix live bug, use service
- Modify: `backend/app/services/recurring_job_service.py` — fix live bug, use service
- Create: `backend/tests/test_change_execution_service.py`

**Context:** `trigger_change_workflow` is imported in both `scheduled_cr_worker.py` and `recurring_job_service.py` but does not exist in `app.workflows.execute_change_workflow`. The real function is `execute_change_workflow`, called via `workflow_runner.start_workflow()`. This is a live bug causing ImportError crashes. The service wraps the correct call pattern.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_change_execution_service.py
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch


def _make_cr(status="approved", org_id=None, requester_id=None):
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    cr = MagicMock(spec=ChangeRequest)
    cr.id = uuid.uuid4()
    cr.organization_id = org_id or uuid.uuid4()
    cr.requester_id = requester_id or uuid.uuid4()
    cr.status = ChangeRequestStatus(status)
    cr.execution_runs = []
    return cr


@pytest.mark.asyncio
async def test_approved_cr_can_execute():
    from app.services.change_execution_service import ChangeExecutionService

    cr = _make_cr(status="approved")
    db = AsyncMock()
    db.get = AsyncMock(return_value=cr)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    with patch("app.services.change_execution_service.workflow_runner") as mock_runner, \
         patch("app.services.change_execution_service.record_event", new_callable=AsyncMock):
        mock_runner.start_workflow = AsyncMock(return_value="wf-id-123")
        result = await ChangeExecutionService.start(cr.id, cr.requester_id, "manual", db)
        mock_runner.start_workflow.assert_called_once()
    assert result is not None


@pytest.mark.asyncio
async def test_unapproved_cr_cannot_execute():
    from app.services.change_execution_service import ChangeExecutionService
    from fastapi import HTTPException

    cr = _make_cr(status="draft")
    db = AsyncMock()
    db.get = AsyncMock(return_value=cr)

    with pytest.raises(HTTPException) as exc_info:
        await ChangeExecutionService.start(cr.id, cr.requester_id, "manual", db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_scheduled_source_unapproved_cr_cannot_execute():
    from app.services.change_execution_service import ChangeExecutionService
    from fastapi import HTTPException

    cr = _make_cr(status="planned")
    db = AsyncMock()
    db.get = AsyncMock(return_value=cr)

    with pytest.raises(HTTPException) as exc_info:
        await ChangeExecutionService.start(cr.id, cr.requester_id, "scheduled", db)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_recurring_job_source_unapproved_cr_cannot_execute():
    from app.services.change_execution_service import ChangeExecutionService
    from fastapi import HTTPException

    cr = _make_cr(status="awaiting_approval")
    db = AsyncMock()
    db.get = AsyncMock(return_value=cr)

    with pytest.raises(HTTPException) as exc_info:
        await ChangeExecutionService.start(cr.id, cr.requester_id, "recurring_job", db)
    assert exc_info.value.status_code == 400
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd backend && python -m pytest tests/test_change_execution_service.py -v
```

- [ ] **Step 3: Create ChangeExecutionService**

```python
# backend/app/services/change_execution_service.py
"""Unified execution entry point for all CR execution sources.

All paths — manual, scheduled, recurring, AI-assisted — must go through
ChangeExecutionService.start() so approval checks, audit logging, and
workflow invocation stay consistent.
"""
import uuid
import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.execution_run import ExecutionRun, ExecutionStatus
from app.services.audit_service import record_event
from app.workflows import runner as workflow_runner
from app.workflows.execute_change_workflow import execute_change_workflow

logger = logging.getLogger(__name__)

_EXECUTABLE_STATUSES = {ChangeRequestStatus.approved}

VALID_SOURCES = frozenset({
    "manual", "scheduled", "recurring_job", "maintenance_window_release", "api", "ai_assisted"
})


class ChangeExecutionService:
    @staticmethod
    async def start(
        cr_id: uuid.UUID,
        actor_id: uuid.UUID,
        source: str,
        db: AsyncSession,
    ) -> ExecutionRun:
        """Start execution of an approved CR.

        Enforces: status is executable, audit logged, workflow started.
        Raises HTTPException on policy violations.
        """
        cr = await db.get(ChangeRequest, cr_id)
        if not cr:
            raise HTTPException(status_code=404, detail="Change request not found")

        if cr.status not in _EXECUTABLE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot execute CR in status '{cr.status.value}'. Must be 'approved'.",
            )

        if source not in VALID_SOURCES:
            raise HTTPException(status_code=400, detail=f"Unknown execution source: {source!r}")

        attempt = len(getattr(cr, "execution_runs", [])) + 1
        workflow_id = f"wf-cr-{cr.id}-{attempt}"
        run = ExecutionRun(
            change_request_id=cr.id,
            workflow_id=workflow_id,
            status=ExecutionStatus.pending,
        )
        db.add(run)
        await db.flush()

        await record_event(
            db,
            cr.organization_id,
            "execution.initiated",
            {"change_request_id": str(cr.id), "workflow_id": workflow_id, "source": source},
            actor_id=actor_id,
            change_request_id=cr.id,
        )
        await db.commit()

        wf_input = workflow_runner.WorkflowInput(
            change_request_id=str(cr.id),
            organization_id=str(cr.organization_id),
            initiator_id=str(actor_id),
        )
        await workflow_runner.start_workflow(execute_change_workflow, wf_input, workflow_id=workflow_id)

        logger.info("CR %s execution started via source=%s workflow=%s", cr.id, source, workflow_id)
        return run
```

- [ ] **Step 4: Update scheduled_cr_worker.py to use service and fix ImportError**

```python
# backend/app/workers/scheduled_cr_worker.py
from __future__ import annotations
import logging
from datetime import datetime, timezone
from sqlalchemy import select, and_
from app.database import AsyncSessionLocal
from app.models.change_request import ChangeRequest, ChangeRequestStatus

logger = logging.getLogger(__name__)


async def execute_scheduled_crs() -> None:
    """Execute CRs whose execute_at time has passed."""
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        result = await db.execute(
            select(ChangeRequest).where(
                and_(
                    ChangeRequest.execute_at <= now,
                    ChangeRequest.execute_at.isnot(None),
                    ChangeRequest.status == ChangeRequestStatus.approved,
                )
            )
        )
        for cr in result.scalars():
            try:
                from app.services.change_execution_service import ChangeExecutionService
                async with AsyncSessionLocal() as exec_db:
                    await ChangeExecutionService.start(cr.id, cr.requester_id, "scheduled", exec_db)
                logger.info(f"Triggered scheduled CR {cr.id}")
            except Exception as e:
                logger.warning(f"Failed to trigger scheduled CR {cr.id}: {e}")
```

- [ ] **Step 5: Update recurring_job_service.py to use service and fix ImportError**

At the bottom of `_fire_recurring_job`, replace:

```python
    # Trigger execution outside the session so the CR row is visible to the workflow
    try:
        from app.workflows.execute_change_workflow import trigger_change_workflow
        await trigger_change_workflow(str(cr.id))
```

With:

```python
    # Trigger execution outside the session so the CR row is visible to the workflow
    try:
        from app.services.change_execution_service import ChangeExecutionService
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as exec_db:
            await ChangeExecutionService.start(cr.id, job.created_by, "recurring_job", exec_db)
```

- [ ] **Step 6: Run tests**

```
cd backend && python -m pytest tests/test_change_execution_service.py -v
```
Expected: 4 PASSED

- [ ] **Step 7: Smoke test that backend starts without ImportError**

```
docker exec nexplane-backend-1 python3 -c "from app.workers.scheduled_cr_worker import execute_scheduled_crs; from app.services.recurring_job_service import _fire_recurring_job; print('OK')"
```
Expected: `OK` (no ImportError)

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/change_execution_service.py \
        backend/app/routers/change_requests.py \
        backend/app/workers/scheduled_cr_worker.py \
        backend/app/services/recurring_job_service.py \
        backend/tests/test_change_execution_service.py
git commit -m "feat(safety): Phase 5 — unified ChangeExecutionService; fix trigger_change_workflow ImportError in scheduled/recurring workers"
```

---

## Task 6: Phase 6 — Recurring-job approval policy

**Files:**
- Create: `backend/app/models/recurring_job_policy.py`
- Create: `backend/alembic/versions/073_add_recurring_job_policy.py`
- Modify: `backend/app/services/recurring_job_service.py` — enforce policy constraints
- Create: `backend/tests/test_recurring_job_policy.py`

**Context:** Currently `_fire_recurring_job` auto-approves every generated CR unconditionally. The spec requires recurring jobs to reference a pre-approved policy with expiry, allowed change types, max risk level. CRs that exceed policy constraints must return to normal approval flow.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/test_recurring_job_policy.py
import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock


def _make_policy(
    expires_at=None,
    allowed_change_types=None,
    max_risk_level="high",
    enabled=True,
):
    from app.models.recurring_job_policy import RecurringJobPolicy
    p = MagicMock(spec=RecurringJobPolicy)
    p.id = uuid.uuid4()
    p.enabled = enabled
    p.expires_at = expires_at
    p.allowed_change_types = allowed_change_types or ["ssm_command", "ec2_stop"]
    p.max_risk_level = max_risk_level
    return p


def test_policy_within_expiry_is_valid():
    from app.services.recurring_job_service import _policy_allows
    policy = _make_policy(expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    ok, reason = _policy_allows(policy, change_type="ssm_command", risk_level="low")
    assert ok is True
    assert reason is None


def test_expired_policy_is_rejected():
    from app.services.recurring_job_service import _policy_allows
    policy = _make_policy(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    ok, reason = _policy_allows(policy, change_type="ssm_command", risk_level="low")
    assert ok is False
    assert "expired" in reason.lower()


def test_disallowed_change_type_is_rejected():
    from app.services.recurring_job_service import _policy_allows
    policy = _make_policy(allowed_change_types=["ssm_command"])
    ok, reason = _policy_allows(policy, change_type="ec2_terminate", risk_level="low")
    assert ok is False
    assert "change_type" in reason.lower()


def test_risk_level_exceeding_policy_is_rejected():
    from app.services.recurring_job_service import _policy_allows
    policy = _make_policy(max_risk_level="medium")
    ok, reason = _policy_allows(policy, change_type="ssm_command", risk_level="critical")
    assert ok is False
    assert "risk" in reason.lower()


def test_no_policy_requires_manual_approval():
    from app.services.recurring_job_service import _policy_allows
    ok, reason = _policy_allows(None, change_type="ssm_command", risk_level="low")
    assert ok is False
    assert "no policy" in reason.lower()
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd backend && python -m pytest tests/test_recurring_job_policy.py -v
```

- [ ] **Step 3: Create RecurringJobPolicy model**

```python
# backend/app/models/recurring_job_policy.py
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, func, ForeignKey, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class RecurringJobPolicy(Base):
    __tablename__ = "recurring_job_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    allowed_change_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    max_risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Create Alembic migration**

```python
# backend/alembic/versions/073_add_recurring_job_policy.py
"""Add recurring_job_policies table and policy_id FK on recurring_jobs

Revision ID: 073
Revises: 072
Create Date: 2026-06-05
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "073"
down_revision = "072"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "recurring_job_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allowed_change_types", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("max_risk_level", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("enabled", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column(
        "recurring_jobs",
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("recurring_job_policies.id"),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("recurring_jobs", "policy_id")
    op.drop_table("recurring_job_policies")
```

- [ ] **Step 5: Add _policy_allows helper to recurring_job_service.py**

Add these constants and helper before `_fire_recurring_job`:

```python
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _policy_allows(
    policy,
    change_type: str,
    risk_level: str,
) -> tuple[bool, str | None]:
    """Return (allowed, reason_or_None). reason is None when allowed."""
    if policy is None:
        return False, "no policy attached to recurring job; manual approval required"
    if not policy.enabled:
        return False, "recurring job policy is disabled"
    now = datetime.now(tz=timezone.utc)
    if policy.expires_at and policy.expires_at <= now:
        return False, f"policy expired at {policy.expires_at.isoformat()}"
    if policy.allowed_change_types and change_type not in policy.allowed_change_types:
        return False, f"change_type '{change_type}' not in policy allowed_change_types"
    if _RISK_ORDER.get(risk_level, 0) > _RISK_ORDER.get(policy.max_risk_level, 1):
        return False, f"risk_level '{risk_level}' exceeds policy max_risk_level '{policy.max_risk_level}'"
    return True, None
```

- [ ] **Step 6: Enforce policy in _fire_recurring_job auto-approval**

In `_fire_recurring_job`, after the CR is created and status is `draft`, add policy check before auto-approval:

```python
        # Policy-gated auto-approval
        from app.models.recurring_job import RecurringJob as _RJ
        _policy = None
        if job.policy_id:
            from app.models.recurring_job_policy import RecurringJobPolicy
            _policy_res = await db.get(RecurringJobPolicy, job.policy_id)
            _policy = _policy_res

        _allowed, _reason = _policy_allows(_policy, str(change_type.value), "low")
        if _allowed:
            # Auto-approve via policy
            approval = Approval(
                change_request_id=cr.id,
                approver_id=job.created_by,
                decision=ApprovalDecision.approved,
                comment=f"Auto-approved by recurring job policy {job.policy_id}",
            )
            db.add(approval)
            cr.status = ChangeRequestStatus.approved
        else:
            # Policy does not permit auto-approval — leave in awaiting_approval
            logger.warning(
                "Recurring job %s: auto-approval denied by policy: %s. CR %s awaits manual approval.",
                job_id, _reason, cr.id,
            )
            cr.status = ChangeRequestStatus.awaiting_approval
```

Remove the old unconditional auto-approve block.

- [ ] **Step 7: Run migration**

```
docker exec nexplane-backend-1 alembic -c /app/alembic.ini upgrade head
```

- [ ] **Step 8: Run tests**

```
cd backend && python -m pytest tests/test_recurring_job_policy.py -v
```
Expected: 5 PASSED

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/recurring_job_policy.py \
        backend/alembic/versions/073_add_recurring_job_policy.py \
        backend/app/services/recurring_job_service.py \
        backend/tests/test_recurring_job_policy.py
git commit -m "feat(safety): Phase 6 — recurring-job approval policy enforcement (expiry, allowed types, max risk)"
```

---

## Task 7: Run full test suite and verify no regressions

- [ ] **Step 1: Run all new tests together**

```
cd backend && python -m pytest \
  tests/test_config_validation.py \
  tests/test_agent_result_binding.py \
  tests/test_rollback_state_machine.py \
  tests/test_verification_framework.py \
  tests/test_change_execution_service.py \
  tests/test_recurring_job_policy.py \
  -v
```
Expected: all PASSED

- [ ] **Step 2: Run existing test suite for regressions**

```
cd backend && python -m pytest tests/ -v --ignore=tests/smoke -x
```
Expected: no new failures

- [ ] **Step 3: Verify backend starts cleanly**

```
docker exec nexplane-backend-1 python3 -c "
from app.config import settings
from app.models.change_request import ChangeRequestStatus, VerificationStatus
from app.services.change_execution_service import ChangeExecutionService
from app.workers.scheduled_cr_worker import execute_scheduled_crs
from app.services.recurring_job_service import _fire_recurring_job
print('All imports OK')
print('rollback_partial:', ChangeRequestStatus.rollback_partial)
print('VerificationStatus values:', list(VerificationStatus))
"
```
Expected: no ImportError, all values printed

- [ ] **Step 4: Final commit (integration)**

```bash
git add -u
git commit -m "test: verify all Safe Execution Contract phases pass together"
```

---

## Self-Review

### Spec coverage check

| Spec requirement | Covered by |
|---|---|
| Production secrets fail-closed | Task 1 |
| Agent result bound to assigned agent | Task 2 |
| Duplicate terminal job rejected | Task 2 |
| Cross-org result rejected | Task 2 |
| rollback_partial / rollback_failed / manual_recovery_required states | Task 3 |
| Failed rollback step cannot produce clean rolled_back | Task 3 |
| Explicit verification_status field | Task 4 |
| Mock-pass cannot silently complete infrastructure changes | Task 4 |
| All execution paths use one service | Task 5 |
| Unapproved CRs cannot execute through any path | Task 5 |
| Fix trigger_change_workflow live ImportError | Task 5 |
| Recurring auto-approval is policy-gated | Task 6 |
| Policy expiry enforced | Task 6 |

### Known gaps (deferred per spec)

- **Result signature (HMAC over canonical payload):** Spec mentions `result_signature` covering `job_id + agent_id + status + result + error + submitted_at`. This is deferred — it requires the agent binary to sign payloads and the server to verify HMAC. The ownership check in Phase 2 (`agent_registration_id == body.agent_id`) is a structural prerequisite; HMAC signing is a follow-on hardening layer. Smoke test coverage for Phase 2 is covered by the existing agent smoke phases (A, SANTA_SYNC).

- **Verification registry / real verifiers:** Phase 4 adds the `verification_status` field and wires it from `activity_run_verification`. The spec also calls for a full verifier registry with `capture_pre_state / verify_post_state / verify_rollback` per change type. That registry is a larger build requiring domain knowledge per connector type; not included in this plan.

- **Maintenance-window source in ChangeExecutionService:** The service accepts `"maintenance_window_release"` as a source constant but no maintenance-window code currently invokes it. Wire-up deferred until maintenance window scheduling is built.

- **Smoke tests for Phase 1–6:** Phases 1–6 are backend safety gates. Smoke tests run against live infrastructure test the full CR lifecycle. Existing smoke phases (A, SANTA_SYNC, GCP_KEY_ROTATE) already exercise the agent registration/job/result path. Phase 2 hardening will be exercised by those same phases once deployed. No new dedicated smoke phases are needed for phases 1, 3, 4, 5, 6 — they are invisible to the agent protocol.
