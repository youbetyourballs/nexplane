# Project-Level Rollback Design

**Date:** 2026-05-31
**Status:** Approved

---

## Goal

Add coordinated rollback across all CRs in a project, executed in reverse order, with pause/resume resilience, user prompting on failures, and reconstitution support for permanent operations.

---

## Background

Per-CR rollback already exists (`POST /change-requests/{id}/rollback`). Projects have sequenced CRs with `depends_on` relationships. What's missing is an orchestrator that drives all CR rollbacks in reverse execution order, persists progress so a crashed backend can resume, and handles permanent CRs (no clean undo) by detecting a pre-capture backup CR and using its result for reconstitution.

---

## Not In Scope

- Automatic insertion of backup CRs into projects (future AI planner concern)
- Rollback of individual project phases independently
- Rollback scheduling (time-based auto-rollback)
- Changes to per-CR rollback logic

---

## Architecture

A new `ProjectRollback` record tracks a rollback run. A `ProjectRollbackStep` record tracks each CR's rollback individually. At backend startup, `project_rollback_service.resume_interrupted()` queries for any rollback with `status = running` and re-dispatches the background task from `current_step`.

The rollback engine walks `ProjectChangeRequest` members in **reverse `sequence_order`**, with inverted `depends_on` enforcement: if CR B depended on CR A during execution, CR A's rollback waits until CR B's rollback is `completed` or `skipped`.

For each CR the engine selects one of three paths:
- **Standard** — calls the existing `_do_rollback()` path
- **Reconstitution** — permanent CR with a detectable pre-capture backup CR; passes the backup's `execution_result` as the reconstitution payload
- **Permanent / no backup** — pauses and sets the step to `awaiting_user`; operator skips, retries, or marks manually handled

Auto-triggers (execution failure, soak health check failure) create a rollback in `awaiting_user` state and surface a notification before any rollback begins. The user approves, adjusts scope, or dismisses.

---

## Data Model

### `project_rollbacks` (new table)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `project_id` | UUID FK → projects | |
| `status` | enum | `pending`, `running`, `paused`, `awaiting_user`, `completed`, `failed` |
| `trigger` | enum | `manual`, `execution_failure`, `soak_health_check` |
| `triggered_by_user_id` | UUID FK → users, nullable | null for auto-triggers |
| `triggered_by_cr_id` | UUID FK → change_requests, nullable | the CR that failed (auto-trigger only) |
| `current_step` | int | index of step currently being processed; used for resume |
| `notes` | str, nullable | user-provided reason or system prompt message |
| `created_at` | datetime | |
| `started_at` | datetime, nullable | |
| `paused_at` | datetime, nullable | |
| `completed_at` | datetime, nullable | |

### `project_rollback_steps` (new table)

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `project_rollback_id` | UUID FK → project_rollbacks | |
| `change_request_id` | UUID FK → change_requests | |
| `sequence_order` | int | reverse of original execution order (1 = first to roll back) |
| `status` | enum | `pending`, `running`, `skipped`, `completed`, `failed`, `awaiting_user` |
| `rollback_kind` | enum | `standard`, `reconstitution`, `permanent_no_backup` |
| `backup_cr_id` | UUID FK → change_requests, nullable | pre-capture CR whose execution_result feeds reconstitution |
| `started_at` | datetime, nullable | |
| `completed_at` | datetime, nullable | |
| `result` | JSON, nullable | rollback outcome or skip/decision reason |

**Migration:** two new tables, no changes to existing models.

**Project status addition:** add `rolling_back` to `ProjectStatus` enum.

---

## Reconstitution

When the engine encounters a permanent CR (`rollback_type == "permanent"` in the manifest), it searches backwards through the project's `completed` CRs for one matching a known pre-capture pattern targeting the same asset:

```python
RECONSTITUTION_PAIRS = {
    "rotate_iam_key":                 ["create_backup", "capture_instance_state"],
    "rotate_ssh_keys":                ["create_backup"],
    "gcp_rotate_service_account_key": ["create_backup"],
    "azure_rotate_storage_key":       ["create_backup"],
    "rds_instance_delete":            ["rds_snapshot_create"],
    "ec2_terminate":                  ["snapshot_asset", "capture_instance_state"],
    "iam_user_delete":                ["capture_instance_state"],
}
```

If a match is found (same target asset, ran before the permanent CR, status `completed`), its `execution_result` is passed as the reconstitution payload and `backup_cr_id` is set on the step.

If no match is found, the step is set to `rollback_kind=permanent_no_backup`, `status=awaiting_user`, and the rollback pauses with the message: _"No backup found for `{change_type}` on `{asset}` — skip, retry, or mark as manually handled."_

**Preflight warning:** `POST /projects/{id}/rollback` runs a preflight scan before creating the rollback. It returns a `warnings` list identifying permanent CRs with no detectable backup predecessor, so operators know upfront what will require manual intervention.

---

## API Endpoints

All added to `backend/app/routers/projects.py`.

### `POST /projects/{id}/rollback`

Request body:
```json
{
  "notes": "optional reason",
  "cr_ids": ["uuid", "uuid"] | null
}
```
`cr_ids: null` rolls back all eligible CRs. Pass a subset to exclude specific ones.

Response:
```json
{
  "rollback": { ...ProjectRollbackRead },
  "warnings": ["No backup found for rotate_iam_key on asset X"]
}
```

Creates `ProjectRollback` + steps, sets project status to `rolling_back`, fires background task. Returns immediately.

### `GET /projects/{id}/rollback`

