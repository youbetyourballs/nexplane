# MCP Server Exhaustive Smoke Verification Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run and extend `test_mcp_server_live.py` to live-verify every MCP tool module against real infrastructure, with DB-verified accuracy for high-stakes memory queries.

**Architecture:** Extend the existing smoke test file with 7 new phases. All phases share the `_invoke_mcp_tool_inprocess` thread-isolation infrastructure and the `_smoke_state` dict that carries the CR/asset created in `MCP_CR_ROUNDTRIP`. The test is self-seeding: it creates a finding and a runbook if none exist, and cleans them up in the `finally` block.

**Tech Stack:** Python 3.12, asyncio, asyncpg, FastMCP, SQLAlchemy 2.x async, httpx, threading (for event-loop isolation)

## Global Constraints

- All assertions must run against live EC2 infrastructure — no mocks, no stubs
- Every tool invocation uses `_invoke_mcp_tool_inprocess` (same auth+DB path as real MCP calls)
- DB-verified phases must query the DB directly (via a fresh `AsyncSessionLocal`) and compare against the MCP response — not trust the MCP response alone
- Host intelligence tools: graceful empty result is acceptable (smoke asset is not agent-enrolled); response structure (correct keys, no exception) must be asserted
- Identity and runbooks: non-error assertion only if the org has no data; test must not fail on empty lists
- `MCP_MEMORY_ACCURACY` requires the CR from `MCP_CR_ROUNDTRIP` — that phase must run first
- EXPECTED_TOOLS set must be expanded to cover all registered tools across all 9 modules
- Cleanup: all seeded findings and runbooks must be deleted in the `finally` block
- Follow existing file conventions: `phase_*` naming, `_smoke_state` for cross-phase state, `fail()` for assertion failures

---

## Current State

The existing file covers 8 phases:

| Phase | Status |
|---|---|
| MCP_TOOL_ENUM | Written, never run live |
| MCP_AGENT_TOKEN_AUTH | Written, never run live |
| MCP_CR_ROUNDTRIP | Written, never run live |
| MCP_PROVENANCE | Written, never run live |
| MCP_WHO_APPROVED | Written, never run live |
| MCP_DEPENDENCY | Written, never run live |
| MCP_SAFETY_QUERY | Written, never run live |
| MCP_TIMELINE | Written, never run live |

**7 new phases to add** covering the remaining ~50 unverified tools:

| New Phase | Tool module(s) | Assertion tier |
|---|---|---|
| MCP_FINDINGS | findings (12 tools) | DB-verified: id/severity/title match DB |
| MCP_CONNECTORS | connectors (5 tools) | DB-verified: id/connector_type match DB |
| MCP_IDENTITY | identity (6 tools) | Grounded non-error |
| MCP_RUNBOOKS | runbooks (4 tools) | DB-verified: id/name match DB |
| MCP_HOST_INTEL | host_intelligence (15 tools) | Grounded: correct structure, graceful empty |
| MCP_PLANNING_CTX | planning_context (8 tools) | DB-verified accuracy: CR from roundtrip in get_asset_history with matching cr_id/change_type/created_at |
| MCP_MEMORY_ACCURACY | cross-module consistency | DB-verified + cross-tool: same CR id consistent across get_asset_history, get_change_request, get_asset_timeline |

---

## Phase Designs

### MCP_FINDINGS

**Tools exercised:** `list_findings`, `get_finding`, `list_finding_change_requests`

**Setup:** Query DB for existing findings in the org. If none, create one via `POST /findings` (title="mcp-smoke-finding", severity="medium", asset_id from smoke_state). Store `seeded_finding_id` in smoke_state for cleanup.

**Assertions:**
1. `list_findings(token, limit=20)` returns a list (may be empty if seeding failed, skip gracefully)
2. `get_finding(token, finding_id)` for the first finding:
   - DB cross-check: query `Finding` by id → assert `mcp_result["id"] == db_finding.id`, `mcp_result["severity"] == db_finding.severity.value`, `mcp_result["title"] == db_finding.title`
3. `list_finding_change_requests(token, finding_id)` returns a list without error

**Cleanup:** `DELETE /findings/{seeded_finding_id}` if seeded.

---

### MCP_CONNECTORS

**Tools exercised:** `list_connectors`, `get_connector`, `list_connector_change_types`

**Setup:** No seeding needed — the org always has connectors from prior smoke runs (AWS connector at minimum).

**Assertions:**
1. `list_connectors(token, limit=20)` returns non-empty list
2. Pick first connector id. `get_connector(token, connector_id)`:
   - DB cross-check: query `Connector` by id → assert `mcp_result["id"] == str(db_connector.id)`, `mcp_result["connector_type"] == db_connector.connector_type.value`
   - Assert credentials are NOT present in the MCP response (security: no credential leakage)
