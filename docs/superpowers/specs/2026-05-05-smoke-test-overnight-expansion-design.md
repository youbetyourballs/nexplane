# Smoke Test Overnight Expansion Design

**Date:** 2026-05-05  
**Status:** Approved for implementation  

---

## Scope

Five independent sub-projects executed overnight in sequence:

1. **CR title standardization** — consistent `[Phase X] description` titles across all smoke test files
2. **Parallel runner** — `test_parallel_live.py` executes AWS/GCP/Azure tracks concurrently via subprocess
3. **Comprehensive AWS coverage** — new Phases U, V, W covering remaining mock-path executors
4. **Agent smoke test expansion** — drive Linux agent commands via Nexplane CRs (not just SSM verification)
5. **Smoke test runner UI** — `SmokeTests.tsx` page with run/stream/history, backed by flat-file API

Multi-cloud consolidation (X1/X2 phases) is **blocked** pending Azure Sub-projects B-G. Not in scope.

---

## Sub-project 1: CR Title Standardization

### Current state
CR titles in the smoke test files are inconsistent:
- Some have phase prefixes: `"Smoke-E: stop instance"`
- Some use generic prefix: `"Smoke: launch EC2"` (no phase letter)
- Agent phases have their own prefix: `"Agent-smoke: audit_linux_patch_status"`

### Target format
All CR titles use the format: `[Phase X] action description`

Examples:
- `"[Phase A] create key pair"` (was `"Smoke: create key pair"`)
- `"[Phase E] stop instance"` (was `"Smoke-E: stop instance"`)
- `"[Phase B-aws-linux] audit_linux_patch_status"` (agent file, cloud-os scoped)

Rules:
- Phase letter from the function that owns the CR (A–T for AWS, L–R for GCP, N–T for Azure)
- Agent phases: `[Phase <group>-<cloud>-<os>]` e.g. `[Phase ossecurity-aws-linux]`
- No `Smoke:` or `Smoke-X:` prefixes — phase bracket is the identifier
- Description is lowercase, imperative, no trailing punctuation

Files to update: `test_aws_live.py`, `test_gcp_live.py`, `test_azure_live.py`, `test_agent_live.py`

---

## Sub-project 2: Parallel Runner

### File
`backend/tests/smoke/test_parallel_live.py`

### Architecture
A coordinator script that runs each provider file as a separate **subprocess** (not thread, to avoid GIL and shared state issues). Each subprocess writes to its own log file; the coordinator tails all logs to stdout with per-provider prefixes in real time.

### CLI
```bash
python backend/tests/smoke/test_parallel_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --tailscale-auth-key tskey-auth-<key> \
  --gcp-project nexplane \
  --azure-resource-group nexplane-smoke-rg \
  --aws-phases A,B,C,D,E,F,G,H,I,K,P,Q,R,T \
  --gcp-phases L,M,N,O,P,Q,R \
  --azure-phases N,O,P,Q,R,S,T
```

### Behavior
1. Spawns three subprocesses simultaneously:
   - `python test_aws_live.py --phases <aws-phases> ...`
   - `python test_gcp_live.py --phases <gcp-phases> --gcp-project ...`
   - `python test_azure_live.py --phases <azure-phases> --azure-resource-group ...`
2. Each subprocess stdout is forwarded to a `threading.Thread` reader that prefixes lines with `[AWS]`, `[GCP]`, `[Azure]` and prints them
3. Coordinator waits for all three to finish (`proc.wait()`)
4. Final summary table shows per-provider exit code + pass/fail + elapsed time:
   ```
   ============================================================
   Parallel Smoke Test Summary
   ============================================================
     AWS   ✅ PASSED  (14m 32s)
     GCP   ✅ PASSED   (8m 14s)
     Azure ❌ FAILED  (11m 07s) — exit code 1
   ============================================================
   Overall: FAILED (one or more providers failed)
   ============================================================
   ```
