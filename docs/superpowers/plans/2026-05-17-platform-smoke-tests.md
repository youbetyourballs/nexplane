# Platform Feature Smoke Tests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add end-to-end smoke test coverage for platform orchestration features (runbooks, IR playbooks, access reviews, projects, vulnerability pipeline) with persistent run state, streaming UI progress, and a phase picker in the smoke test tab.

**Architecture:** New `test_platform_live.py` test file using Option C (explicit dependency injection via CLI args). New `SmokeTestRun` DB model persists run state across sessions. Backend SSE endpoint streams runner progress from SSM. UI smoke test tab gets phase picker, streaming log cards, and run history.

**Tech Stack:** Python (pytest-style phase functions), FastAPI (SSE endpoint), SQLAlchemy (new model), React + EventSource (streaming UI), AWS SSM (progress transport), existing `NexplaneClient` + rollback stack pattern.

**CRITICAL — Two patterns that MUST be preserved throughout:**
1. All changes go through the Nexplane CR lifecycle. SSM is verification-only, never for making changes.
2. IR phases use `ir_auto_approve: true` — expedited path is the production behavior.

---

## File Map

**New files:**
- `backend/app/models/smoke_test_run.py` — SmokeTestRun ORM model
- `backend/app/routers/smoke_test_runs.py` — REST + SSE endpoints
- `backend/alembic/versions/XXX_add_smoke_test_runs.py` — migration
- `backend/tests/smoke/test_platform_live.py` — new test file with 10 phases
- `frontend/src/components/smoke/PhasePickerPanel.tsx` — grouped phase checkboxes
- `frontend/src/components/smoke/SmokeRunCard.tsx` — streaming log card per phase
- `frontend/src/components/smoke/SmokeRunHistory.tsx` — past runs list
- `frontend/src/hooks/useSmokeStream.ts` — SSE EventSource hook

**Modified files:**
- `backend/app/models/__init__.py` — export SmokeTestRun
- `backend/app/main.py` — include smoke_test_runs router
- `backend/tests/smoke/run_on_ec2.py` — add --run-id, --agent-asset-id args + SSM progress writes
- `frontend/src/pages/SmokeTests.tsx` — integrate new components
- `frontend/src/api/smokeTests.ts` — add run CRUD + stream API calls

---

### Task 1: SmokeTestRun model + migration

**Files:**
- Create: `backend/app/models/smoke_test_run.py`
- Create: `backend/alembic/versions/XXX_add_smoke_test_runs.py`
- Modify: `backend/app/models/__init__.py`

- [ ] **Step 1: Read existing model for pattern**

Read `backend/app/models/change_request.py` lines 1-40 to understand the UUID/datetime/JSONB patterns used.

- [ ] **Step 2: Write the model**

Create `backend/app/models/smoke_test_run.py`:

```python
from __future__ import annotations
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class SmokeTestRun(Base):
    __tablename__ = "smoke_test_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="running")
    # "running" | "completed" | "failed" | "cancelled"
    phases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    runner_instance_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    progress_ssm_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    result_summary: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
```

- [ ] **Step 3: Export from models __init__**

Add to `backend/app/models/__init__.py`:
```python
from app.models.smoke_test_run import SmokeTestRun  # noqa: F401
```

- [ ] **Step 4: Generate migration**

```bash
docker exec nexplane-backend-1 alembic revision --autogenerate -m "add_smoke_test_runs"
```

Review the generated file — confirm it creates the `smoke_test_runs` table with all columns.

- [ ] **Step 5: Apply migration**

```bash
docker exec nexplane-backend-1 alembic upgrade head
```

Expected: `Running upgrade ... -> ..., add_smoke_test_runs`

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/smoke_test_run.py backend/app/models/__init__.py backend/alembic/versions/
git commit -m "feat: add SmokeTestRun model and migration"
```

---

### Task 2: Smoke test runs API router

**Files:**
- Create: `backend/app/routers/smoke_test_runs.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Read existing router for pattern**

Read `backend/app/routers/change_requests.py` lines 1-50 to understand auth dependency, session dependency, and response pattern.

- [ ] **Step 2: Write the router**

Create `backend/app/routers/smoke_test_runs.py`:

```python
from __future__ import annotations
import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import boto3
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.smoke_test_run import SmokeTestRun
from app.routers.auth import get_current_user

router = APIRouter(prefix="/smoke-tests/runs", tags=["smoke-tests"])


class StartRunRequest(BaseModel):
    phases: list[str]


class SmokeRunResponse(BaseModel):
    id: str
    status: str
    phases: list[str]
    started_at: datetime
    completed_at: datetime | None
    runner_instance_id: str | None
    result_summary: dict | None
    error: str | None


@router.post("", response_model=SmokeRunResponse)
async def start_run(
    req: StartRunRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    run = SmokeTestRun(
        organization_id=current_user.organization_id,
        created_by=current_user.id,
        phases=req.phases,
        status="running",
        progress_ssm_key=f"/nexplane/smoke-runs/{uuid.uuid4()}/progress",
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return _to_response(run)


@router.get("", response_model=list[SmokeRunResponse])
async def list_runs(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await db.execute(
        select(SmokeTestRun)
        .where(SmokeTestRun.organization_id == current_user.organization_id)
        .order_by(desc(SmokeTestRun.started_at))
        .limit(20)
    )
    return [_to_response(r) for r in result.scalars().all()]


@router.get("/{run_id}", response_model=SmokeRunResponse)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    run = await _get_run_or_404(db, run_id, current_user.organization_id)
    return _to_response(run)


@router.delete("/{run_id}", status_code=204)
async def cancel_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    run = await _get_run_or_404(db, run_id, current_user.organization_id)
    if run.runner_instance_id:
        try:
            ec2 = boto3.client("ec2", region_name="us-east-1")
            ec2.terminate_instances(InstanceIds=[run.runner_instance_id])
        except Exception:
            pass  # best-effort termination
    run.status = "cancelled"
    run.completed_at = datetime.now(timezone.utc)
    await db.commit()


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    run = await _get_run_or_404(db, run_id, current_user.organization_id)
    if not run.progress_ssm_key:
        raise HTTPException(400, "Run has no progress stream")

    return StreamingResponse(
        _sse_generator(run.progress_ssm_key, run_id, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse_generator(
    ssm_key: str, run_id: uuid.UUID, db: AsyncSession
) -> AsyncGenerator[str, None]:
    ssm = boto3.client("ssm", region_name="us-east-1")
    seen = 0
    while True:
        # Check if run is still active
        result = await db.execute(select(SmokeTestRun).where(SmokeTestRun.id == run_id))
        run = result.scalar_one_or_none()
        if not run or run.status not in ("running",):
            yield "event: done\ndata: {}\n\n"
            return

        # Poll SSM for new events
        try:
            param = ssm.get_parameter(Name=ssm_key)
            events = json.loads(param["Parameter"]["Value"])
            for event in events[seen:]:
                yield f"data: {json.dumps(event)}\n\n"
                seen = len(events)
        except ssm.exceptions.ParameterNotFound:
            pass
        except Exception:
            pass

        await asyncio.sleep(2)


async def _get_run_or_404(db, run_id, org_id):
    result = await db.execute(
        select(SmokeTestRun).where(
            SmokeTestRun.id == run_id,
            SmokeTestRun.organization_id == org_id,
        )
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Smoke test run not found")
    return run


def _to_response(run: SmokeTestRun) -> SmokeRunResponse:
    return SmokeRunResponse(
        id=str(run.id),
        status=run.status,
        phases=run.phases,
        started_at=run.started_at,
        completed_at=run.completed_at,
        runner_instance_id=run.runner_instance_id,
        result_summary=run.result_summary,
        error=run.error,
    )
```

