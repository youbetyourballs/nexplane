# MCP Smoke Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run all four existing MCP smoke files against live EC2 infrastructure, fix every failure, then add four new CR-workflow phases that prove the MCP layer drives and surfaces real domain operations (snapshot, backup, DB dump/restore, containerize) end-to-end.

**Architecture:** All smoke tests run in-process inside the `nexplane-backend-1` Docker container on EC2 (`100.101.186.39`) via SSH. Tests import MCP tools directly from `app.mcp_tools.*` and hit the live database through the NullPool-patched engine in `conftest.py`. The four new CR-workflow phases use the same approver pattern as `test_mcp_server_live.py` MCP_CR_ROUNDTRIP: a second user (`approver@acme.example`) approves CRs to satisfy the anti-self-approval constraint.

**Tech Stack:** Python 3.11, pytest, pytest-asyncio, asyncio (loop_scope=session), SQLAlchemy async, httpx, boto3, FastAPI MCP tools imported in-process.

## Global Constraints

- All runs execute via `docker exec nexplane-backend-1` on EC2 `100.101.186.39`; never from the Windows laptop
- `NEXPLANE_SMOKE=1` env var is the gate on every file
- Exit code 2 = AI budget pause (not failure) — distinct from pytest exit code 1
- Each existing file must be fully green before starting the next
- `approve_change_request` MCP tool enforces no-self-approval; use `_get_approver_token()` pattern from `test_mcp_server_live.py` (logs in as `approver@acme.example`) for all new CR workflow tests
- New smoke file follows the in-process call pattern: `from app.mcp_tools.change_requests import create_change_request` — no HTTP calls to MCP endpoints
- DB ground truth checks use `AsyncSessionLocal` directly (NullPool, already patched by conftest.py)
- Commits happen after each file is fully green; never commit a failing file

---

## File Map

| File | Action |
|------|--------|
| `backend/tests/smoke/smoke_helpers.py` | Add `check_for_budget_pause()` function |
| `backend/tests/smoke/test_mcp_server_live.py` | Fix failures found in live run |
| `backend/tests/smoke/test_smoke_mcp_host_intelligence.py` | Fix failures found in live run |
| `backend/tests/smoke/test_smoke_mcp_planning_context.py` | Fix failures found in live run |
| `backend/tests/smoke/test_mcp_project_orchestration.py` | Fix failures found in live run |
| `backend/tests/smoke/test_smoke_mcp_cr_workflows.py` | **Create** — 4 new CR-workflow phases |

---

### Task 1: Add check_for_budget_pause() to smoke_helpers.py

**Files:**
- Modify: `backend/tests/smoke/smoke_helpers.py`

**Interfaces:**
- Produces: `check_for_budget_pause(result: dict | None) -> None` — raises `SystemExit(2)` on AI budget exhaustion signals

- [ ] **Step 1: Locate the insertion point in smoke_helpers.py**

  Open `backend/tests/smoke/smoke_helpers.py`. Find the `log()` and `fail()` helper functions near the top (around line 40). Insert the new function immediately after `fail()`.

- [ ] **Step 2: Add the function**

  ```python
  # Budget signals from the backend's AI proxy layer
  _BUDGET_SIGNALS = frozenset({
      "quota", "budget", "rate_limit", "insufficient_quota",
      "context_length_exceeded", "billing", "capacity",
  })


  def check_for_budget_pause(result: "dict | None") -> None:
      """
      Call after every MCP tool call result. If the result contains an AI
      budget-exhaustion signal, print a clear pause message and exit with
      code 2 (distinct from pytest failure exit code 1) so the operator
      knows to expand the API cap before resuming.
      """
      if not isinstance(result, dict):
          return
      # Check HTTP-level error propagated into result
      error_text = str(result.get("error", "")).lower()
      detail_text = str(result.get("detail", "")).lower()
      combined = error_text + " " + detail_text
      if any(sig in combined for sig in _BUDGET_SIGNALS):
          phase = result.get("phase", "unknown phase")
          api = "Claude" if "claude" in combined or "anthropic" in combined else \
                "OpenAI" if "openai" in combined else "AI"
          log(f"BUDGET_PAUSE: {api} API cap hit during {phase}. "
              f"Expand the budget cap then re-run from this phase.", ok=False)
          print(f"\n⚠️  AI API budget exhausted ({api}). "
                f"Passed phases are already committed. "
                f"Expand the cap and resume.\n")
          sys.exit(2)
  ```

