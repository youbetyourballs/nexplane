# MCP Smoke Tests — Design Spec

## Goal

Run the MCP server smoke suite against live infrastructure for the first time, fix all failures, then add four new CR-workflow phases that prove the MCP layer correctly drives and surfaces real domain operations (snapshot, backup, database dump/restore, containerize) end-to-end.

## Background

The platform has 93+ MCP tools across 9 domains. Four smoke files exist covering tool enumeration, auth, CR roundtrip, host intelligence, planning context, and project orchestration — but none have ever run against live infrastructure. A fifth new file will prove that real domain CRs (snapshot, backup, DB dump/restore, containerize) submitted and polled entirely through MCP tools produce results that match executor-level ground truth.

## Architecture

### Run environment

All smoke runs execute from EC2 (`100.101.186.39`) via SSH, either directly in the backend container or via the EC2 Python path. Tests point at `http://localhost:8000` from within the container, or `http://100.101.186.39:8000` from the EC2 host. `NEXPLANE_SMOKE=1` gate is set. No test runs from the Windows laptop.

### AI API budget guard

Several smoke phases trigger AI API calls on the backend (CR planning via the AI manifest, similarity scoring in planning context, impact simulation). The platform is configured with budget caps on both OpenAI and Claude API integrations. The smoke runner must detect budget-exhaustion responses and halt gracefully:

- HTTP 429 responses or error bodies containing `quota`, `budget`, `rate_limit`, or `insufficient_quota` from the backend's AI proxy are treated as a **BUDGET_PAUSE** signal, not a test failure.
- On BUDGET_PAUSE: print a clear message naming the phase, the API that hit the cap, and the run state; exit with a dedicated exit code (2) distinct from test failure (1) so the operator knows to expand the cap before resuming.
- This guard lives in a shared helper in `smoke_helpers.py` and is called by any phase that invokes AI-backed endpoints.

### Existing files — run order

Run sequentially. Each file must be green before starting the next.

1. **`test_mcp_server_live.py`** — broadest gate (21 phases, 93+ tools). Covers: SSE tool enumeration, agent token auth + revocation, full CR lifecycle, provenance, approver grounding, asset dependency graph, safety query, timeline, findings CRUD, connector enumeration + credential safety, identity graph, runbooks, host intelligence, planning context, memory accuracy, infrastructure provenance/timeline/query/deletion-check, impact graph, impact planning.

2. **`test_smoke_mcp_host_intelligence.py`** — requires migration `intel001` and at least one asset with a live Nexplane agent. 4 phases: tool dispatch (16 host tools), cache hit validation (no re-dispatch within 300s TTL), full context composite, cache invalidation + re-dispatch.

3. **`test_smoke_mcp_planning_context.py`** — requires migrations `intel001` + `pc001`. 8 phases: asset history, fleet context, similar assets, migration precedents, cross-host dependency map, kernel EOL status, environment diff, project precedents.

4. **`test_mcp_project_orchestration.py`** — requires migrations `proj001` + `proj002`. 12 phases covering all 15 project orchestration tools (create, list, get, add/remove CR, define/check criteria, status, timeline, risk, reorder, rollback).

### New file: `test_smoke_mcp_cr_workflows.py`

Four phases. Every phase uses only MCP tools — no direct executor calls, no REST endpoints outside the MCP protocol. DB is queried directly after each MCP call as ground truth.

**Pattern per phase:**
1. `create_change_request` (MCP) → assert CR in DB with `status=draft`
2. `approve_change_request` (MCP) → assert `approver_id` in DB matches test user
3. Poll `get_change_request` (MCP) until `status` is terminal (completed/failed), timeout 600s
4. Assert result fields (see per-phase below)
5. Cross-check: MCP-visible `result` and `status` match `change_requests` DB row exactly
6. `rollback_change_request` (MCP) → poll until rollback terminal
7. Assert rollback cleanup (see per-phase below)
8. `finally`: tag-based EC2/artifact cleanup regardless of outcome

#### Phase MCP_SNAPSHOT

CR type: `os_upgrade` with `snapshot_only=true` on the smoke EC2 test instance.