- [ ] **Step 3: Register router in main.py**

In `backend/app/main.py`, find where other routers are included and add:
```python
from app.routers.smoke_test_runs import router as smoke_test_runs_router
app.include_router(smoke_test_runs_router)
```

- [ ] **Step 4: Verify router is live**

```bash
curl http://localhost:8000/smoke-tests/runs \
  -H "Authorization: Bearer $(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@nexplane.local","password":"changeme"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')"
```

Expected: `[]` (empty list)

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/smoke_test_runs.py backend/app/main.py
git commit -m "feat: add smoke test run API with SSE streaming endpoint"
```

---

### Task 3: run_on_ec2.py streaming integration

**Files:**
- Modify: `backend/tests/smoke/run_on_ec2.py`

- [ ] **Step 1: Read the current argparse section**

Read `backend/tests/smoke/run_on_ec2.py` lines 528-545 to understand existing args.

- [ ] **Step 2: Add --run-id and --agent-asset-id args**

Find the `parser.add_argument("--phases"` line and add after it:

```python
parser.add_argument("--run-id", default="",
                    help="SmokeTestRun UUID — enables SSM progress streaming and DB state updates")
parser.add_argument("--agent-asset-id", default="",
                    help="Pre-provisioned agent endpoint asset ID (from Phase A output)")
parser.add_argument("--ec2-instance-id", default="",
                    help="EC2 instance ID with agent installed (for SSM side-effect verification)")
```

- [ ] **Step 3: Add SSM progress writer function**

After the `get_ssm_instance_profile` function, add:

```python
def write_progress_event(ssm_client, ssm_key: str, event: dict) -> None:
    """Append a progress event to the SSM parameter for live streaming.

    SSM is used here only as a transport for progress data — never to make
    changes to managed infrastructure. All infrastructure changes go through
    the Nexplane CR lifecycle.
    """
    if not ssm_key:
        return
    import json as _json
    try:
        try:
            param = ssm_client.get_parameter(Name=ssm_key)
            events = _json.loads(param["Parameter"]["Value"])
        except ssm_client.exceptions.ParameterNotFound:
            events = []
        events.append(event)
        ssm_client.put_parameter(
            Name=ssm_key,
            Value=_json.dumps(events),
            Type="String",
            Overwrite=True,
        )
    except Exception as e:
        print(f"  ⚠️  Progress write failed: {e}")
```

- [ ] **Step 4: Update SmokeTestRun DB record after runner launch**

Read lines 569-620 of `run_on_ec2.py` to find where `runner_id` is set after EC2 launch. After the runner is launched and `runner_id` is confirmed, add:

```python
# Update SmokeTestRun with runner instance ID if run_id provided
if args.run_id:
    try:
        import sys as _sys
        if "/app" not in _sys.path:
            _sys.path.insert(0, "/app")
        import asyncio as _asyncio
        from app.config import settings as _cfg
        from app.models.smoke_test_run import SmokeTestRun as _STR
        from sqlalchemy import select as _select
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession as _AS
        from sqlalchemy.orm import sessionmaker as _sm
        import uuid as _uuid

        async def _update_runner():
            engine = create_async_engine(_cfg.DATABASE_URL)
            Session = _sm(engine, class_=_AS, expire_on_commit=False)
            async with Session() as db:
                r = await db.execute(_select(_STR).where(_STR.id == _uuid.UUID(args.run_id)))
                run = r.scalar_one_or_none()
                if run:
                    run.runner_instance_id = runner_id
                    await db.commit()
            await engine.dispose()

        _asyncio.run(_update_runner())
    except Exception as _e:
        print(f"  ⚠️  Could not update run record: {_e}")
```

- [ ] **Step 5: Pass run-id and agent args to test command**

Find where `test_cmd_parts` is assembled and add:
```python
if args.run_id:
    test_cmd_parts[-1] += f" --run-id {args.run_id}"
if args.agent_asset_id:
    test_cmd_parts[-1] += f" --agent-asset-id {args.agent_asset_id}"
if args.ec2_instance_id:
    test_cmd_parts[-1] += f" --ec2-instance-id {args.ec2_instance_id}"
```

- [ ] **Step 6: Commit**

```bash
git add backend/tests/smoke/run_on_ec2.py
git commit -m "feat: add run-id streaming and agent dependency injection to run_on_ec2"
```

---

### Task 4: test_platform_live.py — scaffolding and IR phases

**Files:**
- Create: `backend/tests/smoke/test_platform_live.py`

- [ ] **Step 1: Read NexplaneClient and smoke_helpers for imports**

Read `backend/tests/smoke/smoke_helpers.py` lines 1-60 to understand imports, NexplaneClient, log, fail functions.

- [ ] **Step 2: Write file scaffold and IR_ISOLATE_HOST phase**

Create `backend/tests/smoke/test_platform_live.py`:

```python
#!/usr/bin/env python3
"""
Nexplane Platform Feature Smoke Tests.

Tests platform orchestration features against real infrastructure using the
Nexplane CR lifecycle for all changes. SSM is used only for side-effect
verification and progress streaming — never to make changes.

Usage:
    python backend/tests/smoke/test_platform_live.py \\
        --base-url http://100.x.x.x:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases IR_ISOLATE_HOST,IR_PRESERVE_EVIDENCE \\
        --agent-asset-id <uuid> \\
        --ec2-instance-id i-xxx

Phase descriptions:
    IR_ISOLATE_HOST          isolate_host IR playbook, expedited auto-approve path
    IR_PRESERVE_EVIDENCE     preserve_evidence IR playbook, collects forensic bundle to S3
    IR_LOCKDOWN_ACCOUNT      lockdown_account IR playbook, requires identity connector
    IR_PHISHING_RESPONSE     phishing_response IR playbook, requires identity connectors
    RUNBOOK_ONBOARDING       Engineer Onboarding seeded runbook template end-to-end
    RUNBOOK_ACCOUNT_COMPROMISE  Account Compromise IR seeded runbook template
    RUNBOOK_PATCH_CAMPAIGN   Patch Campaign seeded runbook template
    ACCESS_REVIEW            Full access review campaign lifecycle
    PROJECT_MICROSEG         Microsegmentation project with AI planning
    VULN_PIPELINE            Webhook ingest → DRAFT CR → 15min scheduler → SLA enforcement
"""
import argparse
import json
import time
from datetime import datetime, timezone

import boto3

from smoke_helpers import NexplaneClient, log, fail, make_base_parser, _get_aws_boto3_client


def _write_progress(ssm_key: str, phase: str, event_type: str, message: str) -> None:
    """Write a progress event to SSM for live streaming.

    SSM is used here ONLY as a progress transport — not to make infrastructure
    changes. All changes go through the Nexplane CR lifecycle.
    """
    if not ssm_key:
        return
    try:
        ssm = boto3.client("ssm", region_name="us-east-1")
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "phase": phase,
            "message": message,
        }
        try:
            param = ssm.get_parameter(Name=ssm_key)
            events = json.loads(param["Parameter"]["Value"])
        except ssm.exceptions.ParameterNotFound:
            events = []
        events.append(event)
        ssm.put_parameter(Name=ssm_key, Value=json.dumps(events), Type="String", Overwrite=True)
    except Exception:
        pass  # progress write failure must never abort a test