5. Exits with code 0 only if all three pass

### Isolation
Each provider has its own boto3/GCP/Azure clients initialized from the connector credentials — no shared mutable state between tracks. The existing per-file `main()` functions already handle this correctly.

---

## Sub-project 3: Comprehensive AWS Coverage

### What's missing

Audit of AWS executors with mock paths not yet exercised by any existing phase (A-T):

| Executor | Missing coverage | Proposed phase |
|----------|-----------------|----------------|
| `capture_instance_state` | Only runs as internal step in ec2_stop, never standalone | Phase U |
| `terminate_instance` | Rollback of ec2_launch — exercised indirectly, never via a dedicated smoke CR | Phase U |
| `restore_s3_public_access` | Rollback of block_s3_public_access — not tested standalone | Phase U |
| `restore_security_group` | Rollback of security_group_update — exercised in Phase F rollback ✅ (skip) |
| `delete_route53_zone` | Rollback of route53_zone_create — exercised in Phase I rollback ✅ (skip) |
| `tailscale_remove` | Agent teardown — never tested via CR | Phase V |
| `verify_rds_backup` | Covered in Phase J (slow) — Phase S covers it via different path. Skip standalone. |
| `restore_rds_snapshot` | Requires live RDS — add to Phase J (slow, opt-in) |
| `export_security_group` | Ingest/discovery, not a change executor. Skip. |
| `validate_security_rules` | Internal safety check, not user-invocable. Skip. |
| `health_check` | Internal connector health. Skip. |
| `wait_instance_state` | Internal step executor only. Skip. |
| `block_s3_public_access` | Overlaps Phase H (uses `s3_lifecycle_configure` path). Add restore test. | Phase U |
| `discover_*` executors | Ingest actions, not change CRs. Skip. |

### New phases

**Phase U — Instance state capture + S3 public access restore**

Steps:
1. `capture_instance_state` CR on running EC2 → verify returns instance metadata
2. `block_s3_public_access` CR on a new test bucket → verify via boto3
3. `restore_s3_public_access` CR (rollback) → verify access restored
4. Rollback stack cleanup

Requires Phase A (running EC2 instance).

**Phase V — Tailscale remove**

Steps:
1. `tailscale_remove` CR on the EC2 instance (the one deployed in Phase A)
2. Verify via Tailscale API or SSM that tailscale is no longer active
3. Re-join tailscale via `tailscale_join` CR to restore state for Phase T

Requires Phase A. Must run before Phase T (which uses the agent).

**Phase W — restore_rds_snapshot (add to Phase J)**

Phase J already creates an RDS instance and snapshot. Add a `restore_rds_snapshot` step after the snapshot is created:
1. `restore_rds_snapshot` CR → restores to a new instance identifier
2. Verify the restored instance is available via boto3
3. Delete restored instance via `rds_instance_delete` CR
4. Continue with existing Phase J teardown

Only runs when Phase J is selected (slow, opt-in).

### Phase ordering update in main()

New dependency chain: `A → U → V → T` (U and V require A, V must precede T to restore tailscale)

---

## Sub-project 4: Agent Smoke Test Expansion (Live CRs)

### Current state
`test_agent_live.py` AWS Linux track deploys the agent and verifies each command group by running SSM shell commands that check whether the agent binary's underlying system calls work. It does NOT dispatch commands through the Nexplane control plane agent CRs.

### Target state
After Phase A deploys the agent and it registers as an `endpoint` asset, the test drives **each Linux agent command group via Nexplane change requests** targeting the `endpoint` asset. The CR path exercises the full stack: Nexplane API → planning engine → execution engine → agent HTTP endpoint → agent command → result reporting back to Nexplane.

### Architecture

The existing `_setup_aws_linux_instance()` already waits for the agent to register as an endpoint asset. Extract that endpoint asset ID and pass it to each phase runner.