Success assertions (borrowed from `test_os_upgrade_smoke.py`):
- `snapshot_id` present in CR result
- `root_volume_id` present
- `availability_zone` and `region` present
- CR status is `completed`
- EBS snapshot exists in AWS with tag `ManagedBy=nexplane`

Rollback assertions:
- Rollback CR reaches terminal state
- Snapshot is tagged for operator cleanup (not auto-deleted — matches existing OS upgrade behavior)
- `get_change_request` via MCP shows rollback CR linked to parent

#### Phase MCP_BACKUP

CR type: `server_backup` targeting the smoke test instance.

Success assertions (borrowed from `test_backup_scheduler_live.py`):
- `artifact_ref` present in CR result (S3 key or Vault path)
- CR status is `completed`
- S3 object exists at the artifact_ref key (boto3 head_object)
- DB `backup_targets` row references the artifact_ref

Rollback assertions:
- Rollback CR reaches terminal state
- artifact_ref remains in S3 (backup rollback does not delete the artifact — matches existing backup behavior; the artifact is the safety net)

#### Phase MCP_DB_MIGRATE

Two sequential sub-phases: dump then restore.

Sub-phase A — `database_dump`:
- CR targets the smoke Postgres instance on the test EC2 host
- Success: `dump_path` or `artifact_ref` present in result; CR status `completed`
- DB: `change_requests` row has `change_type=database_dump`, status matches

Sub-phase B — `database_restore`:
- Uses artifact_ref from sub-phase A as input
- Success: `rows_restored` count > 0 or `success: true` in result; CR status `completed`
- DB: verify restore CR linked to dump CR via `artifact_ref`

Rollback assertions (borrowed from `test_backup_strategies_restore_live.py`):
- Restore rollback: restore CR rolls back cleanly
- Dump artifact retained (dump rollback does not delete the dump file)

#### Phase MCP_CONTAINERIZE

CR type: `containerize_build` targeting the smoke test instance (must have Docker running).

Success assertions (borrowed from `test_containerize_smoke.py`):
- `image_name` present in CR result
- `image_digest` present
- CR status `completed`

Rollback assertions:
- Rollback CR reaches terminal state
- Image deleted from local Docker registry on the instance (agent confirms via `docker images` output in rollback result)

## Data Flow

```
Test runner (EC2)
  └─ MCP tool call (SSE/HTTP to backend :8000)
       └─ Backend MCP layer
            └─ CR created in DB
            └─ Approval recorded
            └─ Executor dispatched
                 └─ Real infra operation (EBS snapshot / S3 backup / pg_dump / docker build)
            └─ Result written to CR
  └─ Poll MCP get_change_request until terminal
  └─ Assert MCP result fields
  └─ Assert DB ground truth (direct DB query)
  └─ MCP rollback_change_request
  └─ Poll until rollback terminal
  └─ Assert cleanup
  └─ finally: boto3/agent tag-based cleanup
```

## AI Budget Guard

Implemented as `check_for_budget_pause(response)` in `smoke_helpers.py`. Called after any HTTP response from an AI-backed endpoint. Checks:
- HTTP status 429
- Response JSON contains keys: `quota`, `budget`, `rate_limit`, `insufficient_quota`, `context_length_exceeded`

On match: prints phase name, which API hit the cap (OpenAI vs Claude, extracted from error body if available), current test state (which phases passed), then calls `sys.exit(2)`. Exit code 2 is distinct from pytest failure (1) so CI and the operator can distinguish budget pause from test failure.

## Error Handling

- **Timeout on CR poll**: fail the phase with a clear timeout message; `finally` block runs cleanup.
- **Missing migration**: detect via API 404 on migration-gated endpoints; print which migration is needed; skip remaining phases in that file (not a test failure).
- **No live agent on asset**: phases requiring host intelligence skip gracefully with `pytest.skip()` and a message naming the required asset.
- **EC2 instance not in expected state**: pre-flight check at phase start; skip with descriptive message if instance is stopped/terminated.

## Files Changed

| File | Change |
|---|---|
| `backend/tests/smoke/test_mcp_server_live.py` | Fix all failures found during first live run |
| `backend/tests/smoke/test_smoke_mcp_host_intelligence.py` | Fix all failures |
| `backend/tests/smoke/test_smoke_mcp_planning_context.py` | Fix all failures |
| `backend/tests/smoke/test_mcp_project_orchestration.py` | Fix all failures |
| `backend/tests/smoke/test_smoke_mcp_cr_workflows.py` | New file — 4 CR-workflow phases |
| `backend/tests/smoke/smoke_helpers.py` | Add `check_for_budget_pause()` helper |