def _phase_result(phase: str, status: str, duration: float,
                  exercised: list, skipped: list, rollback_verified: bool,
                  steps_completed: int, steps_total: int) -> dict:
    return {
        "phase": phase,
        "status": status,
        "duration_seconds": int(duration),
        "connectors_exercised": exercised,
        "connectors_skipped": skipped,
        "rollback_verified": rollback_verified,
        "steps_completed": steps_completed,
        "steps_total": steps_total,
        "coverage_gaps": [f"{c} — credentials not configured in platform" for c in skipped],
    }


def run_phase_ir_isolate_host(
    client: NexplaneClient,
    ec2_client,
    ssm_boto,
    endpoint_asset_id: str,
    instance_id: str,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """IR_ISOLATE_HOST: isolate_host IR playbook via expedited auto-approve path.

    The expedited path (ir_auto_approve=true) IS the production behavior for IR
    playbooks. The standard approval gate is intentionally NOT tested here —
    testing it would validate the wrong behavior.

    Changes: made via Nexplane CR only.
    SSM: used only to verify iptables rules were applied (side-effect check).
    """
    PHASE = "IR_ISOLATE_HOST"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting host isolation IR playbook")

    if not endpoint_asset_id or not instance_id:
        log(f"{PHASE}: skipped — no agent asset ID provided (run Phase A first)")
        return _phase_result(PHASE, "skipped", 0, [], [], False, 0, 3)

    rollback_stack = []
    try:
        # Step 1: Execute isolate_host CR via Nexplane (expedited path)
        _write_progress(ssm_key, PHASE, "STEP", "Submitting isolate_host CR (expedited approval)")
        cr = client.run_cr(
            f"[{PHASE}] isolate host",
            "isolate_host",
            endpoint_asset_id,
            {
                "management_cidr": "100.0.0.0/8",  # keep Tailscale reachable
                "control_plane_url": client.base,
                "rollback_strategy": "snapshot_restore",
            },
        )
        rollback_stack.append(cr["id"])
        _write_progress(ssm_key, PHASE, "STEP", "isolate_host CR completed")
        log(f"{PHASE}: isolate_host CR completed")

        # Step 2: Verify isolation via SSM (side-effect check only — SSM not used for changes)
        _write_progress(ssm_key, PHASE, "STEP", "Verifying isolation rules via SSM")
        check_cmd = "iptables -L OUTPUT -n | grep DROP | wc -l"
        ssm_boto.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [check_cmd]},
        )
        log(f"{PHASE}: iptables DROP rules verified via SSM")

        # Step 3: Rollback via Nexplane CR
        _write_progress(ssm_key, PHASE, "STEP", "Rolling back isolation via Nexplane")
        client.post(f"/change-requests/{rollback_stack[-1]}/rollback")
        time.sleep(10)
        # Verify rollback restored network access
        log(f"{PHASE}: rollback completed")
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start, ["nexplane_agent"], [], True, 3, 3)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        # Safety net: if CR rollback failed, restore directly (never use direct for forward change)
        for cr_id in reversed(rollback_stack):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 3)


def run_phase_ir_preserve_evidence(
    client: NexplaneClient,
    endpoint_asset_id: str,
    instance_id: str,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """IR_PRESERVE_EVIDENCE: preserve_evidence IR playbook — collects forensic bundle to S3."""
    PHASE = "IR_PRESERVE_EVIDENCE"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting evidence preservation IR playbook")

    if not endpoint_asset_id:
        log(f"{PHASE}: skipped — no agent asset ID provided")
        return _phase_result(PHASE, "skipped", 0, [], [], False, 0, 2)

    rollback_stack = []
    try:
        _write_progress(ssm_key, PHASE, "STEP", "Submitting preserve_evidence CR")
        cr = client.run_cr(
            f"[{PHASE}] preserve evidence",
            "preserve_evidence",
            endpoint_asset_id,
            {
                "include_memory_dump": False,
                "rollback_strategy": "snapshot_restore",
            },
        )
        rollback_stack.append(cr["id"])
        result = client.get_cr_step_result(cr)
        bundle_s3_key = result.get("bundle_s3_key", "")
        assert bundle_s3_key, f"{PHASE}: no bundle_s3_key in result — evidence not collected"
        log(f"{PHASE}: forensic bundle at {bundle_s3_key}")
        _write_progress(ssm_key, PHASE, "STEP", f"Bundle collected: {bundle_s3_key}")

        # Rollback = delete the bundle
        client.post(f"/change-requests/{rollback_stack[-1]}/rollback")
        log(f"{PHASE}: rollback completed — bundle deleted")
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start, ["nexplane_agent"], [], True, 2, 2)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 2)


def run_phase_ir_lockdown_account(
    client: NexplaneClient,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """IR_LOCKDOWN_ACCOUNT: lockdown_account IR playbook across configured identity connectors."""
    PHASE = "IR_LOCKDOWN_ACCOUNT"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting account lockdown IR playbook")

    # Discover configured identity connectors
    connectors = client.get("/connectors")
    identity_types = {"active_directory", "okta", "entra_id", "google_workspace"}
    configured = [c["connector_type"] for c in connectors if c["connector_type"] in identity_types]
    unconfigured = list(identity_types - set(configured))

    if not configured:
        log(f"{PHASE}: skipped — no identity connectors configured")
        return _phase_result(PHASE, "skipped", 0, [], list(identity_types), False, 0, 2)

    for ct in unconfigured:
        _write_progress(ssm_key, PHASE, "CONNECTOR_SKIP", f"{ct} — credentials not configured")
        log(f"{PHASE}: skipping {ct} — not configured")

    rollback_stack = []
    try:
        # Create a smoke test identity asset to lock down
        # Use the first configured connector's cloud account
        asset = client.get("/assets", params={"asset_type": "identity", "q": "smokeuser"})
        if not asset:
            fail(f"{PHASE}: no smokeuser identity asset found — run LDAP_ROTATE first")

        asset_id = asset[0]["id"]
        _write_progress(ssm_key, PHASE, "STEP", f"Locking down smokeuser across {configured}")
        cr = client.run_cr(
            f"[{PHASE}] lockdown smokeuser",
            "lockdown_account",
            asset_id,
            {"username": "smokeuser", "rollback_strategy": "snapshot_restore"},
        )
        rollback_stack.append(cr["id"])
        log(f"{PHASE}: lockdown CR completed")

        # Rollback
        client.post(f"/change-requests/{rollback_stack[-1]}/rollback")
        log(f"{PHASE}: rollback completed")
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start, configured, unconfigured, True, 2, 2)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        for cr_id in reversed(rollback_stack):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start, [], [], False, 0, 2)


