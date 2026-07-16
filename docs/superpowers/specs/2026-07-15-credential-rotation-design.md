# Credential Rotation with Fan-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `credential_rotation` CR type that orchestrates serial multi-step credential rotation campaigns with pause/retry/skip on failure and FILO rollback.

**Architecture:** A new `change_type: credential_rotation` drives a dedicated backend execution workflow. Each step in the campaign calls an existing executor's `execute()` directly (no sub-CRs). On step failure the CR enters `paused` status; the operator can retry, skip, or trigger rollback. Rollback unwinds completed steps in reverse order using each executor's `rollback()`.

**Tech Stack:** Python/FastAPI backend, existing executor pattern, existing CR state machine, PostgreSQL for CR storage.

## Global Constraints

- All new Python files must NOT use `from __future__ import annotations`
- All functionality must be tested against live infrastructure — no mocks
- Every CR must pass through awaiting_approval before execution
- Rollback guarantee: completed steps unwind FILO; irreversible steps surface as warnings, not failures
- Smoke test must exercise full CR lifecycle: create → plan → approve → execute → rollback

---

## Section 1: CR Structure

The `credential_rotation` CR uses the existing `desired_outcome` field with a `steps` array:

```json
{
  "change_type": "credential_rotation",
  "title": "Rotate deploy-bot credentials",
  "desired_outcome": {
    "steps": [
      {
        "connector_type": "aws",
        "action_id": "rotate_iam_key",
        "params": { "username": "deploy-bot" },
        "label": "Rotate IAM key for deploy-bot"
      },
      {
        "connector_type": "aws",
        "action_id": "rotate_secrets_manager_secret",
        "params": { "secret_id": "deploy-bot-key" },
        "label": "Update Secrets Manager copy"
      }
    ]
  }
}
```

No new fields on the CR model. The `change_type` value is the only new schema element.

Execution result tracks step progress:

```json
{
  "steps": [
    {"index": 0, "label": "...", "status": "completed", "result": {...}},
    {"index": 1, "label": "...", "status": "failed",    "error": "..."},
    {"index": 2, "label": "...", "status": "pending"}
  ],
  "current_step": 1
}
```

Step statuses: `pending`, `executing`, `completed`, `failed`, `skipped`.

---

## Section 2: Execution Lifecycle

**Standard flow:** `draft → planned → awaiting_approval → approved → executing → completed`

**On step failure:** CR transitions from `executing` to `paused`. The failed step's error is recorded in `execution_result.steps[n].error`. The operator sees the current step index and three available actions:

- `POST /change-requests/{id}/retry-step` — re-run the failed step with the same params; CR returns to `executing`
- `POST /change-requests/{id}/skip-step` — mark the step `skipped`, advance `current_step`, continue executing; CR returns to `executing`
- `POST /change-requests/{id}/rollback` — trigger FILO unwind (existing endpoint, new behavior for this CR type)

**`paused` is a new CR status.** It sits between `executing` and terminal states. The UI surfaces it identically to `awaiting_approval` — operator action required.

**All steps complete:** CR transitions to `completed`.

**Execution engine:** A new `credential_rotation_workflow` function in the backend handles the serial loop. It is invoked by the existing CR execution dispatcher when `change_type == "credential_rotation"`. For each step it:
1. Resolves the connector by `connector_type` (same lookup used by catalog_action executor)
2. Calls `executor_module.execute(params, asset_ids=[], connector=connector)`
3. Records result or error in `execution_result.steps[n]`
4. Advances or pauses

---

## Section 3: Rollback Ordering

Rollback is triggered via `POST /change-requests/{id}/rollback` (existing endpoint). For `credential_rotation` CRs, the rollback handler:

1. Collects all steps with `status: completed` from `execution_result.steps`
2. Walks them in **reverse index order** (FILO)
3. For each completed step:
   - Resolves the executor module for `connector_type` + `action_id`
   - Calls `executor_module.rollback(step.params, step.result, connector)`
   - Records outcome in `execution_result.steps[n].rollback_result`
4. If any step returns `rolled_back: False`, records it as a warning and continues
5. Terminal status: `rolled_back` if all warnings are absent; `rolled_back_with_warnings` if any step could not fully undo

`rolled_back_with_warnings` is a new terminal CR status. The UI displays it like `rolled_back` but with a warning banner listing the steps that couldn't undo and their reasons.

Skipped steps are excluded from rollback — they were never executed.

---

## Section 4: New API Endpoints

**`POST /change-requests/{id}/retry-step`**
- Valid only when CR status is `paused`
- Re-runs `execution_result.steps[current_step]` with original params
- Transitions CR back to `executing`
- Returns 400 if CR is not `paused`

**`POST /change-requests/{id}/skip-step`**
- Valid only when CR status is `paused`
- Sets `execution_result.steps[current_step].status = "skipped"`
- Increments `current_step`; if more steps remain, continues executing; if last step, transitions to `completed`
- Transitions CR back to `executing` (or `completed`)
- Returns 400 if CR is not `paused`

---

## Section 5: Smoke Test

File: `backend/tests/smoke/test_credential_rotation_smoke.py`

Uses the same `NexplaneClient` / `_run_cr` / `_rollback` helper pattern as other smoke tests.

**Phase 1 — Happy path fan-out:**
1. Create a `credential_rotation` CR with two steps:
   - Step 0: `aws / rotate_iam_key` for smoke IAM user `nexplane-smoke-rotation`
   - Step 1: `aws / rotate_secrets_manager_secret` for secret `nexplane-smoke-rotation-key`
2. Plan → approve → execute → poll until `completed`
3. Assert both steps have `status: completed` in execution result
4. Trigger rollback → poll until `rolled_back` or `rolled_back_with_warnings`
5. Assert step 1 rolled back first (Secrets Manager version restored), then step 0

**Phase 2 — Pause/skip path:**
1. Create a `credential_rotation` CR with two steps:
   - Step 0: `aws / rotate_iam_key` for smoke IAM user
   - Step 1: invalid params (non-existent secret id) to force failure
2. Plan → approve → execute → poll until `paused`
3. Assert `current_step == 1` and step 1 has `status: failed`
4. Call `POST /change-requests/{id}/skip-step`
5. Poll until `completed`
6. Assert step 0 `completed`, step 1 `skipped`
7. Trigger rollback → assert only step 0 is unwound (step 1 excluded)

Both phases use AWS credentials from the platform database via `get_connector_creds_from_db("aws")`. The smoke IAM user and Secrets Manager secret are created and torn down within the test (or pre-existing smoke fixtures if already present).