## Ephemeral Infrastructure for Skipped Phases

Tasks 3 and 6 were delivered in prior session but 17 host-intelligence tests and 13 CR-workflow tests skip due to missing live infra. This addendum specifies spinning up ephemeral EC2 at test start, matching the `test_os_upgrade_smoke.py` / `test_containerize_smoke.py` pattern exactly.

### Pattern (both files)

Each file adds a pytest `setup_class` / `teardown_class` on its test class:

1. **Launch EC2** via `ec2_launch` CR (mode=quick, t3.small, amazon_linux, NexplaneEC2TestProfile). Store `cls.launch_cr_id`.
2. **Wait SSM** — poll `ssm.describe_instance_information` until instance appears (300s timeout).
3. **Deploy agent** via `deploy_nexplane_agent` CR. Store `cls.agent_asset_id`.
4. **Poll asset registration** — search `/assets` by hostname then private IP until `agent_version` is non-null (300s timeout).
5. **Teardown** — `_rollback_cr(cls.launch_cr_id)` in `teardown_class` regardless of test outcome.

All boto3 clients use `get_connector_creds_from_db("aws")` from `smoke_helpers.py`.

### Host Intelligence Additions (`test_smoke_mcp_host_intelligence.py`)

- Add `SmokeMCPHostIntel` class wrapping all existing tests with `setup_class` / `teardown_class` following the pattern above.
- The existing `_require_agent()` helper queries `AgentRegistration` by `asset_id`; it will find the newly registered agent and the 17 skips become live passes.
- No changes to the 4 test phase bodies — only infra setup/teardown added.

### CR Workflow Additions (`test_smoke_mcp_cr_workflows.py`)

Same EC2 launch + agent pattern, plus additional per-file setup:

**AWS connector** — register the platform's existing AWS connector against the new EC2 asset:
- `PUT /assets/{asset_id}/connector` with the AWS connector id (retrieved via `get_connector_creds_from_db`).

**S3 backup storage** — call `_get_or_create_backup_storage_by_type()` (already in `test_backup_strategies_restore_live.py`, copy pattern) to create/reuse a `smoke-s3-nexplane-backups` backup storage record.

**Docker** — install via SSM: `sudo yum install -y docker && sudo systemctl enable --now docker`. Verify with `docker info`. No extra connector needed — agent CRs run commands through the registered Nexplane agent.

**Postgres** — install via SSM: `sudo yum install -y postgresql15-server && sudo postgresql-setup --initdb && sudo systemctl enable --now postgresql`. Create smoke DB: `sudo -u postgres createdb smoke_db`.

**SSH connector** — generate RSA keypair with paramiko, inject public key via SSM, register `ssh` connector in platform against `private_ip:22` as `ec2-user`. Needed by `restore_server` and `agent_backup` executors.

After setup, remove the `pytest.skip()` guards in the 4 phase functions (they currently skip when `asset.connector_id is None` — the new asset has a connector).

### No AMI Caching Needed

`ec2_launch` + SSM wait + `deploy_nexplane_agent` completes in ~3–4 minutes — under the 60s AMI-cache threshold from the memory guidelines. AMI caching is not needed here.

### Files Changed (Addendum)

| File | Change |
|---|---|
| `backend/tests/smoke/test_smoke_mcp_host_intelligence.py` | Wrap in class, add `setup_class`/`teardown_class` with ephemeral EC2 |
| `backend/tests/smoke/test_smoke_mcp_cr_workflows.py` | Add `setup_class`/`teardown_class`, AWS connector attach, S3 storage, Docker+Postgres via SSM, SSH connector, remove skip guards |

## Out of Scope

- New MCP tools (this session tests what exists)
- Database major-version upgrade CR type (backlogged as top priority after this session)
- Changes to executors or catalog JSON (fix MCP smoke only; executor bugs surfaced here go to a separate session)
- Windows / macOS agent-specific phases (tabled until Jamf)