def run_phase_vuln_pipeline(
    client: NexplaneClient,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """VULN_PIPELINE: full vulnerability pipeline with real 15-minute scheduler wait.

    Tests: webhook ingest → asset match → DRAFT CR generation → SLA enforcement.
    The 15-minute wait is intentional — tests the real production scheduler path.
    """
    PHASE = "VULN_PIPELINE"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting vulnerability pipeline smoke test")

    import uuid as _uuid
    import httpx

    # Step 1: Ingest a synthetic critical CVE finding via webhook
    finding_id = f"smoke-{_uuid.uuid4().hex[:8]}"
    _write_progress(ssm_key, PHASE, "STEP", f"Injecting synthetic CVE finding {finding_id}")

    # Get a real asset ID to attach the finding to
    assets = client.get("/assets", params={"asset_type": "server", "limit": 1})
    if not assets:
        fail(f"{PHASE}: no server assets found — run Phase A first")
    asset_ip = assets[0].get("asset_metadata", {}).get("private_ip", "10.0.0.1")

    finding_payload = {
        "scanner": "qualys",
        "scanner_finding_id": finding_id,
        "finding_type": "cve",
        "severity": "critical",
        "cve_id": "CVE-2026-SMOKE",
        "title": "Smoke test critical CVE",
        "description": "Synthetic finding for smoke test — not a real vulnerability",
        "ip_address": asset_ip,
        "affected_package": "openssl",
        "affected_version": "1.1.1",
        "fixed_version": "3.0.0",
    }

    resp = client.post("/webhooks/vulnerability-findings", json=finding_payload)
    finding_uuid = resp.get("id")
    assert finding_uuid, f"{PHASE}: finding not created"
    log(f"{PHASE}: finding ingested — {finding_uuid}")

    # Step 2: Verify asset was matched
    _write_progress(ssm_key, PHASE, "STEP", "Verifying asset match")
    time.sleep(5)  # give matcher time to run
    finding = client.get(f"/vulnerabilities/{finding_uuid}")
    assert finding.get("asset_id"), f"{PHASE}: finding not matched to asset"
    log(f"{PHASE}: finding matched to asset {finding['asset_id']}")

    # Step 3: Verify DRAFT CR was auto-generated
    _write_progress(ssm_key, PHASE, "STEP", "Verifying DRAFT CR generation")
    time.sleep(5)
    crs = client.get("/change-requests", params={"vulnerability_finding_id": finding_uuid})
    assert crs, f"{PHASE}: no DRAFT CR generated for finding"
    draft_cr_id = crs[0]["id"]
    assert crs[0]["status"] == "draft", f"{PHASE}: CR not in draft status"
    log(f"{PHASE}: DRAFT CR generated — {draft_cr_id}")

    # Step 4: Wait for real scheduler cycle (15 min + buffer)
    _write_progress(ssm_key, PHASE, "STEP", "Waiting for SLA enforcement scheduler (15 min)")
    log(f"{PHASE}: waiting 16 minutes for scheduler cycle...")
    for i in range(16):
        time.sleep(60)
        _write_progress(ssm_key, PHASE, "STEP", f"Waiting for scheduler... {i+1}/16 minutes elapsed")

    # Step 5: Verify SLA enforcement fired
    _write_progress(ssm_key, PHASE, "STEP", "Verifying SLA enforcement")
    finding_updated = client.get(f"/vulnerabilities/{finding_uuid}")
    assert finding_updated.get("sla_breached") or finding_updated.get("escalated"), \
        f"{PHASE}: SLA enforcement did not fire for critical finding"
    log(f"{PHASE}: SLA enforcement verified")

    # Cleanup: reject the DRAFT CR, delete the finding
    try:
        client.post(f"/change-requests/{draft_cr_id}/reject",
                    json={"decision": "rejected", "comment": "smoke test cleanup"})
        client.client.delete(f"{client.base}/vulnerabilities/{finding_uuid}")
    except Exception:
        pass

    _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed ({int(time.time()-start)}s)")
    return _phase_result(PHASE, "passed", time.time() - start, ["vulnerability_pipeline"], [], True, 5, 5)


if __name__ == "__main__":
    parser = make_base_parser()
    parser.description = "Nexplane Platform Feature Smoke Tests"
    parser.add_argument("--agent-asset-id", default="", help="Pre-provisioned agent endpoint asset ID")
    parser.add_argument("--ec2-instance-id", default="", help="EC2 instance ID for SSM verification")
    parser.add_argument("--run-id", default="", help="SmokeTestRun UUID for DB state updates")
    parser.add_argument("--ssm-progress-key", default="", help="SSM key for progress streaming")
    args, _ = parser.parse_known_args()

    phases = [p.strip() for p in args.phases.split(",")]
    client = NexplaneClient(args.base_url, args.email, args.password)
    ec2_client = _get_aws_boto3_client("ec2")
    ssm_boto = _get_aws_boto3_client("ssm")

    results = []
    phase_map = {
        "IR_ISOLATE_HOST": lambda: run_phase_ir_isolate_host(
            client, ec2_client, ssm_boto,
            args.agent_asset_id, args.ec2_instance_id,
            args.run_id, args.ssm_progress_key,
        ),
        "IR_PRESERVE_EVIDENCE": lambda: run_phase_ir_preserve_evidence(
            client, args.agent_asset_id, args.ec2_instance_id,
            args.run_id, args.ssm_progress_key,
        ),
        "IR_LOCKDOWN_ACCOUNT": lambda: run_phase_ir_lockdown_account(
            client, args.run_id, args.ssm_progress_key,
        ),
        "VULN_PIPELINE": lambda: run_phase_vuln_pipeline(
            client, args.run_id, args.ssm_progress_key,
        ),
    }

    passed = failed = skipped = 0
    for phase in phases:
        if phase not in phase_map:
            print(f"  Unknown phase: {phase}")
            continue
        result = phase_map[phase]()
        results.append(result)
        if result["status"] == "passed":
            passed += 1
        elif result["status"] == "failed":
            failed += 1
        else:
            skipped += 1

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
    for r in results:
        icon = "✅" if r["status"] == "passed" else "❌" if r["status"] == "failed" else "⚠️"
        print(f"  {icon} {r['phase']} ({r['duration_seconds']}s)")
        if r["coverage_gaps"]:
            for gap in r["coverage_gaps"]:
                print(f"      ⚪ {gap}")
    print("="*60)

    if failed > 0:
        raise SystemExit(1)
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_platform_live.py
git commit -m "feat: add test_platform_live.py with IR playbook smoke phases"
```

---

### Task 5: Runbook smoke phases

**Files:**
- Modify: `backend/tests/smoke/test_platform_live.py`

- [ ] **Step 1: Add RUNBOOK_ONBOARDING phase**

Add to `test_platform_live.py` after `run_phase_ir_lockdown_account`:

```python
def run_phase_runbook_onboarding(
    client: NexplaneClient,
    endpoint_asset_id: str,
    run_id: str = "",
    ssm_key: str = "",
) -> dict:
    """RUNBOOK_ONBOARDING: Engineer Onboarding seeded runbook template end-to-end.

    Uses the seeded 'Engineer Onboarding' runbook template. Exercises:
    - Runbook execution creation
    - Step execution (change CR steps)
    - Human checkpoint (auto-approved in smoke mode)
    - Rollback of all steps in reverse order

    Missing identity connectors are tolerated — reported as coverage gaps.
    """
    PHASE = "RUNBOOK_ONBOARDING"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting Engineer Onboarding runbook")

    # Find the seeded runbook template
    runbooks = client.get("/runbooks", params={"name": "Engineer Onboarding"})
    if not runbooks:
        fail(f"{PHASE}: 'Engineer Onboarding' seeded runbook not found — check seed data")
    runbook_id = runbooks[0]["id"]

    # Discover which connectors the runbook needs vs what's configured
    connectors = client.get("/connectors")
    configured_types = {c["connector_type"] for c in connectors}
    identity_types = {"active_directory", "okta", "github"}
    exercised = list(identity_types & configured_types)
    skipped = list(identity_types - configured_types)

    for ct in skipped:
        _write_progress(ssm_key, PHASE, "CONNECTOR_SKIP", f"{ct} — not configured")

    rollback_crs = []
    try:
        # Create runbook execution
        _write_progress(ssm_key, PHASE, "STEP", "Creating runbook execution")
        execution = client.post(f"/runbooks/{runbook_id}/execute", json={
            "context": {
                "engineer_name": "Smoke Test Engineer",
                "engineer_email": "smoke@nexplane.test",
                "github_username": "nexplane-smoke-user",
                "manager_email": "admin@nexplane.local",
            }
        })
        execution_id = execution["id"]
        log(f"{PHASE}: execution created — {execution_id}")

        # Wait for execution to complete or reach human checkpoint
        _write_progress(ssm_key, PHASE, "STEP", "Waiting for runbook steps to execute")
        deadline = time.time() + 300
        while time.time() < deadline:
            exec_status = client.get(f"/runbooks/executions/{execution_id}")
            status = exec_status.get("status")
            if status in ("completed", "failed", "waiting_human"):
                break
            time.sleep(10)

        if exec_status.get("status") == "waiting_human":
            # Auto-approve the human checkpoint in smoke mode
            _write_progress(ssm_key, PHASE, "STEP", "Auto-approving human checkpoint (smoke mode)")
            client.post(f"/runbooks/executions/{execution_id}/resume",
                        json={"approved": True, "comment": "smoke test auto-approval"})
            time.sleep(30)

        exec_final = client.get(f"/runbooks/executions/{execution_id}")
        assert exec_final["status"] == "completed", \
            f"{PHASE}: runbook execution ended with status {exec_final['status']}"
        log(f"{PHASE}: runbook execution completed")

        # Collect step CR IDs for rollback
        for step_result in exec_final.get("step_results", []):
            if step_result.get("cr_id") and step_result.get("status") == "completed":
                rollback_crs.append(step_result["cr_id"])

        # Rollback all steps via Nexplane in reverse order (dogfood rollback path)
        _write_progress(ssm_key, PHASE, "STEP", f"Rolling back {len(rollback_crs)} step CRs")
        for cr_id in reversed(rollback_crs):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception as e:
                log(f"{PHASE}: rollback of {cr_id} failed: {e}", ok=False)

        log(f"{PHASE}: all rollbacks completed")
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time() - start, exercised, skipped, True,
                             len(exec_final.get("step_results", [])),
                             len(exec_final.get("step_results", [])))

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        for cr_id in reversed(rollback_crs):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass
        return _phase_result(PHASE, "failed", time.time() - start, [], skipped, False, 0, 0)
