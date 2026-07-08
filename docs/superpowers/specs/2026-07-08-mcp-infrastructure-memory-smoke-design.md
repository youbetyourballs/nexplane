# MCP Infrastructure Memory Smoke — Design

**Goal:** Live-verify the infrastructure memory layer (REST `/memory/*` endpoints and MCP `planning_context` tools) against real CR history, closing the smoke gap that blocks the impact simulation smoke.

**Architecture:** Four new phases appended to `test_mcp_server_live.py`, sharing `_smoke_state` with the existing 15 phases. Two phases create purpose-built CRs for deterministic verification; two rely on state already populated by earlier phases (`MCP_CR_ROUNDTRIP`, `MCP_DEPENDENCY`). All assert against the platform at `http://172.31.1.233:8000`.

**Tech Stack:** Python, httpx, FastAPI, SQLAlchemy async, existing `NexplaneClient` + `_invoke_mcp_tool_inprocess` helpers

---

## Global Constraints

- All assertions run against live platform — no mocks
- Phases share `_smoke_state` populated by prior MCP phases; must run after `MCP_MEMORY_ACCURACY` in `ALL_PHASES`
- `fail()` on any incorrect ground-truth value (not just missing keys)
- Smoke follows existing file conventions: `phase_*` naming, `fail()` for assertion failures
- No new backend code — all endpoints already exist

---

## What Already Exists (do not re-test)

- `MCP_PLANNING_CTX`: `get_asset_history` structure + basic DB accuracy, `get_fleet_context` non-empty, other planning_context tools structural checks
- `MCP_MEMORY_ACCURACY`: cross-tool consistency, `get_change_request` vs `get_asset_history` agreement on change_type
- `MCP_DEPENDENCY`: creates parent asset + dependent child, stores `_smoke_state["dependent_asset_id"]`

---

## New Phases

### Phase 1: `MCP_INFRA_PROVENANCE`

**What it gates:** `/memory/provenance/{asset_id}` REST endpoint + deep field-level accuracy for MCP `get_asset_history`

**State consumed:** `_smoke_state["asset_id"]`, `_smoke_state["cr_id"]`, `_smoke_state["approver_id"]` (all populated by `MCP_CR_ROUNDTRIP`)

**Assertions:**

REST `GET /memory/provenance/{asset_id}`:
- Response status 200
- `change_history` is a list
- At least one entry where `cr_id == _smoke_state["cr_id"]`
- That entry's `approver_email` is non-empty string

MCP `get_asset_history(asset_id=asset_id, since_days=1)`:
- Returns a list
- Entry matching smoke `cr_id` has:
  - `change_type == "tag_resource"`
  - `status == "completed"`
  - `approver_name` is non-empty string
  - `applied_at` parses as valid ISO 8601 timestamp
  - `rolled_back == False`

Cross-check:
- Fetch approver email from DB in-process: `SELECT email FROM users WHERE id = :approver_id` using the same `_invoke_mcp_tool_inprocess` thread/session pattern as `MCP_MEMORY_ACCURACY`
- Assert `approver_name` from MCP tool matches that email

---

### Phase 2: `MCP_INFRA_TIMELINE`

**What it gates:** `/memory/timeline` REST endpoint + MCP `get_migration_precedents` accuracy against existing history

**Setup:** Record `since_ts = datetime.now(UTC).isoformat()` just before creating 2 purpose-built `tag_resource` CRs (plan → approve → execute both). Store their CR ids as `timeline_cr_ids`.

**Assertions:**

REST `GET /memory/timeline?since={since_ts}`:
- Response status 200
- `total >= 2`
- Both `timeline_cr_ids` appear in `changes` list (match by `cr_id` field)
- `approved_count >= 2`

REST `GET /memory/timeline` (no time filter):
- Response has keys: `total`, `approved_count`, `auto_remediation_count`, `rollback_count`, `changes`
- All count fields are non-negative integers

MCP `get_migration_precedents("tag_resource")`:
- `total_executions > 0` (existing history ensures this)
- `success_rate` is float between 0.0 and 1.0
- `avg_duration_minutes >= 0`
- `sample_cr_ids` is a list

---

### Phase 3: `MCP_INFRA_QUERY`

**What it gates:** `POST /memory/query` NL dispatcher — routing and grounded responses

**State consumed:** `_smoke_state["asset_id"]`, asset name fetched via `GET /assets/{asset_id}`