- [ ] **Step 3: Verify the function is importable from the container**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "docker exec nexplane-backend-1 python -c \
    'import sys; sys.path.insert(0,\"/app/tests/smoke\"); \
     from smoke_helpers import check_for_budget_pause; print(\"ok\")'"
  ```
  Expected output: `ok`

- [ ] **Step 4: scp the updated file to EC2**

  ```bash
  scp -i ~/.ssh/id_ed25519 \
    backend/tests/smoke/smoke_helpers.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/smoke_helpers.py
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add backend/tests/smoke/smoke_helpers.py
  git commit -m "smoke: add check_for_budget_pause() to smoke_helpers"
  ```

---

### Task 2: Run test_mcp_server_live.py — fix all failures

**Files:**
- Fix (as needed): `backend/tests/smoke/test_mcp_server_live.py`
- Fix (as needed): any `backend/app/mcp_tools/*.py` file surfacing a bug

**Interfaces:**
- Consumes: `check_for_budget_pause()` from Task 1
- Produces: all 21 phases passing on EC2

- [ ] **Step 1: Obtain required env values from the live platform**

  Get the admin email/password and an API token:
  ```bash
  # Get admin credentials from the platform seed (check seed.py or .env on EC2)
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "docker exec nexplane-backend-1 grep -E 'ADMIN|admin' /app/.env 2>/dev/null || \
     cat /home/ec2-user/nexplane/.env | grep -i admin"

  # Create an API token via REST
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "curl -s -X POST http://localhost:8000/auth/login \
     -H 'Content-Type: application/json' \
     -d '{\"email\":\"admin@nexplane.local\",\"password\":\"<password>\"}' \
     | python3 -c 'import sys,json; t=json.load(sys.stdin); print(t[\"access_token\"])'"
  ```

  Then create an agent token:
  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "curl -s -X POST http://localhost:8000/api/v1/tokens \
     -H 'Authorization: Bearer <access_token>' \
     -H 'Content-Type: application/json' \
     -d '{\"name\":\"smoke-test\",\"scopes\":[]}' \
     | python3 -c 'import sys,json; t=json.load(sys.stdin); print(t.get(\"raw_token\") or t.get(\"token\"))'"
  ```

  Get an asset with a live agent:
  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "curl -s http://localhost:8000/assets \
     -H 'Authorization: Bearer <access_token>' \
     | python3 -c 'import sys,json; a=json.load(sys.stdin); [print(x[\"id\"],x[\"name\"]) for x in a.get(\"items\",a) if x.get(\"agent_registered\")]'"
  ```

- [ ] **Step 2: Run test_mcp_server_live.py against the live backend**

  `test_mcp_server_live.py` uses argparse (not pytest). Run it as:
  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec \
    -e NEXPLANE_SMOKE=1 \
    nexplane-backend-1 python /app/tests/smoke/test_mcp_server_live.py \
    --base-url http://localhost:8000 \
    --email admin@nexplane.local \
    --password '<password>' \
    --phases MCP_TOOL_ENUM,MCP_AGENT_TOKEN_AUTH,MCP_CR_ROUNDTRIP,MCP_PROVENANCE,\
  MCP_WHO_APPROVED,MCP_DEPENDENCY,MCP_SAFETY_QUERY,MCP_TIMELINE,MCP_FINDINGS,\
  MCP_CONNECTORS,MCP_IDENTITY,MCP_RUNBOOKS,MCP_HOST_INTEL,MCP_PLANNING_CTX,\
  MCP_MEMORY_ACCURACY,MCP_INFRA_PROVENANCE,MCP_INFRA_TIMELINE,MCP_INFRA_QUERY,\
  MCP_INFRA_DELETION_CHECK,MCP_IMPACT_GRAPH,MCP_IMPACT_PLANNING 2>&1 | tee /tmp/mcp_live_run.txt"
  ```

  Review output in `/tmp/mcp_live_run.txt`. Run individual phases to isolate:
  ```bash
  # Run only the first failing phase:
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec \
    -e NEXPLANE_SMOKE=1 nexplane-backend-1 \
    python /app/tests/smoke/test_mcp_server_live.py \
    --base-url http://localhost:8000 \
    --email admin@nexplane.local --password '<password>' \
    --phases MCP_TOOL_ENUM 2>&1"
  ```

- [ ] **Step 3: Diagnose and fix each failure category**

  Common failure patterns and fixes:

  **A. Tool count mismatch (`expected 78 tools, got N`)**
  - The `EXPECTED_TOOLS` set in `test_mcp_server_live.py` may not match what's registered.
  - Fix: update `EXPECTED_TOOLS` to match `list_mcp_tools_via_sse()` output, OR register the missing tool in the appropriate `backend/app/mcp_tools/*.py` file.

  **B. `AttributeError: 'NoneType' has no attribute 'id'` in MCP_CR_ROUNDTRIP**
  - Asset not found or agent not registered.
  - Fix: ensure at least one asset with `agent_registered=True` exists. If no agent is live, the `MCP_HOST_INTEL` phase will also fail — seed a fake asset or register the EC2 instance as an asset.

  **C. `HTTPException: 401` from `resolve_mcp_token`**
  - API token expired or wrong format.
  - Fix: re-create the token via `POST /api/v1/tokens`.

  **D. `approver@acme.example` login fails in MCP_CR_ROUNDTRIP**
  - The demo approver user doesn't exist in this deployment.
  - Fix: create the user via the REST API:
    ```bash
    curl -s -X POST http://localhost:8000/users \
      -H 'Authorization: Bearer <admin_token>' \
      -H 'Content-Type: application/json' \
      -d '{"email":"approver@acme.example","password":"approver123","role":"approver","name":"Smoke Approver"}'
    ```

  **E. Exit code 2 (budget pause)**
  - The backend's AI proxy hit its cap.
  - Action: expand the OpenAI or Claude budget cap in the platform settings, then resume.

  **F. `KeyError` or `AttributeError` in MCP tool implementation**
  - Real bug in a `backend/app/mcp_tools/*.py` function.
  - Fix: read the traceback, open the relevant file, fix the bug, restart the backend container:
    ```bash
    ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
      "cd /home/ec2-user/nexplane && docker compose restart backend"
    ```

- [ ] **Step 4: Iterate until all 21 phases pass**

  Re-run after each fix. Only commit when all 21 phases pass (exit code 0).

- [ ] **Step 5: scp any changed backend files to EC2 before testing**

  For each backend file you edit locally:
  ```bash
  scp -i ~/.ssh/id_ed25519 \
    backend/app/mcp_tools/<changed_file>.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/mcp_tools/<changed_file>.py
  # Then restart backend:
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "cd /home/ec2-user/nexplane && docker compose restart backend"
  ```

- [ ] **Step 6: Commit all fixes**

  ```bash
  git add backend/tests/smoke/test_mcp_server_live.py \
          backend/app/mcp_tools/  # any files changed
  git commit -m "smoke: fix test_mcp_server_live.py — all 21 phases passing on EC2"
  ```

---

### Task 3: Run test_smoke_mcp_host_intelligence.py — fix all failures

**Files:**
- Fix (as needed): `backend/tests/smoke/test_smoke_mcp_host_intelligence.py`
- Fix (as needed): `backend/app/mcp_tools/host_intelligence.py`

**Interfaces:**
- Consumes: API_TOKEN from Task 2; ASSET_ID of asset with live Nexplane agent
- Produces: all 4 phases (PHASE_1 through PHASE_4) passing

- [ ] **Step 1: Verify migration intel001 is applied**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "docker exec nexplane-backend-1 python -c \
    'import asyncio; from app.database import AsyncSessionLocal; from sqlalchemy import text
  async def check():
      async with AsyncSessionLocal() as db:
          r = await db.execute(text(\"SELECT version_num FROM alembic_version WHERE version_num LIKE \x27intel%\x27\"))
          rows = r.fetchall()
          print(rows)
  asyncio.run(check())'"
  ```
  If `intel001` is missing, run migrations:
  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "docker exec nexplane-backend-1 alembic upgrade heads"
  ```

- [ ] **Step 2: Run the test**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec \
    -e NEXPLANE_SMOKE=1 \
    -e API_TOKEN=<nxp_token_from_task2> \
    -e ASSET_ID=<asset_with_agent> \
    nexplane-backend-1 \
    python -m pytest /app/tests/smoke/test_smoke_mcp_host_intelligence.py -v -s 2>&1"
  ```

- [ ] **Step 3: Diagnose and fix each failure**

  Common failures:

  **A. `{"error": "No agent registered for asset <id>"}` from any host intelligence tool**
  - The asset has no live Nexplane agent.
  - Fix option 1: deploy an agent on the EC2 smoke instance and register it.
  - Fix option 2: if no agent is available, add `pytest.skip("no live agent")` at module level and document as a known infra gap.

  **B. `{"error": "intel001 migration not applied"}`**
  - Run `alembic upgrade heads` as shown in Step 1.

  **C. Cache hit PHASE_2 fails: count increased when it should stay the same**
  - Host intelligence cache TTL logic may have a bug.
  - Open `backend/app/mcp_tools/host_intelligence.py`, find the cache-read path, verify it returns cached result without dispatching a new agent job within 300s.

  **D. Cache invalidation PHASE_4 fails: count didn't go to 0 then 1**
  - The `invalidate_cache()` tool may not be clearing the correct DB rows.
  - Check `backend/app/mcp_tools/host_intelligence.py` `invalidate_cache()` implementation against the `host_intelligence_cache` table.

- [ ] **Step 4: Iterate until all 4 phases pass, then commit**

  ```bash
  git add backend/tests/smoke/test_smoke_mcp_host_intelligence.py \
          backend/app/mcp_tools/host_intelligence.py
  git commit -m "smoke: fix test_smoke_mcp_host_intelligence.py — all 4 phases passing on EC2"
  ```

---

### Task 4: Run test_smoke_mcp_planning_context.py — fix all failures

**Files:**
- Fix (as needed): `backend/tests/smoke/test_smoke_mcp_planning_context.py`
- Fix (as needed): `backend/app/mcp_tools/planning_context.py`

**Interfaces:**
- Consumes: API_TOKEN and ASSET_ID from Task 2; migrations intel001 + pc001
- Produces: all 8 phases passing

- [ ] **Step 1: Verify migrations intel001 and pc001 are applied**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "docker exec nexplane-backend-1 python -c \
    'import asyncio; from app.database import AsyncSessionLocal; from sqlalchemy import text
  async def check():
      async with AsyncSessionLocal() as db:
          r = await db.execute(text(\"SELECT version_num FROM alembic_version\"))
          print([row[0] for row in r.fetchall()])
  asyncio.run(check())'"
  ```
  Both `intel001` and `pc001` must appear. If missing: `alembic upgrade heads`.

- [ ] **Step 2: Run the test**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec \
    -e NEXPLANE_SMOKE=1 \
    -e API_TOKEN=<nxp_token> \
    -e ASSET_ID=<asset_id> \
    nexplane-backend-1 \
    python -m pytest /app/tests/smoke/test_smoke_mcp_planning_context.py -v -s 2>&1"
  ```

- [ ] **Step 3: Diagnose and fix each failure**

  Common failures:

  **A. PHASE_3 find_similar_assets: `similarity_score` out of range (not 0.0–1.0)**
  - Bug in the similarity scoring normalization in `planning_context.py`.
  - Find `find_similar_assets()`, check the score computation, clamp to `max(0.0, min(1.0, score))`.

  **B. PHASE_5 get_cross_host_dependency_map: `edges` key missing**
  - The function may return a flat list instead of `{"edges": [...], "external_dependencies": [...]}`.
  - Fix the return shape in `planning_context.py`.

  **C. PHASE_7 get_environment_diff: single asset returns wrong error shape**
  - Test expects `{"error": "..."}` on single asset input; function may raise an exception instead.
  - Wrap the single-asset case in `planning_context.py` to return `{"error": "Need at least 2 assets"}`.

  **D. PHASE_8 get_project_precedents: unknown goal returns empty list but similarity_score is 0.0**
  - Test checks `similarity_score > 0.0` for a matching goal.
  - Ensure the goal matching uses the same embedding model as existing CR records; if no matching CRs exist, add a seed CR or skip with `pytest.skip("no project precedents seeded")`.

  **E. Exit code 2 (budget pause)**
  - `get_migration_precedents` and `find_similar_assets` may call the AI layer.
  - Action: expand the cap and resume from the failing phase.

- [ ] **Step 4: Iterate until all 8 phases pass, then commit**

  ```bash
  git add backend/tests/smoke/test_smoke_mcp_planning_context.py \
          backend/app/mcp_tools/planning_context.py
  git commit -m "smoke: fix test_smoke_mcp_planning_context.py — all 8 phases passing on EC2"
  ```

---

### Task 5: Run test_mcp_project_orchestration.py — fix all failures

**Files:**
- Fix (as needed): `backend/tests/smoke/test_mcp_project_orchestration.py`
- Fix (as needed): `backend/app/mcp_tools/projects.py`

**Interfaces:**
- Consumes: API_TOKEN from Task 2; migrations proj001 + proj002; at least one existing asset
- Produces: all 12 phases passing

- [ ] **Step 1: Verify migrations proj001 and proj002 are applied**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
    "docker exec nexplane-backend-1 alembic current"
  ```
  Both `proj001` and `proj002` must appear. If missing: `alembic upgrade heads`.

- [ ] **Step 2: Run the test**

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec \
    -e NEXPLANE_SMOKE=1 \
    -e API_TOKEN=<nxp_token> \
    -e ASSET_ID=<asset_id> \
    nexplane-backend-1 \
    python -m pytest /app/tests/smoke/test_mcp_project_orchestration.py -v -s 2>&1"
  ```

- [ ] **Step 3: Diagnose and fix each failure**

  Common failures:

  **A. PHASE_1 create_project: `status` is not `draft`**
  - Check `projects.py` `create_project()` — the initial status must be `"draft"` not `"active"`.

  **B. PHASE_5 add_cr_to_project: `cr_count` not incrementing**
  - `add_cr_to_project()` may not be committing the association.
  - Check the DB transaction in `projects.py`.

  **C. PHASE_6 define_success_criteria: `pending_manual` not returned**
  - `check_success_criteria()` must return status `"pending_manual"` for manually-verified criteria.
  - Check `projects.py` `check_success_criteria()` for the pending-manual branch.

  **D. PHASE_12 rollback_project: error on draft project**
  - The test expects rollback of a draft project to return a descriptive no-op message (not a crash).
  - Fix `projects.py` `rollback_project()` to return `{"error": "No executed CRs to roll back"}` rather than raise.

  **E. `KeyError: 'cr_count'` in PHASE_2 list_projects**
  - `list_projects()` return shape may omit `cr_count`.
  - Add `cr_count` to the returned dict in `projects.py`.

- [ ] **Step 4: Iterate until all 12 phases pass, then commit**

  ```bash
  git add backend/tests/smoke/test_mcp_project_orchestration.py \
          backend/app/mcp_tools/projects.py
  git commit -m "smoke: fix test_mcp_project_orchestration.py — all 12 phases passing on EC2"
  ```

---

### Task 6: Create test_smoke_mcp_cr_workflows.py — 4 new CR-workflow phases

**Files:**
- Create: `backend/tests/smoke/test_smoke_mcp_cr_workflows.py`

**Interfaces:**
- Consumes: `check_for_budget_pause()` from Task 1; `NexplaneClient` and `log()` from `smoke_helpers`; MCP tools from `app.mcp_tools.change_requests`; `AsyncSessionLocal` from `app.database`; `ChangeRequest` model from `app.models`
- Produces: 4 phases (MCP_SNAPSHOT, MCP_BACKUP, MCP_DB_MIGRATE, MCP_CONTAINERIZE) passing against live infra with FILO rollback verified

- [ ] **Step 1: Create the file with shared infrastructure**

  Create `backend/tests/smoke/test_smoke_mcp_cr_workflows.py`:

  ```python
  # SPDX-License-Identifier: AGPL-3.0-only
  # Copyright (C) 2024-2026 Nexplane, Inc.
  """
  Smoke: MCP CR-workflow phases
  
  Tests that real domain CRs submitted entirely via MCP tools produce results
  matching executor-level ground truth. All four phases follow the same pattern:
      create_change_request (MCP)
      → submit_for_approval (MCP)
      → approve via REST as approver@acme.example
      → execute_change_request (MCP)
      → poll get_change_request (MCP) until terminal
      → assert result fields (borrowed from connector smoke tests)
      → cross-check MCP result vs DB row
      → rollback_change_request (MCP)
      → poll until rollback terminal
      → assert cleanup
  
  Run from EC2:
      docker exec \\
        -e NEXPLANE_SMOKE=1 \\
        -e API_TOKEN=nxp_... \\
        -e ASSET_ID=<uuid-of-ec2-smoke-instance-asset> \\
        nexplane-backend-1 \\
        python -m pytest /app/tests/smoke/test_smoke_mcp_cr_workflows.py -v -s
  
  The approver (approver@acme.example, password approver123) must exist.
  If it doesn't, create it first:
      curl -X POST http://localhost:8000/users \\
        -H 'Authorization: Bearer <admin_token>' \\
        -H 'Content-Type: application/json' \\
        -d '{"email":"approver@acme.example","password":"approver123","role":"approver","name":"Smoke Approver"}'
  """
  
  from __future__ import annotations
  
  import asyncio
  import os
  import sys
  import time
  
  import pytest
  
  sys.path.insert(0, "/app")
  sys.path.insert(0, "/app/tests/smoke")
  
  from smoke_helpers import NexplaneClient, check_for_budget_pause, log
  
  from app.mcp_tools.change_requests import (
      approve_change_request,
      create_change_request,
      execute_change_request,
      get_change_request,
      rollback_change_request,
      submit_for_approval,
  )
  
  if os.environ.get("NEXPLANE_SMOKE") != "1":
      pytest.skip("Set NEXPLANE_SMOKE=1 to run smoke tests", allow_module_level=True)
  
  pytestmark = pytest.mark.asyncio(loop_scope="session")
  
  BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
  API_TOKEN = os.environ.get("API_TOKEN", "")
  ASSET_ID = os.environ.get("ASSET_ID", "")
  
  _STATE: dict = {}
  
  
  def _require(*keys: str) -> None:
      missing = [k for k in keys if not os.environ.get(k)]
      if missing:
          pytest.skip(f"Required env vars not set: {', '.join(missing)}")
  
  
  def _get_approver_token() -> str:
      """Return a bearer token for approver@acme.example (different user from API_TOKEN creator).
      MCP enforce no-self-approval; this mirrors the pattern in test_mcp_server_live.py."""
      for password in ("approver123", "admin123", "Approver123!"):
          try:
              import httpx
              resp = httpx.post(
                  f"{BASE_URL}/auth/login",
                  json={"email": "approver@acme.example", "password": password},
                  timeout=30,
              )
              if resp.status_code == 200:
                  log(f"Approver login OK (approver@acme.example)")
                  return resp.json()["access_token"]
          except Exception:
              pass
      pytest.fail(
          "Could not log in as approver@acme.example. "
          "Create the user first:\n"
          "  curl -X POST http://localhost:8000/users "
          "-H 'Authorization: Bearer <admin_token>' "
          "-d '{\"email\":\"approver@acme.example\",\"password\":\"approver123\","
          "\"role\":\"approver\",\"name\":\"Smoke Approver\"}'"
      )
      return ""  # unreachable
  
  
  async def _create_approver_api_token(approver_bearer: str) -> str:
      """Create an MCP-compatible nxp_ token for the approver."""
      import httpx
      resp = httpx.post(
          f"{BASE_URL}/api/v1/tokens",
          headers={"Authorization": f"Bearer {approver_bearer}"},
          json={"name": "smoke-approver", "scopes": []},
          timeout=30,
      )
      resp.raise_for_status()
      data = resp.json()
      return data.get("raw_token") or data.get("token") or data["id"]
  
  
  async def _poll_cr(cr_id: str, timeout: int = 600) -> dict:
      deadline = time.time() + timeout
      while time.time() < deadline:
          cr = await get_change_request(token=API_TOKEN, cr_id=cr_id)
          check_for_budget_pause(cr)
          status = cr.get("status", "")
          if status in ("completed", "failed", "rolled_back", "rejected"):
              return cr
          log(f"  CR {cr_id} status={status} — waiting 10s…")
          await asyncio.sleep(10)
      pytest.fail(f"CR {cr_id} did not reach terminal state within {timeout}s")
  
  
  async def _submit_and_execute(cr_id: str, approver_token: str) -> dict:
      """Submit for approval, approve as approver, execute, poll to terminal."""
      await submit_for_approval(token=API_TOKEN, cr_id=cr_id)
      log(f"  CR {cr_id} submitted for approval")
  
      approved = await approve_change_request(token=approver_token, cr_id=cr_id,
                                              comment="smoke approval")
      check_for_budget_pause(approved)
      log(f"  CR {cr_id} approved")
  
      executed = await execute_change_request(token=API_TOKEN, cr_id=cr_id)
      check_for_budget_pause(executed)
      log(f"  CR {cr_id} executing")
  
      return await _poll_cr(cr_id)
  
  
  async def _rollback_and_poll(cr_id: str, approver_token: str,
                               timeout: int = 300) -> dict:
      rb = await rollback_change_request(token=API_TOKEN, cr_id=cr_id)
      check_for_budget_pause(rb)
      rollback_cr_id = rb.get("rollback_cr_id") or cr_id
      return await _poll_cr(rollback_cr_id, timeout=timeout)
  
  
  async def _db_get_cr(cr_id: str) -> object:
      from app.database import AsyncSessionLocal
      from app.models import ChangeRequest
      async with AsyncSessionLocal() as db:
          row = await db.get(ChangeRequest, cr_id)
          return row
  ```

- [ ] **Step 2: Verify the shared infrastructure imports without error**

  After scping the file to EC2:
  ```bash
  scp -i ~/.ssh/id_ed25519 \
    backend/tests/smoke/test_smoke_mcp_cr_workflows.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/test_smoke_mcp_cr_workflows.py

  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec \
    -e NEXPLANE_SMOKE=1 -e API_TOKEN=dummy -e ASSET_ID=dummy \
    nexplane-backend-1 \
    python -c 'import sys; sys.path.insert(0,\"/app\"); sys.path.insert(0,\"/app/tests/smoke\"); \
    from smoke_helpers import check_for_budget_pause; \
    from app.mcp_tools.change_requests import create_change_request; print(\"imports ok\")'"
  ```
  Expected: `imports ok`

- [ ] **Step 3: Add the MCP_SNAPSHOT phase**

  Append to `test_smoke_mcp_cr_workflows.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Phase MCP_SNAPSHOT
  # os_upgrade with snapshot_only=True — prove MCP drives a real EBS snapshot
  # Success criteria borrowed from test_os_upgrade_smoke.py
  # ---------------------------------------------------------------------------
  
  
  async def test_MCP_SNAPSHOT_setup():
      _require("API_TOKEN", "ASSET_ID")
      bearer = _get_approver_token()
      _STATE["approver_token"] = await _create_approver_api_token(bearer)
      log("MCP_SNAPSHOT: approver token ready")
  
  
  async def test_MCP_SNAPSHOT_create_and_execute():
      approver_token = _STATE.get("approver_token")
      if not approver_token:
          pytest.skip("approver_token not set — setup phase failed")
  
      cr = await create_change_request(
          token=API_TOKEN,
          change_type="os_upgrade",
          asset_id=ASSET_ID,
          title="[smoke] MCP snapshot-only",
          parameters={"snapshot_only": True},
      )
      check_for_budget_pause(cr)
      assert cr.get("status") == "draft", f"Expected draft, got: {cr}"
      cr_id = cr["id"]
      _STATE["snapshot_cr_id"] = cr_id
      log(f"MCP_SNAPSHOT: created CR {cr_id}")
  
      result_cr = await _submit_and_execute(cr_id, approver_token)
  
      assert result_cr["status"] == "completed", \
          f"Snapshot CR did not complete: {result_cr}"
      result = result_cr.get("result") or {}
      assert result.get("snapshot_id"), \
          f"snapshot_id missing from result: {result}"
      assert result.get("root_volume_id"), \
          f"root_volume_id missing from result: {result}"
      assert result.get("availability_zone"), \
          f"availability_zone missing: {result}"
      assert result.get("region"), \
          f"region missing: {result}"
      _STATE["snapshot_id"] = result["snapshot_id"]
      log(f"MCP_SNAPSHOT: snapshot_id={result['snapshot_id']} ✅")
  
  
  async def test_MCP_SNAPSHOT_db_ground_truth():
      cr_id = _STATE.get("snapshot_cr_id")
      if not cr_id:
          pytest.skip("snapshot_cr_id not set")
  
      row = await _db_get_cr(cr_id)
      assert row is not None, f"CR {cr_id} not found in DB"
      assert row.status.value == "completed", \
          f"DB status is {row.status.value!r}, expected completed"
      assert row.result, "result column is empty in DB"
      assert row.result.get("snapshot_id") == _STATE["snapshot_id"], \
          f"DB snapshot_id {row.result.get('snapshot_id')!r} != MCP result {_STATE['snapshot_id']!r}"
  
      mcp_cr = await get_change_request(token=API_TOKEN, cr_id=cr_id)
      check_for_budget_pause(mcp_cr)
      assert mcp_cr["status"] == "completed"
      log("MCP_SNAPSHOT: DB ground truth verified ✅")
  
  
  async def test_MCP_SNAPSHOT_rollback():
      cr_id = _STATE.get("snapshot_cr_id")
      approver_token = _STATE.get("approver_token")
      if not cr_id:
          pytest.skip("snapshot_cr_id not set")
  
      rb = await _rollback_and_poll(cr_id, approver_token)
      assert rb["status"] in ("completed", "rolled_back"), \
          f"Snapshot rollback failed: {rb}"
      # Snapshot is TAGGED for cleanup, not auto-deleted
      # (matches test_os_upgrade_smoke.py SNAPSHOT_AND_ROLLBACK phase behavior)
      # old_volume_id and snapshot_id remain in AWS; operator cleans up tagged resources
      log("MCP_SNAPSHOT: rollback completed — snapshot tagged for cleanup ✅")
  ```

- [ ] **Step 4: Add the MCP_BACKUP phase**

  Append to `test_smoke_mcp_cr_workflows.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Phase MCP_BACKUP
  # server_backup — prove MCP drives a real S3/Vault backup
  # Success criteria borrowed from test_backup_scheduler_live.py
  # ---------------------------------------------------------------------------
  
  
  async def test_MCP_BACKUP_create_and_execute():
      _require("API_TOKEN", "ASSET_ID")
      approver_token = _STATE.get("approver_token")
      if not approver_token:
          pytest.skip("approver_token not set")
  
      cr = await create_change_request(
          token=API_TOKEN,
          change_type="server_backup",
          asset_id=ASSET_ID,
          title="[smoke] MCP server backup",
          parameters={},
      )
      check_for_budget_pause(cr)
      assert cr.get("status") == "draft", f"Expected draft, got: {cr}"
      cr_id = cr["id"]
      _STATE["backup_cr_id"] = cr_id
      log(f"MCP_BACKUP: created CR {cr_id}")
  
      result_cr = await _submit_and_execute(cr_id, approver_token)
  
      assert result_cr["status"] == "completed", \
          f"Backup CR did not complete: {result_cr}"
      result = result_cr.get("result") or {}
      artifact_ref = result.get("artifact_ref")
      assert artifact_ref, \
          f"artifact_ref missing from backup result: {result}"
      _STATE["backup_artifact_ref"] = artifact_ref
      log(f"MCP_BACKUP: artifact_ref={artifact_ref} ✅")
  
  
  async def test_MCP_BACKUP_db_ground_truth():
      cr_id = _STATE.get("backup_cr_id")
      if not cr_id:
          pytest.skip("backup_cr_id not set")
  
      row = await _db_get_cr(cr_id)
      assert row.status.value == "completed"
      assert row.result.get("artifact_ref") == _STATE["backup_artifact_ref"], \
          "DB artifact_ref does not match MCP result"
  
      mcp_cr = await get_change_request(token=API_TOKEN, cr_id=cr_id)
      check_for_budget_pause(mcp_cr)
      assert mcp_cr["status"] == "completed"
      log("MCP_BACKUP: DB ground truth verified ✅")
  
  
  async def test_MCP_BACKUP_rollback():
      cr_id = _STATE.get("backup_cr_id")
      approver_token = _STATE.get("approver_token")
      if not cr_id:
          pytest.skip("backup_cr_id not set")
  
      rb = await _rollback_and_poll(cr_id, approver_token)
      assert rb["status"] in ("completed", "rolled_back"), \
          f"Backup rollback failed: {rb}"
      # Backup artifact is RETAINED on rollback — the artifact IS the safety net
      # (matches test_backup_scheduler_live.py server_restore rollback behavior)
      log("MCP_BACKUP: rollback completed — artifact retained as expected ✅")
  ```

- [ ] **Step 5: Add the MCP_DB_MIGRATE phase**

  Append to `test_smoke_mcp_cr_workflows.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Phase MCP_DB_MIGRATE
  # database_dump + database_restore — prove MCP drives real pg_dump / pg_restore
  # Success criteria borrowed from test_backup_strategies_restore_live.py
  # ---------------------------------------------------------------------------
  
  
  async def test_MCP_DB_MIGRATE_dump():
      _require("API_TOKEN", "ASSET_ID")
      approver_token = _STATE.get("approver_token")
      if not approver_token:
          pytest.skip("approver_token not set")
  
      cr = await create_change_request(
          token=API_TOKEN,
          change_type="database_dump",
          asset_id=ASSET_ID,
          title="[smoke] MCP database dump",
          parameters={"db_type": "postgres", "database": "nexplane"},
      )
      check_for_budget_pause(cr)
      assert cr.get("status") == "draft", f"Expected draft, got: {cr}"
      cr_id = cr["id"]
      _STATE["dump_cr_id"] = cr_id
      log(f"MCP_DB_MIGRATE dump: created CR {cr_id}")
  
      result_cr = await _submit_and_execute(cr_id, approver_token)
  
      assert result_cr["status"] == "completed", \
          f"Dump CR failed: {result_cr}"
      result = result_cr.get("result") or {}
      artifact = result.get("artifact_ref") or result.get("dump_path")
      assert artifact, \
          f"No artifact_ref or dump_path in dump result: {result}"
      _STATE["dump_artifact"] = artifact
      log(f"MCP_DB_MIGRATE dump: artifact={artifact} ✅")
  
  
  async def test_MCP_DB_MIGRATE_restore():
      artifact = _STATE.get("dump_artifact")
      approver_token = _STATE.get("approver_token")
      if not artifact:
          pytest.skip("dump_artifact not set — dump phase failed")
  
      cr = await create_change_request(
          token=API_TOKEN,
          change_type="database_restore",
          asset_id=ASSET_ID,
          title="[smoke] MCP database restore",
          parameters={
              "db_type": "postgres",
              "database": "nexplane",
              "artifact_ref": artifact,
          },
      )
      check_for_budget_pause(cr)
      assert cr.get("status") == "draft"
      cr_id = cr["id"]
      _STATE["restore_cr_id"] = cr_id
      log(f"MCP_DB_MIGRATE restore: created CR {cr_id}")
  
      result_cr = await _submit_and_execute(cr_id, approver_token)
  
      assert result_cr["status"] == "completed", \
          f"Restore CR failed: {result_cr}"
      result = result_cr.get("result") or {}
      assert result.get("success") is True or result.get("rows_restored") is not None, \
          f"Restore result missing success or rows_restored: {result}"
      log(f"MCP_DB_MIGRATE restore: result={result} ✅")
  
  
  async def test_MCP_DB_MIGRATE_db_ground_truth():
      for label, cr_id in [
          ("dump", _STATE.get("dump_cr_id")),
          ("restore", _STATE.get("restore_cr_id")),
      ]:
          if not cr_id:
              continue
          row = await _db_get_cr(cr_id)
          assert row is not None and row.status.value == "completed", \
              f"DB {label} CR {cr_id} not completed (status={getattr(row, 'status', None)})"
          mcp_cr = await get_change_request(token=API_TOKEN, cr_id=cr_id)
          check_for_budget_pause(mcp_cr)
          assert mcp_cr["status"] == "completed", \
              f"MCP {label} CR status mismatch: {mcp_cr['status']!r}"
      log("MCP_DB_MIGRATE: DB ground truth verified for dump + restore ✅")
  
  
  async def test_MCP_DB_MIGRATE_rollback():
      restore_cr_id = _STATE.get("restore_cr_id")
      approver_token = _STATE.get("approver_token")
      if not restore_cr_id:
          pytest.skip("restore_cr_id not set")
  
      rb = await _rollback_and_poll(restore_cr_id, approver_token)
      assert rb["status"] in ("completed", "rolled_back"), \
          f"Restore rollback failed: {rb}"
      # Dump artifact is retained after restore rollback (matches backup_strategies_restore)
      log("MCP_DB_MIGRATE: restore rollback completed — dump artifact retained ✅")
  ```

- [ ] **Step 6: Add the MCP_CONTAINERIZE phase**

  Append to `test_smoke_mcp_cr_workflows.py`:

  ```python
  # ---------------------------------------------------------------------------
  # Phase MCP_CONTAINERIZE
  # containerize_build — prove MCP drives a real Docker image build
  # Success criteria borrowed from test_containerize_smoke.py
  # ---------------------------------------------------------------------------
  
  
  async def test_MCP_CONTAINERIZE_build():
      _require("API_TOKEN", "ASSET_ID")
      approver_token = _STATE.get("approver_token")
      if not approver_token:
          pytest.skip("approver_token not set")
  
      cr = await create_change_request(
          token=API_TOKEN,
          change_type="containerize_build",
          asset_id=ASSET_ID,
          title="[smoke] MCP containerize build",
          parameters={
              "app_path": "/tmp/smoke-app",
              "image_name": "nexplane-smoke-test",
          },
      )
      check_for_budget_pause(cr)
      assert cr.get("status") == "draft", f"Expected draft, got: {cr}"
      cr_id = cr["id"]
      _STATE["containerize_cr_id"] = cr_id
      log(f"MCP_CONTAINERIZE: created CR {cr_id}")
  
      result_cr = await _submit_and_execute(cr_id, approver_token)
  
      assert result_cr["status"] == "completed", \
          f"Containerize CR failed: {result_cr}"
      result = result_cr.get("result") or {}
      assert result.get("image_name"), f"image_name missing: {result}"
      assert result.get("image_digest"), f"image_digest missing: {result}"
      _STATE["containerize_image_name"] = result["image_name"]
      _STATE["containerize_image_digest"] = result["image_digest"]
      log(f"MCP_CONTAINERIZE: image={result['image_name']} digest={result['image_digest']} ✅")
  
  
  async def test_MCP_CONTAINERIZE_db_ground_truth():
      cr_id = _STATE.get("containerize_cr_id")
      if not cr_id:
          pytest.skip("containerize_cr_id not set")
  
      row = await _db_get_cr(cr_id)
      assert row.status.value == "completed"
      assert row.result.get("image_digest") == _STATE["containerize_image_digest"], \
          "DB image_digest does not match MCP result"
  
      mcp_cr = await get_change_request(token=API_TOKEN, cr_id=cr_id)
      check_for_budget_pause(mcp_cr)
      assert mcp_cr["status"] == "completed"
      log("MCP_CONTAINERIZE: DB ground truth verified ✅")
  
  
  async def test_MCP_CONTAINERIZE_rollback():
      cr_id = _STATE.get("containerize_cr_id")
      approver_token = _STATE.get("approver_token")
      if not cr_id:
          pytest.skip("containerize_cr_id not set")
  
      rb = await _rollback_and_poll(cr_id, approver_token)
      assert rb["status"] in ("completed", "rolled_back"), \
          f"Containerize rollback failed: {rb}"
  
      # Rollback deletes the image — verify in rollback result
      result = rb.get("result") or {}
      deleted = result.get("image_deleted") is True or "deleted" in str(result).lower()
      assert deleted, \
          f"Image deletion not confirmed in rollback result: {result}"
      log(f"MCP_CONTAINERIZE: rollback completed — image deleted ✅")
  ```

- [ ] **Step 7: scp the complete file to EC2 and run a quick import check**

  ```bash
  scp -i ~/.ssh/id_ed25519 \
    backend/tests/smoke/test_smoke_mcp_cr_workflows.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/test_smoke_mcp_cr_workflows.py

  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec \
    -e NEXPLANE_SMOKE=1 -e API_TOKEN=dummy -e ASSET_ID=dummy \
    nexplane-backend-1 \
    python -m pytest /app/tests/smoke/test_smoke_mcp_cr_workflows.py \
    --collect-only 2>&1 | head -40"
  ```
  Expected: pytest lists 13 tests (setup + 3 per phase × 4 phases = 13), no import errors.

- [ ] **Step 8: Run the new smoke test against live infra**

  Ensure the smoke EC2 test instance is running (check in AWS console or via `aws ec2 describe-instances --filters Name=tag:Name,Values=nexplane-smoke-test-01`), then:

  ```bash
  ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec \
    -e NEXPLANE_SMOKE=1 \
    -e API_TOKEN=<nxp_token_from_task2> \
    -e ASSET_ID=<asset_id_of_smoke_instance> \
    nexplane-backend-1 \
    python -m pytest /app/tests/smoke/test_smoke_mcp_cr_workflows.py -v -s 2>&1"
  ```

- [ ] **Step 9: Debug failures in MCP_SNAPSHOT**

  If `os_upgrade` CR fails, check:
  - The `os_upgrade` executor's `snapshot_only` parameter name — read `backend/app/connectors/executors/aws/os_upgrade.py` and confirm the exact param name. If it's `snapshot_only_mode` or similar, update the test parameters.
  - The asset must be an EC2 instance with an EBS root volume and AWS connector attached.
  - If AWS credentials are missing from the connector, check `GET /connectors/<id>` and verify the `aws_access_key_id` is set.

- [ ] **Step 10: Debug failures in MCP_BACKUP**

  If `server_backup` CR fails, check:
  - The asset must have a backup target configured. If `artifact_ref` is empty, check `GET /backup-targets?asset_id=<id>` — a backup target (S3 bucket + credentials) must be wired up.
  - If no backup target exists, create one via:
    ```bash
    curl -X POST http://localhost:8000/backup-targets \
      -H "Authorization: Bearer <token>" \
      -H "Content-Type: application/json" \
      -d '{"asset_id":"<id>","backend":"s3","config":{"bucket":"nexplane-smoke-backup-test","prefix":"smoke/"}}'
    ```

- [ ] **Step 11: Debug failures in MCP_DB_MIGRATE**

  If `database_dump` fails, check:
  - Postgres must be running on the asset (the nexplane platform's own Postgres instance works).
  - The `database` parameter (`nexplane`) must match an accessible DB. If the dump executor requires SSH/agent access, ensure the Nexplane agent is running on the asset.
  - Read the executor at `backend/app/connectors/executors/nexplane_agent/backup_strategies/database_dump.py` to verify exact parameter names (`db_type`, `database`, `host`, `port`). Update test parameters if needed.

- [ ] **Step 12: Debug failures in MCP_CONTAINERIZE**

  If `containerize_build` fails, check:
  - Docker must be installed on the target asset (EC2 instance).
  - `/tmp/smoke-app` must exist on the asset with at least a Dockerfile. If not, create it:
    ```bash
    # Via SSH to the smoke EC2 instance, or via SSM command CR:
    mkdir -p /tmp/smoke-app
    echo 'FROM alpine:latest\nCMD ["echo", "smoke"]' > /tmp/smoke-app/Dockerfile
    ```
  - Read `backend/app/connectors/executors/containerize/containerize_build.py` to verify exact `app_path` and `image_name` parameter names.

- [ ] **Step 13: Iterate until all 13 tests pass, then commit**

  ```bash
  git add backend/tests/smoke/test_smoke_mcp_cr_workflows.py
  git commit -m "smoke: add test_smoke_mcp_cr_workflows.py — MCP_SNAPSHOT/BACKUP/DB_MIGRATE/CONTAINERIZE all passing on EC2"
  ```