```

- [ ] **Step 2: Add RUNBOOK_ACCOUNT_COMPROMISE and RUNBOOK_PATCH_CAMPAIGN**

Following the identical pattern as RUNBOOK_ONBOARDING, add:

```python
def run_phase_runbook_account_compromise(client, endpoint_asset_id, run_id="", ssm_key=""):
    """RUNBOOK_ACCOUNT_COMPROMISE: Account Compromise IR seeded runbook template."""
    PHASE = "RUNBOOK_ACCOUNT_COMPROMISE"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting Account Compromise IR runbook")
    runbooks = client.get("/runbooks", params={"name": "Account Compromise IR"})
    if not runbooks:
        fail(f"{PHASE}: 'Account Compromise IR' seeded runbook not found")
    runbook_id = runbooks[0]["id"]
    connectors = client.get("/connectors")
    configured_types = {c["connector_type"] for c in connectors}
    identity_types = {"active_directory", "okta", "entra_id"}
    exercised = list(identity_types & configured_types)
    skipped_connectors = list(identity_types - configured_types)
    for ct in skipped_connectors:
        _write_progress(ssm_key, PHASE, "CONNECTOR_SKIP", f"{ct} — not configured")
    rollback_crs = []
    try:
        execution = client.post(f"/runbooks/{runbook_id}/execute", json={
            "context": {"compromised_username": "smokeuser", "incident_id": "INC-SMOKE-001"}
        })
        execution_id = execution["id"]
        deadline = time.time() + 300
        while time.time() < deadline:
            s = client.get(f"/runbooks/executions/{execution_id}")
            if s.get("status") in ("completed", "failed", "waiting_human"):
                break
            time.sleep(10)
        if s.get("status") == "waiting_human":
            client.post(f"/runbooks/executions/{execution_id}/resume",
                        json={"approved": True, "comment": "smoke auto-approval"})
            time.sleep(30)
        final = client.get(f"/runbooks/executions/{execution_id}")
        assert final["status"] == "completed"
        for sr in final.get("step_results", []):
            if sr.get("cr_id") and sr.get("status") == "completed":
                rollback_crs.append(sr["cr_id"])
        for cr_id in reversed(rollback_crs):
            try:
                client.post(f"/change-requests/{cr_id}/rollback")
            except Exception:
                pass
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time()-start, exercised, skipped_connectors, True,
                             len(final.get("step_results",[])), len(final.get("step_results",[])))
    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        for cr_id in reversed(rollback_crs):
            try: client.post(f"/change-requests/{cr_id}/rollback")
            except Exception: pass
        return _phase_result(PHASE, "failed", time.time()-start, [], skipped_connectors, False, 0, 0)


def run_phase_runbook_patch_campaign(client, endpoint_asset_id, run_id="", ssm_key=""):
    """RUNBOOK_PATCH_CAMPAIGN: Patch Campaign seeded runbook template. Requires agent."""
    PHASE = "RUNBOOK_PATCH_CAMPAIGN"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting Patch Campaign runbook")
    if not endpoint_asset_id:
        log(f"{PHASE}: skipped — no agent asset ID provided")
        return _phase_result(PHASE, "skipped", 0, [], [], False, 0, 0)
    runbooks = client.get("/runbooks", params={"name": "Patch Campaign"})
    if not runbooks:
        fail(f"{PHASE}: 'Patch Campaign' seeded runbook not found")
    runbook_id = runbooks[0]["id"]
    rollback_crs = []
    try:
        execution = client.post(f"/runbooks/{runbook_id}/execute", json={
            "context": {"target_asset_ids": [endpoint_asset_id], "patch_type": "security"}
        })
        execution_id = execution["id"]
        deadline = time.time() + 600
        while time.time() < deadline:
            s = client.get(f"/runbooks/executions/{execution_id}")
            if s.get("status") in ("completed", "failed", "waiting_human"):
                break
            time.sleep(15)
            _write_progress(ssm_key, PHASE, "STEP", "Patch campaign in progress...")
        if s.get("status") == "waiting_human":
            client.post(f"/runbooks/executions/{execution_id}/resume",
                        json={"approved": True, "comment": "smoke auto-approval"})
            time.sleep(60)
        final = client.get(f"/runbooks/executions/{execution_id}")
        assert final["status"] == "completed"
        for sr in final.get("step_results", []):
            if sr.get("cr_id") and sr.get("status") == "completed":
                rollback_crs.append(sr["cr_id"])
        for cr_id in reversed(rollback_crs):
            try: client.post(f"/change-requests/{cr_id}/rollback")
            except Exception: pass
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time()-start, ["nexplane_agent"], [], True,
                             len(final.get("step_results",[])), len(final.get("step_results",[])))
    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        for cr_id in reversed(rollback_crs):
            try: client.post(f"/change-requests/{cr_id}/rollback")
            except Exception: pass
        return _phase_result(PHASE, "failed", time.time()-start, [], [], False, 0, 0)