Each agent command group maps to a nexplane_agent catalog action. The CR `change_type` must exist in the ChangeType enum and CT definition files — create new ones for each group.

### New change types needed

| Group | Commands | change_type |
|-------|----------|-------------|
| `linux_patch` | `audit_linux_patch_status`, `apply_linux_patches` | `agent_linux_patch` |
| `ossecurity` | 12 commands (selinux, seccomp, sysctl, firewall, etc.) | `agent_ossecurity` |
| `linuxauth` | 6 commands (ssh, pam, ntp, ca-certs, users, privesc) | `agent_linuxauth` |
| `crossplatform` | 4 commands (tls, dns, sw-inventory, syslog) | `agent_crossplatform` |
| `compliance` | `audit_cis_compliance`, `collect_evidence` | `agent_compliance` |
| `forensics` | forensics bundle | `agent_forensics` |
| `fleet` | `restart_service`, `push_config_file`, `health_check` | `agent_fleet` |
| `backup` | `create_backup`, `restore_files` | `agent_backup` |
| `reboot` | `graceful_reboot`, `verify_post_reboot` | `agent_reboot` |
| `credrotation` | `update_agent_env_file`, `rotate_ssh_keys` | `agent_credrotation` |
| `iac` | `terraform_plan` | `agent_iac` |
| `linuxupgrade` | `estimate_image_size` | `agent_linuxupgrade` |
| `win_patch` | `audit_windows_patch_status`, `apply_windows_patches` | `agent_win_patch` |
| `winharden` | 11 Windows hardening commands | `agent_winharden` |

### CT definition format

Each `agent_*` change type:
```json
{
  "change_type": "agent_ossecurity",
  "display_name": "Agent: OS Security Hardening",
  "steps": [
    {"generic_action": "audit_os_security_posture", "purpose": "preflight_validate", "required": true},
    {"generic_action": "configure_selinux",          "purpose": "execute",            "required": false},
    {"generic_action": "configure_seccomp",          "purpose": "execute",            "required": false},
    ...
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"]
}
```

### CR dispatch in test_agent_live.py

Replace SSM-based phase runners with CR-based runners when an endpoint asset is available:

```python
def run_ossecurity_aws_cr(client, endpoint_asset_id):
    client.run_cr(
        "[Phase ossecurity-aws-linux] OS security hardening",
        "agent_ossecurity",
        endpoint_asset_id,
        {"dry_run": True},  # audit-only, no destructive changes
    )
```

