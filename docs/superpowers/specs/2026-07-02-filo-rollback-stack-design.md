# FILO Rollback Stack — Implementation Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce FILO rollback ordering in project rollback, implement `to_cr_id` partial rollback, and prove both with a live smoke test.

**Architecture:** Two targeted fixes to existing files (`projects.py` and `project_rollback_service.py`) close the known code gaps; a new smoke suite exercises per-CR FILO guard, asset-level rollback-all, and project rollback with `to_cr_id`. No new models or migrations.

**Tech Stack:** Python/SQLAlchemy async, FastAPI, pytest-asyncio, Nexplane agent (`apply_sysctl_hardening` executor with snapshot-based rollback).

## Global Constraints

- NEVER use `from __future__ import annotations` in any file that contains FastMCP `@mcp.tool()` decorators — FastMCP inspects annotations at runtime and crashes with string annotations.
- All smoke tests run on EC2 via Tailscale (`ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39`); never from laptop.
- Smoke env vars: `API_TOKEN` (nxp_... token), `ASSET_ID` (UUID of platform asset with running agent).
- Maximum two hosts for smoke infrastructure — all phases run on the single existing platform asset.
- The `_auth()` helper returns `(principal, db, db_cm)`. For FK fields needing a user UUID (`actor_id`, `created_by`), check `isinstance(principal, User)` and use `principal.created_by_user_id` for AgentTokens.
- All smoke changes go through the Nexplane CR lifecycle (create → plan → approve → execute → rollback), not direct executor calls.
- Smoke must verify rollback happened by checking observable state (agent file/sysctl, CR status in DB), not just by absence of error.

---

## Fix A: `to_cr_id` Enforcement in `rollback_project`

**File:** `backend/app/mcp_tools/projects.py` — `rollback_project` function

**Current behavior:** `to_cr_id` is parsed, logged as "not yet enforced", and silently ignored — full rollback always runs.

**Target behavior:** When `to_cr_id` is provided, find that CR's `sequence_order` in `ProjectChangeRequest`, filter eligible members to those with `sequence_order >= target_sequence_order` (the target CR and everything applied after it in project order), and pass those IDs as `cr_ids` to `project_rollback_service.initiate()`. The service already handles the `cr_ids` filter; this is only a change in the MCP tool.

**Semantics:** `to_cr_id` means "roll back from the most recently applied CR down to and including this CR." CRs applied before `to_cr_id` in project order are left untouched.

**Error cases:**
- `to_cr_id` not a member of the project → return `{"error": "cr_not_in_project", "cr_id": to_cr_id}`
- `to_cr_id` references a CR that was never executed (status not `completed`) → return `{"error": "cr_not_executed", "cr_id": to_cr_id}`
- After filtering, zero eligible members → return `{"rollback_id": null, "message": "no executed CRs to roll back in range"}`

---

## Fix B: `application_sequence`-First Ordering in Project Rollback

**File:** `backend/app/services/project_rollback_service.py` — `initiate()` function

**Current behavior:** Eligible members sorted by `ProjectChangeRequest.sequence_order DESC` only.

**Target behavior:** Sort by `change_request.application_sequence DESC` if the field is non-null on the CR; fall back to `sequence_order DESC` for CRs not yet executed (application_sequence is NULL). This ensures rollback order reflects actual execution order, not just planned project order.

**Divergence warning:** After sorting, if the resulting order differs from what a pure `sequence_order` sort would produce, emit a `logger.warning("project rollback order diverges from plan order: %s", divergence_detail)` — this flags cases where CRs were reordered in the project after some had already executed.

**Implementation note:** `eligible_members` are `ProjectChangeRequest` instances. The sort requires `m.change_request.application_sequence` — the `change_request` relationship must be eagerly loaded. In `rollback_project` in `projects.py`, the project is loaded via `db.get(Project, project_uuid)` or a `select(Project)` query; add `.options(selectinload(Project.members).selectinload(ProjectChangeRequest.change_request))` to that query so `m.change_request` is available without triggering lazy loads in the service. Import `selectinload` from `sqlalchemy.orm` and `ProjectChangeRequest` from `app.models.project`.

---

## Smoke Test: `test_smoke_filo_rollback.py`

**File:** `backend/tests/smoke/test_smoke_filo_rollback.py`

**CR type:** `apply_sysctl_hardening` — changes a benign sysctl param (`net.ipv4.tcp_keepalive_time`, default 7200) by ±1. Executor dispatches agent job; rollback restores from snapshot. Fully reversible, no infra beyond running agent.

**Module-level state:** `_STATE: dict` carries `cr_a_id`, `cr_b_id`, `cr_c_id`, `cr_d_id`, `project_id`, `cr_a_seq`, `cr_b_seq` across phases.

**Helper:** `async def _create_and_execute_sysctl_cr(token, asset_id, value) -> str` — creates a CR of type `apply_sysctl_hardening` with the given value, plans it, approves it, executes it, polls until `completed`, returns `cr_id`. Reusable across phases.

### PHASE_1: Execute CR-A

Create and execute a sysctl CR setting `net.ipv4.tcp_keepalive_time=7199` on `ASSET_ID`. Assert:
- CR status is `completed`
- `application_sequence` is not None and is an integer
- Store as `_STATE["cr_a_id"]` and `_STATE["cr_a_seq"]`

### PHASE_2: Execute CR-B

Create and execute a sysctl CR setting `net.ipv4.tcp_keepalive_time=7198` on `ASSET_ID`. Assert:
- CR status is `completed`
- `application_sequence` > `_STATE["cr_a_seq"]` (proves monotonic ordering)
- Store as `_STATE["cr_b_id"]` and `_STATE["cr_b_seq"]`

### PHASE_3: FILO Guard Blocks Out-of-Order Rollback

Attempt `POST /change-requests/{cr_a_id}/rollback`. Assert:
- HTTP 409
- Response body has `error == "out_of_order_rollback"`
- `blocking_crs` list contains `cr_b_id`
- CR-A status is still `completed` (rollback did not proceed)

### PHASE_4: Asset Rollback-All

Call `POST /assets/{asset_id}/rollback-all`. Assert:
- HTTP 200
- Response `rolled_back` list contains both `cr_b_id` and `cr_a_id`
- `failed_at` is `null`
- CR-B status is `rolled_back` (confirmed via `GET /change-requests/{cr_b_id}`)
- CR-A status is `rolled_back` (confirmed via `GET /change-requests/{cr_a_id}`)
- CR-B was rolled back before CR-A (order in `rolled_back` list: CR-B first)

### PHASE_5: Project Rollback with `to_cr_id`

Setup:
1. Create project via `create_project` MCP tool.
2. Create and execute CR-C (`tcp_keepalive_time=7199`), add to project.
3. Create and execute CR-D (`tcp_keepalive_time=7198`), add to project.

Partial rollback:
4. Call `rollback_project` MCP tool with `to_cr_id=cr_d_id` (only CR-D, the most recent).
5. Assert CR-D status is `rolled_back`.
6. Assert CR-C status is still `completed` (not rolled back — outside the `to_cr_id` range).

Full cleanup:
7. Call `rollback_project` with no `to_cr_id`.
8. Assert CR-C status is `rolled_back`.

---

## Test Execution

```bash
cd backend
ASSET_ID=<uuid> API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_filo_rollback.py -v -s
```

All 5 phases must pass. Run on EC2 (`docker exec nexplane-backend-1 pytest ...` or from the backend virtualenv with `DATABASE_URL` set).