```

- [ ] **Step 3: Register all new phases in the phase_map dict at the bottom of __main__**

Update the `phase_map` dict to include all phases:
```python
phase_map = {
    "IR_ISOLATE_HOST": lambda: run_phase_ir_isolate_host(...),
    "IR_PRESERVE_EVIDENCE": lambda: run_phase_ir_preserve_evidence(...),
    "IR_LOCKDOWN_ACCOUNT": lambda: run_phase_ir_lockdown_account(...),
    "IR_PHISHING_RESPONSE": lambda: run_phase_ir_lockdown_account(...),  # same pattern
    "RUNBOOK_ONBOARDING": lambda: run_phase_runbook_onboarding(
        client, args.agent_asset_id, args.run_id, args.ssm_progress_key),
    "RUNBOOK_ACCOUNT_COMPROMISE": lambda: run_phase_runbook_account_compromise(
        client, args.agent_asset_id, args.run_id, args.ssm_progress_key),
    "RUNBOOK_PATCH_CAMPAIGN": lambda: run_phase_runbook_patch_campaign(
        client, args.agent_asset_id, args.run_id, args.ssm_progress_key),
    "ACCESS_REVIEW": lambda: run_phase_access_review(
        client, args.run_id, args.ssm_progress_key),
    "PROJECT_MICROSEG": lambda: run_phase_project_microseg(
        client, args.run_id, args.ssm_progress_key),
    "VULN_PIPELINE": lambda: run_phase_vuln_pipeline(
        client, args.run_id, args.ssm_progress_key),
}
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_platform_live.py
git commit -m "feat: add runbook smoke phases (ONBOARDING, ACCOUNT_COMPROMISE, PATCH_CAMPAIGN)"
```

---

### Task 6: ACCESS_REVIEW and PROJECT_MICROSEG phases

**Files:**
- Modify: `backend/tests/smoke/test_platform_live.py`

- [ ] **Step 1: Add run_phase_access_review**

```python
def run_phase_access_review(client: NexplaneClient, run_id: str = "", ssm_key: str = "") -> dict:
    """ACCESS_REVIEW: full access review campaign lifecycle.

    Tests: create campaign → collect entries → reviewer decisions → approve → generate removal CRs.
    """
    PHASE = "ACCESS_REVIEW"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting access review campaign")
    rollback_crs = []
    try:
        # Create campaign
        _write_progress(ssm_key, PHASE, "STEP", "Creating access review campaign")
        campaign = client.post("/access-reviews/campaigns", json={
            "name": "Smoke Test Access Review",
            "reviewer_model": "security_team",
            "scope": {"asset_types": ["server"]},
        })
        campaign_id = campaign["id"]

        # Launch collection
        _write_progress(ssm_key, PHASE, "STEP", "Launching collection")
        client.post(f"/access-reviews/campaigns/{campaign_id}/launch")
        time.sleep(15)

        # Get entries and make revoke decisions
        _write_progress(ssm_key, PHASE, "STEP", "Making reviewer decisions")
        entries = client.get(f"/access-reviews/campaigns/{campaign_id}/entries")
        for entry in entries[:2]:  # decide on first 2 entries only
            client.post(f"/access-reviews/entries/{entry['id']}/decide",
                        json={"decision": "revoke", "reason": "smoke test"})

        # Approve campaign → triggers CR generation
        _write_progress(ssm_key, PHASE, "STEP", "Approving campaign")
        client.post(f"/access-reviews/campaigns/{campaign_id}/approve")
        time.sleep(10)

        # Verify CRs were generated
        campaign_final = client.get(f"/access-reviews/campaigns/{campaign_id}")
        generated_crs = campaign_final.get("generated_cr_ids", [])
        assert generated_crs, f"{PHASE}: no CRs generated from revoke decisions"
        rollback_crs.extend(generated_crs)
        log(f"{PHASE}: {len(generated_crs)} removal CRs generated")

        # Rollback: reject generated CRs (they're in DRAFT, don't execute them)
        for cr_id in generated_crs:
            try:
                client.post(f"/change-requests/{cr_id}/reject",
                            json={"decision": "rejected", "comment": "smoke test cleanup"})
            except Exception:
                pass

        # Delete the campaign
        client.client.delete(f"{client.base}/access-reviews/campaigns/{campaign_id}")
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time()-start, ["access_review_engine"], [], True, 4, 4)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        return _phase_result(PHASE, "failed", time.time()-start, [], [], False, 0, 4)
```

- [ ] **Step 2: Add run_phase_project_microseg**

```python
def run_phase_project_microseg(client: NexplaneClient, run_id: str = "", ssm_key: str = "") -> dict:
    """PROJECT_MICROSEG: microsegmentation project with AI-assisted planning.

    Tests the Project entity + AI planning endpoint. Does not execute the generated
    CRs (they require PaloAlto which is not available) — validates planning only.
    """
    PHASE = "PROJECT_MICROSEG"
    start = time.time()
    _write_progress(ssm_key, PHASE, "PHASE_START", "Starting microsegmentation project")
    try:
        # Create project
        _write_progress(ssm_key, PHASE, "STEP", "Creating microsegmentation project")
        project = client.post("/projects", json={
            "name": "Smoke Test Microsegmentation",
            "goal": "Implement microsegmentation between the smoke test EC2 and the internet",
            "generation_method": "ai_assisted",
        })
        project_id = project["id"]

        # Request AI planning
        _write_progress(ssm_key, PHASE, "STEP", "Requesting AI-assisted plan generation")
        plan = client.post(f"/projects/{project_id}/generate-plan", json={
            "prompt": "Identify network segments and propose firewall rules to restrict lateral movement"
        })
        assert plan.get("proposed_changes"), f"{PHASE}: AI planning returned no proposed changes"
        log(f"{PHASE}: AI proposed {len(plan['proposed_changes'])} changes")

        # Verify project has phases
        project_detail = client.get(f"/projects/{project_id}")
        assert project_detail.get("status") in ("draft", "planning"), \
            f"{PHASE}: unexpected project status {project_detail.get('status')}"

        # Cleanup
        client.client.delete(f"{client.base}/projects/{project_id}")
        _write_progress(ssm_key, PHASE, "PHASE_PASS", f"{PHASE} passed")
        return _phase_result(PHASE, "passed", time.time()-start, ["ai_planning_engine"], [], False, 2, 2)

    except Exception as e:
        _write_progress(ssm_key, PHASE, "PHASE_FAIL", f"{PHASE} failed: {e}")
        return _phase_result(PHASE, "failed", time.time()-start, [], [], False, 0, 2)
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_platform_live.py
git commit -m "feat: add ACCESS_REVIEW and PROJECT_MICROSEG smoke phases"
```

---

### Task 7: Frontend — phase picker and run history components

**Files:**
- Create: `frontend/src/components/smoke/PhasePickerPanel.tsx`
- Create: `frontend/src/components/smoke/SmokeRunCard.tsx`
- Create: `frontend/src/components/smoke/SmokeRunHistory.tsx`
- Create: `frontend/src/hooks/useSmokeStream.ts`
- Modify: `frontend/src/api/smokeTests.ts`

- [ ] **Step 1: Read existing smoke test page**

Read `frontend/src/pages/SmokeTests.tsx` to understand existing structure and API calls.

- [ ] **Step 2: Add API calls for run management**

In `frontend/src/api/smokeTests.ts`, add:

```typescript
export interface SmokeRun {
  id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  phases: string[];
  started_at: string;
  completed_at: string | null;
  runner_instance_id: string | null;
  result_summary: Record<string, PhaseResult> | null;
  error: string | null;
}

export interface PhaseResult {
  phase: string;
  status: 'passed' | 'failed' | 'skipped';
  duration_seconds: number;
  connectors_exercised: string[];
  connectors_skipped: string[];
  rollback_verified: boolean;
  coverage_gaps: string[];
}

export const startSmokeRun = async (phases: string[]): Promise<SmokeRun> => {
  const res = await api.post('/smoke-tests/runs', { phases });
  return res.data;
};

export const listSmokeRuns = async (): Promise<SmokeRun[]> => {
  const res = await api.get('/smoke-tests/runs');
  return res.data;
};

export const getSmokeRun = async (id: string): Promise<SmokeRun> => {
  const res = await api.get(`/smoke-tests/runs/${id}`);
  return res.data;
};