Fall back to SSM if no endpoint asset (agent didn't register in time).

### Windows agent CRs

The existing AWS Windows track in `test_agent_live.py` uses SSM PowerShell. Add agent CR dispatch for `agent_win_patch` and `agent_winharden` when the Windows endpoint asset registers. Windows agent registration takes longer (5+ min after SSM is ready), so the wait loop is extended.

---

## Sub-project 5: Smoke Test Runner UI

### Pages and routes

- New page: `frontend/src/pages/SmokeTests.tsx`
- New route: `/smoke-tests` in `App.tsx`
- Nav link: Add "Smoke Tests" to the sidebar (same style as Connectors, Assets, etc.)

### Backend API

Three new endpoints in a new router `backend/app/routers/smoke_tests.py`:

**`GET /smoke-tests/suites`**  
Returns available test suites and their last run result:
```json
[
  {
    "id": "aws",
    "name": "AWS",
    "file": "test_aws_live.py",
    "phases": ["A","B","C","D","E","F","G","H","I","K","P","Q","R","T"],
    "last_run": {
      "run_id": "aws-1778035385",
      "status": "passed",
      "phases_passed": 14,
      "phases_total": 14,
      "duration_seconds": 1198,
      "finished_at": "2026-05-05T23:03:05Z"
    }
  },
  {"id": "gcp", ...},
  {"id": "azure", ...},
  {"id": "agent", ...},
  {"id": "parallel", ...}
]
```
Results read from `/tmp/nexplane-smoke-runs/last-<suite>.json` if it exists.

**`POST /smoke-tests/run`**  
Body: `{"suite": "aws", "phases": "A,B,C,D", "extra_args": {...}}`  
Spawns subprocess, returns immediately with `run_id`. Writes PID to `/tmp/nexplane-smoke-runs/<run_id>.pid`. Stdout/stderr streamed to `/tmp/nexplane-smoke-runs/<run_id>.log`. On completion, parses log for per-phase results and writes `last-<suite>.json`.

Only one suite can run at a time per suite ID (check for existing PID file).

**`GET /smoke-tests/logs/{run_id}?offset=0`**  
Returns log content from the given byte offset. Frontend polls this every 2s during a run, sending the last offset to get only new lines. Returns `{"content": "...", "next_offset": 1234, "done": false}`.

### Frontend page — SmokeTests.tsx

Three sections:

**Suite cards** (top)  
One card per suite (AWS, GCP, Azure, Agent, Parallel). Each shows:
- Provider icon (reuse Connectors page icons)
- Last run status badge (✅ Passed / ❌ Failed / — Never run)
- Last run time + duration
- "Run" button (opens config modal)

**Run config modal**  
When user clicks Run:
- Phases selector (checkboxes per phase, default = all non-slow)
- Cloud credentials summary (shows which connectors are configured)
- "Start Run" button

**Live log panel** (bottom, shown during/after a run)  
- Streaming log output with ANSI color (✅ green, ❌ red)
- Per-phase status chips that update as phases complete
- "Stop" button (sends SIGTERM to process)
- "Download log" button

### Log parsing

Parse `✅ Phase X complete` and `❌ Phase X failed` from log output using regex:
- `r"✅ Phase ([A-Z]) complete"` → phase X passed
- `r"❌ Phase ([A-Z]) (failed|—)"` → phase X failed
- `r"✅ ALL SELECTED PHASES PASSED"` → suite passed
- `r"❌ SMOKE TEST FAILED"` → suite failed

### State management

No Redux. Local `useState` + `useEffect` polling. Polling interval: 2s during active run, stops when `done: true` from the log endpoint.

### Credential injection

The backend injects credentials from the configured connectors into the subprocess environment. The smoke tests already read credentials from the Nexplane DB via `_get_aws_boto3_client()` etc. — no change needed. The `--tailscale-auth-key` is the only external param; the run config modal shows a text field for it if the Tailscale connector is configured.

---

## File structure

**New files:**
- `backend/tests/smoke/test_parallel_live.py`
- `backend/app/routers/smoke_tests.py`
- `frontend/src/pages/SmokeTests.tsx`
- `backend/app/connectors/change_type_definitions/agent_linux_patch.json` (and 13 others)

**Modified files:**
- `backend/tests/smoke/test_aws_live.py` — CR title standardization + phases U/V + agent CR dispatch
- `backend/tests/smoke/test_gcp_live.py` — CR title standardization
- `backend/tests/smoke/test_azure_live.py` — CR title standardization
- `backend/tests/smoke/test_agent_live.py` — CR title standardization + live CR dispatch
- `backend/app/models/change_request.py` — add agent_* change types to enum
- `backend/app/services/safety_engine.py` — add agent_* to IMPLICIT_ROLLBACK_TYPES
- `backend/app/main.py` — register smoke_tests router
- `frontend/src/App.tsx` — add /smoke-tests route
- `frontend/src/components/Sidebar.tsx` (or equivalent) — add nav link
- `backend/alembic/versions/027_add_agent_change_types.py`

---

## Out of scope

- Multi-cloud X1/X2 consolidation phases (blocked on Azure Sub-projects B-G)
- GCP/Azure agent CR dispatch (stubs until Sub-projects B-G)
- Phase J/S slow RDS phases (already opt-in, not modified)
- Any new connector implementations
