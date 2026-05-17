# Runbook Execution Engine Design

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task.

**Goal:** Wire the existing runbook executor so that triggered runbook executions actually advance step-by-step, auto-executing CRs without per-step human approval, while giving admins a UI toggle to control which runbooks may auto-execute.

**Architecture:** Three independent fixes that together close the gap: (1) scheduler wiring to call the existing tick function every 30 s, (2) CR auto-drive in the bridge (plan → auto-approve → execute inline), (3) `auto_execute` governance flag with a maintenance-window pre-check on trigger. The runbook executor logic (`runbook_executor.py`) is already correct and requires no changes.

**Tech Stack:** FastAPI/SQLAlchemy async, APScheduler, React/TanStack Query, existing `generate_plan` + `start_workflow` internals.

---

## Section 1 — `auto_execute` governance flag

### Model
Add `auto_execute: bool = False` to the `Runbook` SQLAlchemy model. Seeded templates have `auto_execute=True` set at seed time so they work out of the box.

### Schema
Add `auto_execute: bool = False` to `RunbookCreate`, `RunbookUpdate`, and `RunbookOut`. No other schema changes needed.

### Alembic migration
One `ADD COLUMN auto_execute BOOLEAN NOT NULL DEFAULT FALSE` migration. Update the seed upsert to set `auto_execute=True` for all seed templates.

### Trigger gate
In `RunbookService.trigger_runbook`: if `rb.auto_execute is False`, raise `HTTPException(403, "Runbook is not enabled for auto-execution. An admin must enable it first.")`.

### Admin toggle — backend
The existing `PATCH /api/runbooks/{id}` endpoint already accepts `RunbookUpdate`. Adding `auto_execute` to `RunbookUpdate` is sufficient. No new endpoint needed. The PATCH endpoint currently has no role guard — add `Depends(require_roles(UserRole.admin))` so only admins can call it. All other runbook routes (GET, fork, trigger) remain open to any authenticated user.

### Admin toggle — frontend
In `Runbooks.tsx`, for each row:
- Render a toggle switch (styled like existing Settings toggles) visible only when `user?.role === "admin"`. Non-admins see a read-only "Enabled" / "Disabled" badge.
- Clicking the toggle fires `PATCH /api/runbooks/{id}` with `{ auto_execute: !rb.auto_execute }` via a new `useUpdateRunbook` mutation in `useRunbooks.ts`.
- The "Run" button is disabled (greyed, tooltip: *"Runbook must be enabled by an admin before it can be triggered"*) when `rb.auto_execute === false`.

---

## Section 2 — CR auto-execution in the bridge

### Maintenance window pre-check on trigger

`trigger_runbook` accepts an optional `force: bool = False` in a new `TriggerRunbookRequest` field (existing schema already has `context: dict` — add `force: bool = False`).

Before creating the `RunbookExecution`:
1. Collect asset IDs from all step `asset_selector` fields in the runbook snapshot.
2. For each asset, call `is_in_maintenance_window(db, asset.tags, enforcement="hard")`.
3. If any hard window is active **and** `force is False` → return HTTP 409:
   ```json
   { "maintenance_window": "<name>", "warning": "Active change freeze window. Set force=true to override as emergency." }
   ```
4. If `force is True`, proceed and write an audit event `runbook.emergency_override` capturing the triggering user, execution id, and window name.

Frontend `TriggerButton` catches the 409, extracts `maintenance_window` and `warning` from the response body, and shows a confirmation modal:

> ⚠️ **Change window active: [name]**
> This runbook will affect assets under a change freeze. Only proceed if this is an emergency.
> [Cancel] [Confirm emergency override]

Confirming re-triggers with `{ force: true }`.

### Bridge auto-drive

Extract the plan generation logic currently inlined in `generate_change_plan` (router, ~40 lines) into a shared helper, and update the router to call it:

```python
# app/services/change_plan_service.py
async def plan_cr(db: AsyncSession, cr: ChangeRequest) -> ChangePlan:
    """Plan a CR and set its status to 'planned'. Used by router and runbook bridge."""
```

It contains the same `score_change_request` → `generate_plan` → `db.flush` sequence from the router, minus the HTTP-specific parts (no HTTPException for blocked CRs — raises `ValueError` instead so the bridge can log and mark the step failed).

In `runbook_cr_bridge.py`, after `db.add(cr); await db.flush()`:

```python
# 1. Plan
await plan_cr(db, cr)

# 2. Auto-approve (no Approval record — runbook trigger is the approval)
cr.status = ChangeRequestStatus.approved
cr.updated_at = datetime.now(timezone.utc)
await db.flush()

# 3. Execute (background task — returns immediately)
run = ExecutionRun(change_request_id=cr.id, workflow_id=f"wf-rb-{cr.id}-1", status=ExecutionStatus.pending)
db.add(run)
await db.flush()
wf_input = WorkflowInput(change_request_id=str(cr.id), organization_id=str(cr.organization_id), initiator_id=str(execution.triggered_by))
asyncio.create_task(start_workflow(execute_change_workflow, wf_input, workflow_id=run.workflow_id))
```

**No maintenance window check in the bridge.** The trigger endpoint is the gate; once the execution record exists, all steps auto-drive without re-checking windows. This matches operator intent: they confirmed the emergency at trigger time.

**plan_cr error handling:** If `plan_cr` raises (e.g. safety scorer blocks the plan), the bridge catches it, marks the step result as `failed` with the error message, and calls `_handle_step_failure`. The execution fails gracefully rather than leaving a stuck draft CR.

---

## Section 3 — Scheduler wiring

In `main.py`, add to the `_escalation_scheduler` setup:

```python
from app.services.runbook_executor import tick_all_executions as _tick_runbooks

_escalation_scheduler.add_job(
    lambda: _tick_runbooks(AsyncSessionLocal),
    "interval",
    seconds=30,
    id="runbook_executor_tick",
    replace_existing=True,
)
```

Interval: 30 s (matches the docstring intent in `runbook_executor.py`). No new scheduler or infrastructure — one additional job on the existing `AsyncIOScheduler`.

---

## Error handling

| Scenario | Behaviour |
|---|---|
| `auto_execute=False` and user hits Run | 403 from trigger endpoint; frontend shows toast |
| Hard maintenance window active, no `force` | 409 with `maintenance_window` key; frontend shows modal |
| `plan_cr` raises (safety blocked) | Step result marked failed; execution status = failed |
| `change_type` not in ChangeType enum | `ValueError` in bridge; step result marked failed |
| Workflow task crashes mid-execution | CR stays non-completed; next tick sees it, marks step failed |
| Human checkpoint step | Execution status set to `waiting_human`; `/resume` endpoint already implemented |

---

## What is NOT in scope

- Fixing seed template change types (`create_ad_account`, `assign_okta_groups`, etc.) that don't exist in the ChangeType enum — those require connector work and are a separate task.
- `rollback_all` on step failure — the executor already logs a warning and marks `rolled_back`; full rollback CR chaining is deferred.
- Parallel group step rollback.
- Runbook execution history UI (the existing `/runbooks/executions/{id}` page already exists).