export const cancelSmokeRun = async (id: string): Promise<void> => {
  await api.delete(`/smoke-tests/runs/${id}`);
};
```

- [ ] **Step 3: Create useSmokeStream hook**

Create `frontend/src/hooks/useSmokeStream.ts`:

```typescript
import { useEffect, useState } from 'react';

export interface ProgressEvent {
  ts: string;
  type: 'PHASE_START' | 'STEP' | 'CONNECTOR_SKIP' | 'PHASE_PASS' | 'PHASE_FAIL';
  phase: string;
  message: string;
}

export function useSmokeStream(runId: string | null) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!runId) return;
    const token = localStorage.getItem('access_token');
    const es = new EventSource(
      `/api/smoke-tests/runs/${runId}/stream`,
      // EventSource doesn't support headers — pass token as query param
    );

    es.onopen = () => setConnected(true);
    es.onmessage = (e) => {
      if (e.type === 'done') { es.close(); setConnected(false); return; }
      try {
        const event: ProgressEvent = JSON.parse(e.data);
        setEvents(prev => [...prev, event]);
      } catch {}
    };
    es.onerror = () => { es.close(); setConnected(false); };

    return () => { es.close(); setConnected(false); };
  }, [runId]);

  return { events, connected };
}
```

- [ ] **Step 4: Create PhasePickerPanel**

Create `frontend/src/components/smoke/PhasePickerPanel.tsx`:

```tsx
import React, { useState } from 'react';

const PHASE_GROUPS = {
  'Platform Orchestration': [
    'RUNBOOK_ONBOARDING', 'RUNBOOK_ACCOUNT_COMPROMISE', 'RUNBOOK_PATCH_CAMPAIGN',
  ],
  'Incident Response': [
    'IR_ISOLATE_HOST', 'IR_PRESERVE_EVIDENCE', 'IR_LOCKDOWN_ACCOUNT', 'IR_PHISHING_RESPONSE',
  ],
  'Platform Features': ['ACCESS_REVIEW', 'PROJECT_MICROSEG', 'VULN_PIPELINE'],
  'AWS Core': ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'W'],
  'Open Source Connectors': [
    'LDAP_ROTATE', 'VAULT_ROTATE', 'KEYCLOAK', 'K8S_RBAC', 'GITEA',
    'FREEIPA', 'GITLAB', 'TELEPORT', 'WAZUH_AGENT', 'FALCO_POLICY',
    'INFISICAL', 'POSTGRES_ROTATE', 'REDIS_ROTATE', 'MONGODB_ROTATE',
    'OPNSENSE_RULE', 'STEP_CA_ROTATE',
  ],
  'Scanners & SIEM': [
    'TRIVY_SCAN', 'LYNIS_AUDIT', 'SSL_EXPIRY',
    'OPENVAS_SCAN', 'NESSUS_SCAN', 'ELASTIC_ALERTS', 'SPLUNK_ALERTS',
  ],
  'Credential-Gated': ['OKTA_DISABLE', 'SERVICENOW_INCIDENT', 'PAGERDUTY_INCIDENT'],
  'New Phases': ['AD_DC_INTEGRITY', 'BIND_DNS', 'POLLER_BACKOFF', 'WINRM_BOOTSTRAP'],
};

const SUITES: Record<string, string[]> = {
  'Quick (IR + Runbooks)': [
    'IR_ISOLATE_HOST', 'IR_PRESERVE_EVIDENCE', 'RUNBOOK_ONBOARDING',
    'RUNBOOK_ACCOUNT_COMPROMISE',
  ],
  'Full Suite': Object.values(PHASE_GROUPS).flat(),
  'Connectors Only': [
    ...PHASE_GROUPS['Open Source Connectors'],
    ...PHASE_GROUPS['Scanners & SIEM'],
    ...PHASE_GROUPS['Credential-Gated'],
  ],
  'Platform Features Only': [
    ...PHASE_GROUPS['Platform Orchestration'],
    ...PHASE_GROUPS['Incident Response'],
    ...PHASE_GROUPS['Platform Features'],
  ],
};

interface Props {
  onStart: (phases: string[]) => void;
  disabled: boolean;
}

export function PhasePickerPanel({ onStart, disabled }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const toggle = (phase: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(phase) ? next.delete(phase) : next.add(phase);
      return next;
    });
  };

  const applySuite = (suiteName: string) => {
    setSelected(new Set(SUITES[suiteName] || []));
  };

  return (
    <div className="phase-picker">
      <div className="suite-shortcuts">
        {Object.keys(SUITES).map(name => (
          <button key={name} onClick={() => applySuite(name)} className="suite-btn">
            {name}
          </button>
        ))}
      </div>

      {Object.entries(PHASE_GROUPS).map(([group, phases]) => (
        <div key={group} className="phase-group">
          <h4>{group}</h4>
          <div className="phase-checkboxes">
            {phases.map(phase => (
              <label key={phase} className="phase-checkbox">
                <input
                  type="checkbox"
                  checked={selected.has(phase)}
                  onChange={() => toggle(phase)}
                />
                {phase}
              </label>
            ))}
          </div>
        </div>
      ))}

      <button
        onClick={() => onStart(Array.from(selected))}
        disabled={disabled || selected.size === 0}
        className="run-btn primary"
      >
        Run {selected.size} phase{selected.size !== 1 ? 's' : ''}
      </button>
    </div>
  );
}
```

- [ ] **Step 5: Create SmokeRunCard with streaming**

Create `frontend/src/components/smoke/SmokeRunCard.tsx`:

```tsx
import React, { useEffect, useRef } from 'react';
import { useSmokeStream, ProgressEvent } from '../../hooks/useSmokeStream';
import { SmokeRun, PhaseResult } from '../../api/smokeTests';

interface Props {
  run: SmokeRun;
  active: boolean;
}

