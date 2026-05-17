# Platform Feature Smoke Tests — Design Spec

**Date:** 2026-05-17  
**Status:** Approved  
**Scope:** End-to-end smoke test coverage for platform orchestration features (runbooks, IR playbooks, access reviews, projects, vulnerability pipeline) plus a persistent smoke test run model, streaming UI progress, and selective phase execution from the smoke test tab.

---

## Background

The Nexplane platform has ~34% API endpoint coverage in smoke tests and zero coverage for its five core differentiators: runbooks, IR playbooks, access reviews, projects, and the vulnerability pipeline. These features have been implemented and seeded with reference scenarios, but their rollback paths have never been verified against real infrastructure. Per the platform's core principle, an untested rollback is an unverified promise.

This spec defines a new test file (`test_platform_live.py`), a persistent run state model (`SmokeTestRun`), streaming progress via SSE, and UI updates to the smoke test tab — all designed so operators can run and monitor tests without CLI access.

---

## Core Principles (must be preserved in implementation)

**Dogfooding:** All changes in smoke tests go through the Nexplane CR lifecycle (create → plan → approve → execute → rollback). Direct SDK or executor calls are never used to make changes — only to verify side effects independently.

**SSM role:** SSM is used exclusively as a side-effect verification layer (shell into the host and confirm the change happened) and for streaming progress writes. SSM never makes changes. This distinction must be preserved — future implementers must not use SSM to execute changes even as a "shortcut."

**Live infrastructure:** All tests run against real infrastructure. Mock paths are not exercised.

**Graceful degradation:** Platform feature phases tolerate missing connectors. Unconfigured connectors are skipped and reported as coverage gaps, not failures. Configured connectors must pass including rollback verification.

**IR expedited path:** IR playbook phases use `ir_auto_approve: true` — the expedited path is the production behavior for IR. Testing a slow approval gate on an IR playbook would validate the wrong behavior.

---

## Design

### 1. New Test File: `test_platform_live.py`

Location: `backend/tests/smoke/test_platform_live.py`

Follows the same pattern as `test_aws_live.py` — phase functions, `NexplaneClient`, rollback stack in finally blocks. Accepts pre-provisioned infrastructure via CLI args so it can run against any environment without re-provisioning EC2.

**CLI args:**
```
--base-url              Nexplane backend URL (required)
--email                 Nexplane user email
--password              Nexplane user password  
--phases                Comma-separated phase names
--agent-asset-id        Pre-provisioned agent endpoint asset ID (from Phase A)
--ec2-instance-id       EC2 instance ID with agent installed (for SSM verification)
--ldap-host             OpenLDAP host IP (from LDAP_ROTATE, for runbook tests)
--backend-tailscale-ip  Backend Tailscale IP (for POLLER_BACKOFF)
```

**Phases:**

| Phase | Description | Prerequisites | Duration |
|---|---|---|---|
| `RUNBOOK_ONBOARDING` | Engineer Onboarding seeded template end-to-end | Agent, identity connectors (degradable) | ~5 min |
| `RUNBOOK_ACCOUNT_COMPROMISE` | Account Compromise IR seeded template | Agent, identity connectors (degradable) | ~5 min |
| `RUNBOOK_PATCH_CAMPAIGN` | Patch Campaign seeded template | Agent required | ~10 min |
| `IR_ISOLATE_HOST` | isolate_host IR playbook, expedited path | Agent required | ~2 min |
| `IR_PRESERVE_EVIDENCE` | preserve_evidence IR playbook | Agent required, S3 bucket | ~3 min |
| `IR_LOCKDOWN_ACCOUNT` | lockdown_account IR playbook | Identity connector (degradable) | ~2 min |
| `IR_PHISHING_RESPONSE` | phishing_response IR playbook | Identity connectors (degradable) | ~3 min |
| `ACCESS_REVIEW` | Full campaign lifecycle: create → collect → review → approve → generate CRs | Assets + users in DB | ~5 min |
| `PROJECT_MICROSEG` | Microsegmentation project with AI planning | AI connector configured | ~5 min |
| `VULN_PIPELINE` | Webhook ingest → asset match → DRAFT CR → 15min scheduler wait → SLA enforcement | Running backend scheduler | ~20 min |

**Phase result structure:**
```python
{
    "phase": "RUNBOOK_ONBOARDING",
    "status": "passed" | "failed" | "skipped",
    "duration_seconds": 142,
    "connectors_exercised": ["active_directory", "github"],
    "connectors_skipped": ["okta", "entra_id"],  # not configured
    "steps_completed": 4,
    "steps_total": 4,
    "rollback_verified": True,
    "coverage_gaps": ["okta — credentials not configured in platform"]
}
```