3. `list_connector_change_types(token, connector_id)` returns a list without error

---

### MCP_IDENTITY

**Tools exercised:** `list_identities`, `list_access_reviews`, `get_access_review`

**Assertions (grounded non-error only):**
1. `list_identities(token, limit=20)` returns a list (empty OK) without raising
2. `list_access_reviews(token, limit=10)` returns a list (empty OK) without raising
3. If access reviews non-empty: `get_access_review(token, review_id)` returns a dict with `"id"` key

---

### MCP_RUNBOOKS

**Tools exercised:** `list_runbooks`, `get_runbook`

**Setup:** Query DB for existing runbooks in the org. If none, create one via `POST /runbooks` (name="mcp-smoke-runbook", steps=[{name:"check", action:"shell", command:"echo ok"}]). Store `seeded_runbook_id`.

**Assertions:**
1. `list_runbooks(token, limit=20)` returns non-empty list
2. `get_runbook(token, runbook_id)` for first runbook:
   - DB cross-check: query `Runbook` by id → assert `mcp_result["id"] == str(db_runbook.id)`, `mcp_result["name"] == db_runbook.name`

**Note:** `execute_runbook` is not called live — it triggers real infra actions.

**Cleanup:** `DELETE /runbooks/{seeded_runbook_id}` if seeded.

---

### MCP_HOST_INTEL

**Tools exercised:** All 15 host_intelligence tools

**Setup:** Uses `_smoke_state["asset_id"]` (the mcp-smoke-target asset). This asset has no enrolled agent, so all cache lookups will return empty/default.

**Tools called (all via `_invoke_mcp_tool_inprocess`):**
- `get_kernel_info`, `get_running_processes`, `get_cron_jobs`, `get_local_users`
- `get_installed_packages`, `get_running_services`, `get_open_ports`
- `get_security_posture`, `get_seccomp_policy`, `get_apparmor_profiles`
- `get_selinux_policy`, `get_sudoers`, `get_ssl_certs`, `get_patch_status`
- `get_host_full_context`

**Assertions (per tool):**
- No exception raised
- Return type is `dict` or `list` as per tool signature
- For `get_host_full_context`: result is a dict with at least one of `["kernel", "processes", "services", "open_ports", "security_posture"]` present as a key
- For list-returning tools: result is a `list` (empty list is acceptable)
- For dict-returning tools: result is a `dict` (may have `"cached": false` or similar empty indicator)

---

### MCP_PLANNING_CTX

**Tools exercised:** `get_asset_history`, `get_fleet_context`, `find_similar_assets`, `get_cross_host_dependency_map`, `get_migration_precedents`, `get_kernel_eol_status`, `get_environment_diff`, `get_project_precedents`

**DB-verified assertions:**

**`get_asset_history(token, asset_id, limit=20)`** — core memory accuracy check:
- Result is a list
- The CR created in `MCP_CR_ROUNDTRIP` must appear: search for `cr_id = _smoke_state["cr_id"]` in the list
- DB cross-check: query `ChangeRequest` by id → assert `entry["change_type"] == db_cr.change_type.value`, `entry["status"] == db_cr.status.value`
- Timestamp accuracy: `abs(parse(entry["created_at"]) - db_cr.created_at) < 1 second`

**`get_fleet_context(token, environment="dev", limit=20)`** — grounded non-empty:
- Result is a list or dict with asset data
- At least one returned asset id matches an id in the DB (`SELECT id FROM assets WHERE organization_id = org_id LIMIT 20`)

**`find_similar_assets(token, asset_id)`** — grounded non-error:
- No exception, returns list or dict

**`get_cross_host_dependency_map(token, asset_ids=[asset_id])`** — grounded non-error:
- No exception, returns dict

**`get_migration_precedents(token, change_type="tag_resource")`** — grounded non-error:
- No exception, returns list or dict

**`get_kernel_eol_status(token, asset_id)`** — grounded non-error:
- No exception, returns dict

**`get_environment_diff(token, env_a="dev", env_b="staging")`** — grounded non-error:
- No exception, returns list or dict

**`get_project_precedents(token, project_type="hardening")`** — grounded non-error:
- No exception, returns list or dict

---

### MCP_MEMORY_ACCURACY

**Purpose:** Cross-module consistency check. The same CR id must be reported consistently across three independent MCP tools: `get_asset_history` (planning_context), `get_change_request` (change_requests), and `get_asset_timeline` (assets). All three must agree on `change_type`, `status`, and `created_at`.

**Requires:** `_smoke_state["cr_id"]` and `_smoke_state["asset_id"]` from `MCP_CR_ROUNDTRIP`.

**Sequence:**

1. **DB ground truth:** Query `ChangeRequest` by id directly → capture `db_change_type`, `db_status`, `db_created_at`.