Returns current `ProjectRollback` with nested `steps[]`. Frontend polls this for live progress.

### `POST /projects/{id}/rollback/pause`

Sets rollback status to `paused`. Background task checks flag after each step and exits cleanly.

### `POST /projects/{id}/rollback/resume`

Sets status back to `running`, re-dispatches background task from `current_step`.

### `POST /projects/{id}/rollback/steps/{step_id}/decision`

Request body:
```json
{ "action": "skip" | "retry" | "mark_done" }
```
Used when a step is `awaiting_user`. `mark_done` records that the operator handled it manually outside the platform.

---

## Auto-Trigger Hook

`project_rollback_service.on_cr_failed(project_id, cr_id)` is called from the existing CR execution failure path (in `cr_workflow_service.py` or `change_requests.py` where CR status is set to `failed`).

It creates a `ProjectRollback` with `status=awaiting_user`, `trigger=execution_failure`, `triggered_by_cr_id=cr_id`. No rollback steps execute until the user approves via `POST /projects/{id}/rollback/resume` or the confirmation UI action.

Same hook pattern applies for soak health check failure: `on_soak_failed(project_id, phase_id)` — to be wired when soak health checks are implemented.

---

## Startup Resume

In `backend/app/main.py` lifespan handler, after existing startup tasks:

```python
from app.services.project_rollback_service import resume_interrupted
await resume_interrupted(db)
```

`resume_interrupted()` queries for all `ProjectRollback` records with `status = running`, and for each re-dispatches `asyncio.ensure_future(_run_rollback(rollback_id))`. The background task re-reads `current_step` from the database and skips already-completed steps.

---

## Frontend

### Project detail page

- **"Roll Back Project" button** — visible when project status is `completed` or `in_progress`. Opens a confirmation drawer.
- **Confirmation drawer** — shows the rollback plan in reverse order. Each CR row shows: name, change_type, rollback_kind badge (`standard` / `reconstitution` / `permanent — no backup`). User can deselect CRs before confirming. Permanent-no-backup rows are highlighted with a warning.
- **Progress view** — after confirming, the drawer becomes a live progress view. Each step shows status badge. Pause / Resume buttons. Steps in `awaiting_user` show a decision panel (Skip / Retry / Mark Done).

### Auto-trigger notification

When a rollback is created with `status=awaiting_user` (execution failure), a banner appears on the project detail page:

> _"CR `{title}` failed. Roll back the project?"_ — **Review & Roll Back** / **Dismiss**

Clicking "Review & Roll Back" opens the confirmation drawer pre-populated with the failure context.

---

## File Map

| File | Status | Change |
|------|--------|--------|
| `backend/app/models/project.py` | Modify | Add `rolling_back` to `ProjectStatus` enum |
| `backend/app/models/project_rollback.py` | Create | `ProjectRollback` and `ProjectRollbackStep` models |
| `backend/alembic/versions/XXX_project_rollback.py` | Create | Migration for two new tables + enum value |
| `backend/app/schemas/project_rollback.py` | Create | Pydantic schemas: `ProjectRollbackRead`, `ProjectRollbackStepRead`, `RollbackInitRequest`, `StepDecisionRequest` |
| `backend/app/services/project_rollback_service.py` | Create | Orchestration: `initiate()`, `_run_rollback()`, `pause()`, `resume()`, `step_decision()`, `on_cr_failed()`, `resume_interrupted()`, `RECONSTITUTION_PAIRS` |
| `backend/app/routers/projects.py` | Modify | Add 5 new endpoints |
| `backend/app/main.py` | Modify | Call `resume_interrupted()` in lifespan |
| `backend/app/routers/change_requests.py` | Modify | Call `on_cr_failed()` when CR transitions to `failed` |
| `backend/tests/unit/test_project_rollback_service.py` | Create | Unit tests for orchestration logic |
| `backend/tests/smoke/test_aws_live.py` | Modify | Add `PROJECT_ROLLBACK` smoke phase |
| `frontend/src/pages/ProjectDetail.tsx` (or equivalent) | Modify | Roll Back button, confirmation drawer, progress view, auto-trigger banner |

---

## Testing

### Unit tests — `backend/tests/unit/test_project_rollback_service.py`

- `test_reverse_order` — steps are created in reverse `sequence_order`
- `test_inverted_depends_on` — CR A rollback waits until CR B rollback is complete when B depended on A
- `test_reconstitution_found` — permanent CR with matching backup CR gets `rollback_kind=reconstitution`, `backup_cr_id` set
- `test_reconstitution_not_found` — permanent CR with no backup gets `rollback_kind=permanent_no_backup`, step set to `awaiting_user`
- `test_preflight_warnings` — preflight returns warning for each permanent CR without a backup predecessor
- `test_pause_resume` — background task exits after pause, resumes from `current_step`
- `test_resume_interrupted` — `resume_interrupted()` re-dispatches rollbacks in `running` state
- `test_on_cr_failed_creates_awaiting_user` — auto-trigger creates rollback without starting steps

### Smoke phase — `PROJECT_ROLLBACK`

1. Create a project with two CRs: `create_backup` → `rotate_iam_key` (permanent, has backup predecessor)
2. Execute both CRs via the platform
3. Initiate project rollback via `POST /projects/{id}/rollback`
4. Assert rollback created, `rotate_iam_key` step has `rollback_kind=reconstitution`
5. Poll until rollback `status=completed`
6. Assert both steps are `completed` or `skipped`
7. Assert project status returns to `in_progress` or `completed`
8. Cleanup