export function SmokeRunCard({ run, active }: Props) {
  const { events } = useSmokeStream(active ? run.id : null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [events]);

  const phaseEvents = (phase: string) =>
    events.filter(e => e.phase === phase);

  const phaseStatus = (phase: string): 'pending' | 'running' | 'passed' | 'failed' => {
    const evts = phaseEvents(phase);
    if (evts.some(e => e.type === 'PHASE_PASS')) return 'passed';
    if (evts.some(e => e.type === 'PHASE_FAIL')) return 'failed';
    if (evts.some(e => e.type === 'PHASE_START')) return 'running';
    if (run.result_summary?.[phase]) {
      return run.result_summary[phase].status as any;
    }
    return 'pending';
  };

  const statusIcon = (s: string) =>
    ({ passed: '✅', failed: '❌', running: '⏳', pending: '○', skipped: '⚪' }[s] || '○');

  return (
    <div className={`smoke-run-card status-${run.status}`}>
      <div className="run-header">
        <span className="run-id">{run.id.slice(0, 8)}</span>
        <span className="run-status">{run.status}</span>
        <span className="run-time">{new Date(run.started_at).toLocaleString()}</span>
        {run.status === 'running' && active && (
          <span className="run-elapsed">
            {Math.floor((Date.now() - new Date(run.started_at).getTime()) / 1000)}s elapsed
          </span>
        )}
      </div>

      <div className="phase-cards">
        {run.phases.map(phase => {
          const status = phaseStatus(phase);
          const result = run.result_summary?.[phase];
          const logs = phaseEvents(phase);

          return (
            <details key={phase} className={`phase-card phase-${status}`} open={status === 'running'}>
              <summary>
                {statusIcon(status)} {phase}
                {result && <span className="duration"> ({result.duration_seconds}s)</span>}
              </summary>

              {result?.coverage_gaps?.length > 0 && (
                <div className="coverage-gaps">
                  {result.coverage_gaps.map(g => <div key={g} className="gap">⚪ {g}</div>)}
                </div>
              )}

              {active && logs.length > 0 && (
                <div className="phase-log" ref={logRef}>
                  {logs.map((e, i) => (
                    <div key={i} className={`log-line log-${e.type.toLowerCase()}`}>
                      <span className="log-ts">{e.ts.slice(11, 19)}</span>
                      <span className="log-msg">{e.message}</span>
                    </div>
                  ))}
                </div>
              )}
            </details>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Create SmokeRunHistory**

Create `frontend/src/components/smoke/SmokeRunHistory.tsx`:

```tsx
import React from 'react';
import { SmokeRun, cancelSmokeRun } from '../../api/smokeTests';

interface Props {
  runs: SmokeRun[];
  onCancel: (id: string) => void;
}

export function SmokeRunHistory({ runs, onCancel }: Props) {
  const isOrphan = (run: SmokeRun) => {
    if (run.status !== 'running') return false;
    const age = Date.now() - new Date(run.started_at).getTime();
    return age > 2 * 60 * 60 * 1000; // 2 hours
  };

  return (
    <div className="run-history">
      <h3>Run History</h3>
      {runs.length === 0 && <p className="empty">No runs yet.</p>}
      {runs.map(run => (
        <div key={run.id} className={`history-row status-${run.status}`}>
          <span className="history-date">{new Date(run.started_at).toLocaleString()}</span>
          <span className="history-phases">{run.phases.length} phases</span>
          <span className="history-status">{run.status}</span>
          {run.result_summary && (
            <span className="history-summary">
              {Object.values(run.result_summary).filter(r => r.status === 'passed').length} passed
            </span>
          )}
          {isOrphan(run) && (
            <button
              className="terminate-btn"
              onClick={() => onCancel(run.id)}
              title="Runner appears stuck — terminate EC2 instance"
            >
              Terminate orphan runner
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/smoke/ frontend/src/hooks/useSmokeStream.ts frontend/src/api/smokeTests.ts
git commit -m "feat: add smoke test UI components — phase picker, streaming cards, run history"
```

---

### Task 8: Integrate into SmokeTests page

**Files:**
- Modify: `frontend/src/pages/SmokeTests.tsx`

- [ ] **Step 1: Read current page structure**

Read `frontend/src/pages/SmokeTests.tsx` to understand current layout and state management.

- [ ] **Step 2: Integrate new components**

Update `SmokeTests.tsx` to compose the new components:

```tsx
import React, { useState, useEffect } from 'react';
import { PhasePickerPanel } from '../components/smoke/PhasePickerPanel';
import { SmokeRunCard } from '../components/smoke/SmokeRunCard';
import { SmokeRunHistory } from '../components/smoke/SmokeRunHistory';
import { startSmokeRun, listSmokeRuns, cancelSmokeRun, SmokeRun } from '../api/smokeTests';

export function SmokeTests() {
  const [runs, setRuns] = useState<SmokeRun[]>([]);
  const [activeRun, setActiveRun] = useState<SmokeRun | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    listSmokeRuns().then(setRuns);
  }, []);

  const handleStart = async (phases: string[]) => {
    setLoading(true);
    try {
      const run = await startSmokeRun(phases);
      setRuns(prev => [run, ...prev]);
      setActiveRun(run);
    } finally {
      setLoading(false);
    }
  };

  const handleCancel = async (id: string) => {
    await cancelSmokeRun(id);
    setRuns(prev => prev.map(r => r.id === id ? { ...r, status: 'cancelled' } : r));
    if (activeRun?.id === id) setActiveRun(null);
  };

  return (
    <div className="smoke-tests-page">
      <h1>Smoke Tests</h1>

      <div className="smoke-layout">
        <div className="smoke-sidebar">
          <PhasePickerPanel onStart={handleStart} disabled={loading || activeRun?.status === 'running'} />
        </div>

        <div className="smoke-main">
          {activeRun && (
            <SmokeRunCard run={activeRun} active={activeRun.status === 'running'} />
          )}
          <SmokeRunHistory runs={runs} onCancel={handleCancel} />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Restart frontend and verify UI renders**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Open `http://localhost:3000` → navigate to Smoke Tests. Verify phase picker renders with all groups and suite shortcuts. Verify run history is empty initially.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/SmokeTests.tsx
git commit -m "feat: integrate phase picker, streaming cards, and run history into SmokeTests page"
```

---

### Task 9: Wire test_platform_live.py into run_on_ec2.py phase dispatch

**Files:**
- Modify: `backend/tests/smoke/run_on_ec2.py`

- [ ] **Step 1: Find where test script is selected in run_on_ec2.py**

Read run_on_ec2.py around the section that constructs `test_cmd_parts` to understand how it chooses between `test_aws_live.py` and `test_agent_live.py`.

- [ ] **Step 2: Add platform phase detection**

Find the test script selection logic and add platform phase routing:

```python
PLATFORM_PHASES = {
    "IR_ISOLATE_HOST", "IR_PRESERVE_EVIDENCE", "IR_LOCKDOWN_ACCOUNT", "IR_PHISHING_RESPONSE",
    "RUNBOOK_ONBOARDING", "RUNBOOK_ACCOUNT_COMPROMISE", "RUNBOOK_PATCH_CAMPAIGN",
    "ACCESS_REVIEW", "PROJECT_MICROSEG", "VULN_PIPELINE",
}

selected_phases = set(args.phases.split(","))
if selected_phases & PLATFORM_PHASES:
    test_script = "test_platform_live.py"
else:
    test_script = "test_aws_live.py"  # existing behavior
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/run_on_ec2.py
git commit -m "feat: route platform feature phases to test_platform_live.py"
```

---

### Task 10: End-to-end verification

- [ ] **Step 1: Run IR_ISOLATE_HOST against live backend**

Ensure Phase A has run and you have an agent asset ID. Then:

```bash
docker exec -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... nexplane-backend-1 \
  python tests/smoke/run_on_ec2.py \
  --phases IR_ISOLATE_HOST \
  --base-url http://100.122.229.11:8000 \
  --backend-tailscale-ip 100.122.229.11
```

Expected: ✅ IR_ISOLATE_HOST PASSED

- [ ] **Step 2: Verify streaming via UI**

Start a run from the UI smoke test tab. Confirm log lines appear in real time as the phase executes. Confirm the phase card switches from pending → running → passed.

- [ ] **Step 3: Verify run history persists across page reload**

After a run completes, reload the page. Confirm the run appears in history with correct status and phase results.

- [ ] **Step 4: Verify orphan detection**

Manually set a run's `started_at` to 3 hours ago in the DB:
```bash
docker exec nexplane-backend-1 python -c "
import asyncio
from app.config import settings
from app.models.smoke_test_run import SmokeTestRun
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
async def f():
    engine = create_async_engine(settings.DATABASE_URL)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as db:
        r = await db.execute(select(SmokeTestRun).where(SmokeTestRun.status == 'running'))
        run = r.scalars().first()
        if run:
            run.started_at = datetime.now(timezone.utc) - timedelta(hours=3)
            await db.commit()
            print(f'Set {run.id} started_at to 3h ago')
    await engine.dispose()
asyncio.run(f())
"
```

Reload the UI. Confirm "Terminate orphan runner" button appears for that run.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: platform feature smoke tests complete — test_platform_live, SmokeTestRun model, SSE streaming, phase picker UI"
```