**Three queries (each asserted independently):**

Query 1 — provenance intent:
- Request: `{"query": "why does asset {asset_name} exist?"}`
- `parse_query_intent` returns `intent == "provenance"`
- REST response: dict, not an error, contains `asset_id` or `change_history` key

Query 2 — timeline intent:
- Request: `{"query": "what changed in us-east-1?"}`
- `parse_query_intent` returns `intent == "timeline"`
- REST response: dict with `total` key, value is non-negative int

Query 3 — approval search intent:
- Request: `{"query": "who approved the tag_resource change?"}`
- `parse_query_intent` returns `intent == "approval_search"`
- REST response: list (may be empty if no CIDR/port entity extracted — acceptable; assert no 5xx)

**Note:** `parse_query_intent` is tested in-process (import and call directly) for intent routing; the REST endpoint is tested for response shape and non-error status.

---

### Phase 4: `MCP_INFRA_DELETION_CHECK`

**What it gates:** `/memory/deletion-check/{asset_id}` and `/memory/dependencies/{asset_id}`

**State consumed:** `_smoke_state["asset_id"]` (parent asset with dependent created by `MCP_DEPENDENCY`), `_smoke_state["dependent_asset_id"]`

**Part A — asset with dependent:**

REST `GET /memory/deletion-check/{asset_id}`:
- Response status 200
- `safe == False`
- `dependent_count >= 1`
- `blocking_reasons` is a non-empty list

REST `GET /memory/dependencies/{asset_id}`:
- Response status 200
- `dependents` is a list
- At least one entry where `asset_id == _smoke_state["dependent_asset_id"]`

**Part B — isolated asset:**

Create a fresh asset with no dependencies (POST `/assets` with type `server`, name `smoke-isolated-{uuid}`).

REST `GET /memory/deletion-check/{isolated_asset_id}`:
- Response status 200
- `dependent_count == 0`

Cleanup: delete the isolated asset after assertions.

---

## Phase Registration

Append to `ALL_PHASES` in `test_mcp_server_live.py`:
```python
"MCP_INFRA_PROVENANCE",
"MCP_INFRA_TIMELINE",
"MCP_INFRA_QUERY",
"MCP_INFRA_DELETION_CHECK",
```

Add to `phase_fns` dispatcher dict:
```python
"MCP_INFRA_PROVENANCE":      lambda: phase_mcp_infra_provenance(client),
"MCP_INFRA_TIMELINE":        lambda: phase_mcp_infra_timeline(client),
"MCP_INFRA_QUERY":           lambda: phase_mcp_infra_query(client),
"MCP_INFRA_DELETION_CHECK":  lambda: phase_mcp_infra_deletion_check(client),
```

No changes to `run_on_ec2.py` routing needed — `MCP_SERVER` phases already dispatch to `test_mcp_server_live.py`.

---

## Error Handling

- `MCP_INFRA_PROVENANCE` hard-fails if the smoke CR is absent from provenance (this is a memory correctness violation, not a timing issue — the CR completed before this phase runs)
- `MCP_INFRA_TIMELINE` hard-fails if `total < 2` after the two purpose-built CRs were executed; soft-warns if `get_migration_precedents` returns zero total_executions (possible on a fresh platform, but not on the live ops instance)
- `MCP_INFRA_QUERY` hard-fails on 5xx; soft-warns if approval_search returns empty (entity extraction may not match the query string)
- `MCP_INFRA_DELETION_CHECK` hard-fails if `safe != False` for the parent asset (dependency was created by `MCP_DEPENDENCY` earlier in the same run — if it's gone, that's a prior phase failure)
- All phases skip gracefully if required `_smoke_state` keys are missing, with a logged warning rather than an exception

---

## Sequence Dependency

```
MCP_CR_ROUNDTRIP          → populates asset_id, cr_id, approver_id
MCP_DEPENDENCY            → populates dependent_asset_id
MCP_MEMORY_ACCURACY       → (existing final accuracy phase)
MCP_INFRA_PROVENANCE      → consumes asset_id, cr_id, approver_id
MCP_INFRA_TIMELINE        → creates own CRs, consumes nothing from prior infra phases
MCP_INFRA_QUERY           → consumes asset_id
MCP_INFRA_DELETION_CHECK  → consumes asset_id, dependent_asset_id
```