2. **`get_change_request(token, cr_id)`:**
   - Assert `result["id"] == cr_id`
   - Assert `result["change_type"] == db_change_type`
   - Assert `result["status"] == db_status`
   - Assert `abs(parse(result["created_at"]) - db_created_at) < 1s`

3. **`get_asset_history(token, asset_id, limit=50)`:**
   - Find entry where `entry["id"] == cr_id` (or `entry["cr_id"] == cr_id`)
   - Assert `entry["change_type"] == db_change_type`
   - Assert timestamp within 1s of DB

4. **`get_asset_timeline(token, asset_id)`:**
   - Find entry referencing cr_id
   - Assert `change_type` and timestamp consistent with DB

5. **Cross-tool consistency:** The `change_type` value returned by all three tools must be identical (not just each matching DB independently).

6. **Approver consistency:** `get_change_request` result must include approver information. Cross-check against `Approval` table: `approval.approver_id` must match the user id in the MCP response.

---

## EXPECTED_TOOLS Expansion

The `MCP_TOOL_ENUM` phase currently checks 13 tools. Expand to all registered tools across all 9 modules:

```python
EXPECTED_TOOLS = {
    # change_requests (10)
    "list_change_types", "get_change_type", "list_change_requests",
    "get_change_request", "create_change_request", "submit_for_approval",
    "approve_change_request", "execute_change_request",
    "get_execution_progress", "rollback_change_request",
    # assets (6)
    "list_assets", "get_asset", "get_asset_context",
    "list_asset_findings", "get_asset_neighbors", "get_asset_timeline",
    # findings (11)
    "list_findings", "get_finding", "update_finding_status",
    "assign_finding", "accept_risk", "mark_false_positive",
    "trigger_poc_validation", "get_poc_result", "challenge_exploitability",
    "trigger_verification", "get_verification_result",
    "list_finding_change_requests",
    # connectors (5)
    "list_connectors", "get_connector", "test_connector",
    "get_connector_status", "list_connector_change_types",
    # identity (6)
    "list_identities", "get_identity", "list_identity_findings",
    "get_identity_graph", "list_access_reviews", "get_access_review",
    # runbooks (4)
    "list_runbooks", "get_runbook", "execute_runbook",
    "get_runbook_execution_status",
    # host_intelligence (15)
    "get_kernel_info", "get_running_processes", "get_cron_jobs",
    "get_local_users", "get_installed_packages", "get_running_services",
    "get_open_ports", "get_security_posture", "get_seccomp_policy",
    "get_apparmor_profiles", "get_selinux_policy", "get_sudoers",
    "get_ssl_certs", "get_patch_status", "get_host_full_context",
    # planning_context (8)
    "get_asset_history", "get_fleet_context", "find_similar_assets",
    "get_migration_precedents", "get_cross_host_dependency_map",
    "get_kernel_eol_status", "get_environment_diff", "get_project_precedents",
    # provenance/explain (already in existing set)
    "explain_change_request",
}
```

---

## Smoke State Extensions

Add to `_smoke_state`:
```python
_smoke_state = {
    # existing
    "api_token": "",
    "asset_id": "",
    "cr_id": "",
    "approver_id": "",
    "cr_created_at": "",
    "dependent_asset_id": "",
    # new
    "seeded_finding_id": "",     # finding created by MCP_FINDINGS if org had none
    "seeded_runbook_id": "",     # runbook created by MCP_RUNBOOKS if org had none
    "finding_id": "",            # finding used for MCP_FINDINGS assertions
    "connector_id": "",          # connector used for MCP_CONNECTORS assertions
    "runbook_id": "",            # runbook used for MCP_RUNBOOKS assertions
}
```

---

## Run Command

```bash
docker compose exec -T backend python tests/smoke/test_mcp_server_live.py \
    --email admin@acme.example --password admin123 \
    --phases MCP_TOOL_ENUM,MCP_AGENT_TOKEN_AUTH,MCP_CR_ROUNDTRIP,MCP_PROVENANCE,MCP_WHO_APPROVED,MCP_DEPENDENCY,MCP_SAFETY_QUERY,MCP_TIMELINE,MCP_FINDINGS,MCP_CONNECTORS,MCP_IDENTITY,MCP_RUNBOOKS,MCP_HOST_INTEL,MCP_PLANNING_CTX,MCP_MEMORY_ACCURACY
```

---

## Cleanup Contract

The `finally` block must:
1. Delete `seeded_finding_id` if set
2. Delete `seeded_runbook_id` if set
3. Delete the `mcp-smoke-approver-token` API token created during MCP_CR_ROUNDTRIP
4. Delete the `mcp-smoke-test` API token
5. Existing cleanup (smoke asset left in place for reuse)