**Coverage gap reporting:** At the end of each phase, the result includes which connectors were skipped and why. The phase passes if all configured connectors complete + rollback, and all skipped connectors are documented.

### 2. Persistent Run State: `SmokeTestRun` Model

**New file:** `backend/app/models/smoke_test_run.py`

```python
class SmokeTestRun(Base):
    __tablename__ = "smoke_test_runs"

    id: Mapped[UUID]              # run ID — used for SSM key + SSE subscription
    organization_id: Mapped[UUID]
    created_by: Mapped[UUID]
    started_at: Mapped[datetime]
    completed_at: Mapped[Optional[datetime]]
    status: Mapped[str]           # running | completed | failed | cancelled
    phases: Mapped[list[str]]     # phases selected for this run (JSONB)
    runner_instance_id: Mapped[Optional[str]]  # EC2 runner — any session can terminate it
    progress_ssm_key: Mapped[Optional[str]]    # SSM param path for live output
    result_summary: Mapped[Optional[dict]]     # per-phase results after completion (JSONB)
    error: Mapped[Optional[str]]
```

**API endpoints (new router: `backend/app/routers/smoke_test_runs.py`):**
- `POST /smoke-tests/runs` — start a new run, returns run ID
- `GET /smoke-tests/runs` — list runs (most recent first, limit 20)
- `GET /smoke-tests/runs/{id}` — get run details + result summary
- `GET /smoke-tests/runs/{id}/stream` — SSE stream of live progress events
- `DELETE /smoke-tests/runs/{id}` — cancel a running run (terminates EC2 runner)

**SSM progress key format:** `/nexplane/smoke-runs/{run_id}/progress` — JSON array of progress events, appended by runner, deleted on run completion or after 24h.

**Alembic migration:** New table `smoke_test_runs`.

### 3. Streaming Progress

**Runner side (`run_on_ec2.py`):** When `--run-id` is passed, the runner writes structured progress events to SSM after each step:

```python
# Event format written to SSM
{
    "ts": "2026-05-17T11:23:45Z",
    "type": "PHASE_START" | "STEP" | "CONNECTOR_SKIP" | "PHASE_PASS" | "PHASE_FAIL",
    "phase": "RUNBOOK_ONBOARDING",
    "message": "Creating Engineer Onboarding runbook execution",
    "data": {}  # optional structured data
}
```

**Backend side:** `GET /smoke-tests/runs/{id}/stream` is an SSE endpoint. It polls the SSM progress key every 2 seconds and yields new events as they appear. Closes when the run reaches terminal status.

**Frontend side:** The smoke test tab subscribes to the SSE stream using `EventSource`. Each event appends a log line to the active phase card. Phase cards switch between `pending → running → passed/failed` as events arrive.

### 4. UI — Smoke Test Tab Updates

**Phase picker:** Grouped checkboxes organized by category (Platform Orchestration, Incident Response, Platform Features, AWS Core, Open Source Connectors, Scanners/SIEM, Credential-Gated). Predefined suite shortcuts: "Quick" (IR + Runbooks), "Full Suite", "Connectors Only", "New Phases Only".

**Active run view:** Per-phase streaming log cards. Timer showing elapsed time on long-running phases (VULN_PIPELINE shows "15:23 — waiting for scheduler cycle"). Coverage report on platform feature phases listing exercised vs. skipped connectors.

**Run history:** List of past runs showing date, phases, duration, pass/fail counts. Persists across sessions via the `SmokeTestRun` model. Any session can see what was last run and when.

**Orphan runner recovery:** If a run shows status `running` but `started_at` is >2 hours ago, the UI shows a "Terminate orphan runner" button that calls `DELETE /smoke-tests/runs/{id}`.

### 5. Integration with Existing Runner

**`run_on_ec2.py` changes:**
- `--run-id` arg: optional UUID, enables streaming mode
- `--tailscale-auth-key` auto-fetched from platform DB via `sys.path.insert(0, "/app")` + SecretsService (already implemented)
- `SmokeTestRun` record created before EC2 launch, `runner_instance_id` updated after launch, `status` updated on completion

**Phase dependency injection:** `run_on_ec2.py` passes `--agent-asset-id` and `--ec2-instance-id` to `test_platform_live.py` when Phase A has already run (these are stored in the `SmokeTestRun.result_summary` from the prior run or passed explicitly via UI).

---

## What This Does NOT Cover

- Spec 2: Executor stub audit (27 Python executors that may still return mock data)
- Spec 3: Connector coverage expansion (46 zero-coverage connectors)
- macOS smoke infrastructure (EC2 Mac dedicated host — separate cost decision)
- Scheduled smoke runs (cron-triggered, future work)
