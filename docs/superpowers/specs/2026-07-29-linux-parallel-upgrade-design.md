# Linux Parallel Upgrade — Design Spec

## Goal

A `linux_parallel_upgrade` CR type that migrates a Linux host to a new OS version by provisioning a new host in parallel, syncing data, swapping traffic, and decommissioning the old host. Safer than in-place upgrades for major version jumps and ransomware recovery scenarios because rollback is always available until the old host is terminated.

Scope: AWS EC2 and agent-reachable on-prem/VM hosts. Service migration (enumerating and reinstalling services) is a prerequisite handled by a separate `linux_service_migration` CR. This CR handles data movement, traffic cutover, and decommission only.

## Architecture

```
Phase 1 — Preflight
Phase 2 — Snapshot source
Phase 3 — Sync data
Phase 4 — Verify dest health
Phase 5 — Cutover
Phase 6 — Hold / schedule decommission
```

The CR takes two asset IDs: `source_asset_id` (old host, currently serving traffic) and `dest_asset_id` (new host, already provisioned with agent installed and application configured — prerequisite). Both hosts must have reachable Nexplane agents throughout execution.

Composability: `linux_service_migration` runs before this CR in a project. `linux_parallel_upgrade` assumes the dest host is fully configured and ready to serve traffic before Phase 4.

## Parameters

```python
{
  "source_asset_id": str,           # required — old host asset ID
  "dest_asset_id": str,             # required — new host asset ID (agent installed, app configured)
  "sync_paths": [str],              # required — paths to rsync, e.g. ["/var/lib/app", "/home", "/etc/app"]
  "sync_exclude": [str],            # optional — rsync excludes, e.g. ["*.log", "*.tmp"]
  "pre_sync_runs": int,             # optional, default 1 — run sync N times before cutover to drain delta
  "health_check_command": str,      # optional — shell command run on dest to verify app health, e.g. "curl -sf http://localhost/health"
  "health_check_ports": [int],      # optional — TCP ports that must be listening on dest before cutover
  "cutover_method": str,            # required — "eip" | "alb" | "dns" | "static_ip"
  "cutover_config": dict,           # required — method-specific config (see Cutover section)
  "decommission_after_hours": int,  # optional, default 24 — 0 = manual decommission only
  "dry_run": bool,                  # optional, default false — preflight + sync report only, no cutover
}
```

## Phase Details

### Phase 1 — Preflight

- Verify source agent reachable and reporting healthy
- Verify dest agent reachable and reporting healthy
- Verify dest OS version > source OS version (fail if dest is older or same major version)
- Verify each path in `sync_paths` exists on source
- Verify cutover method is available (EIP exists and is associated to source, target group contains source, DNS record resolves to source, interface exists)
- Verify dest has sufficient disk space for `sync_paths` (estimate from source via `du -sh`)
- If `dry_run=true`: return preflight report and disk estimate, stop here

### Phase 2 — Snapshot source

EC2 hosts: stop source instance → take EBS snapshot of root volume → restart source instance → wait for agent heartbeat. Uses the shared `_take_snapshot` / `_restore_snapshot` helpers extracted from `os_upgrade.py`.

On-prem hosts: skip snapshot, record `snapshot_skipped=True` in execution result with a warning. Rollback after cutover will still work (source is stopped but not terminated); only decommission becomes irreversible without a snapshot.

Snapshot ID (or `None`) stored in execution result for rollback use.

### Phase 3 — Sync data

Runs `pre_sync_runs` times. Each run dispatches `rsync_paths` agent job on the source agent:
- rsync `sync_paths` to dest over SSH using dest agent's SSH key
- Excludes `sync_exclude` patterns
- Uses `--checksum` and `--delete` flags for accuracy
- Reports bytes transferred and file count per run

For `pre_sync_runs > 1`: first N-1 runs happen during Phase 3. The final sync run happens at the start of Phase 5 (immediately before cutover) to minimize the delta window.

### Phase 4 — Verify dest health

Run health checks on dest before committing to cutover:

1. **Port probes**: for each port in `health_check_ports`, TCP connect from platform to dest. Fail if any port unreachable after 30s retry window.
2. **Health check command**: if `health_check_command` provided, dispatch as agent job on dest. Fail if exit code non-zero or timeout (60s).
3. **Agent heartbeat**: confirm dest agent is still alive after health commands ran.

Hard fail if any check fails — do not proceed to cutover. Operator must fix dest and re-run CR.

### Phase 5 — Cutover

1. Run final rsync (the Nth run if `pre_sync_runs > 1`) to capture any writes since Phase 3
2. Stop source instance (EC2: `stop_instances`; on-prem: `dispatch_agent_job("shutdown")`)
3. Execute traffic swap via `cutover_method` (see Cutover Methods below)
4. Record `cutover_completed=True`, `source_stopped=True` in execution result

### Phase 6 — Hold / schedule decommission

If `decommission_after_hours > 0`: create a `RecurringJob` (single-fire, not recurring) to execute `terminate_source` after the configured window. Record `decommission_job_id` in execution result.

If `decommission_after_hours = 0`: record `decommission_manual=True`. Operator decommissions via a separate CR or manual action.

Once decommission fires (or is manually triggered): terminate source instance (EC2: `terminate_instances`; on-prem: agent shutdown + asset deactivation). If `snapshot_id` is not `None`, delete the EBS snapshot. `ROLLBACK_CAPABILITY` becomes `"irreversible"` after this point.

## Cutover Methods

### `eip` (AWS EC2 only)
Config: `{"eip_allocation_id": str}`

Forward: disassociate EIP from source → associate to dest
Rollback: disassociate from dest → associate back to source

### `alb`
Config: `{"target_group_arn": str, "region": str}`

Forward: register dest in target group → wait for InService → deregister source
Rollback: register source → wait for InService → deregister dest

### `dns`
Config: `{"hosted_zone_id": str, "record_name": str, "record_type": str, "ttl": int}`

Forward: update A/CNAME record to dest IP; lower TTL to 60s before cutover if `ttl > 60`
Rollback: update record back to source IP

### `static_ip`
Config: `{"interface": str, "ip": str, "netmask": str, "gateway": str}`

Forward: `dispatch_agent_job("configure_static_ip", ...)` on dest → `dispatch_agent_job("remove_static_ip", ...)` on source
Rollback: `dispatch_agent_job("configure_static_ip", ...)` on source → `dispatch_agent_job("remove_static_ip", ...)` on dest

## Rollback

| State | Rollback action | Capability |
|-------|----------------|------------|
| Phases 1–4 (pre-cutover) | Abort; source still serving; cancel snapshot if taken | `full` |
| Phase 5–6, source stopped, not decommissioned | Reverse cutover; restart source; stop dest | `full` |
| After decommission | Source terminated; snapshot ARN recorded for manual restore | `irreversible` |

Execution result checkpoint fields used by rollback:
- `snapshot_id` — EBS snapshot ARN (or `None`)
- `cutover_completed` — bool; if False, no traffic swap to reverse
- `source_stopped` — bool; if True, restart source on rollback
- `decommission_job_id` — RecurringJob ID to cancel if rollback called before decommission fires
- `cutover_method` and `cutover_config` — copied from parameters for rollback routing

## Files

| File | Change |
|------|--------|
| `backend/app/connectors/executors/nexplane_agent/linux_parallel_upgrade.py` | New — main executor (phases 1–6) |
| `backend/app/connectors/executors/nexplane_agent/_snapshot_helpers.py` | New — extract `_take_snapshot` / `_restore_snapshot` from `os_upgrade.py`; both executors import from here |
| `backend/app/connectors/executors/nexplane_agent/os_upgrade.py` | Modify — import snapshot helpers instead of inline functions |
| `backend/app/connectors/change_type_definitions/linux_parallel_upgrade.json` | New — catalog definition |
| `backend/app/tests/test_linux_parallel_upgrade.py` | New — unit tests |
| `backend/tests/smoke/test_linux_parallel_upgrade_smoke.py` | New — live smoke test |

## Agent Command

New agent command `rsync_paths` dispatched by Phase 3:

```go
// Parameters:
// - dest_host: string — dest agent IP
// - dest_ssh_key: string — dest agent private key (base64)
// - paths: []string — paths to sync
// - excludes: []string — rsync exclude patterns
// - delete: bool — use --delete flag

// Executes: rsync -az --checksum --delete [excludes] [paths] user@dest_host:[dest_paths]
// Returns: {"bytes_transferred": int, "files_transferred": int, "duration_seconds": float}
```

## Unit Tests

`test_linux_parallel_upgrade.py`:

1. `test_preflight_fails_if_dest_os_older` — dest OS == source OS fails preflight
2. `test_preflight_fails_if_agent_unreachable` — mocked agent timeout fails preflight
3. `test_dry_run_stops_after_preflight` — dry_run=True returns report, no snapshot or sync
4. `test_pre_sync_runs_respected` — pre_sync_runs=3 dispatches rsync 3 times (2 in phase 3, 1 in phase 5)
5. `test_health_check_port_failure_blocks_cutover` — port probe failure returns error, no cutover
6. `test_health_check_command_failure_blocks_cutover` — non-zero exit blocks cutover
7. `test_cutover_records_checkpoint` — execution result contains all rollback fields after phase 5
8. `test_rollback_pre_cutover_no_traffic_change` — rollback before phase 5 does not call cutover reversal
9. `test_rollback_post_cutover_reverses_traffic` — rollback after phase 5 calls reverse cutover and restarts source
10. `test_decommission_job_scheduled` — decommission_after_hours=24 creates RecurringJob
11. `test_manual_decommission_no_job` — decommission_after_hours=0 records decommission_manual=True, no job

## Smoke Test

`test_linux_parallel_upgrade_smoke.py`:

**Phase 1 — Provision source (Ubuntu 20.04)**
Launch EC2 from AMI cache (Ubuntu 20.04 + Nexplane agent). Write test data: `echo "hello-from-source" > /var/lib/testapp/data.txt`. Associate a test EIP to source.

**Phase 2 — Provision dest (Ubuntu 22.04)**
Launch EC2 from AMI cache (Ubuntu 22.04 + Nexplane agent). Confirm OS version > source. Install test app (`nc -l 8080` as a simple health check target).

**Phase 3 — Execute CR through sync**
Run CR with `dry_run=False`, `cutover_method="eip"`, `sync_paths=["/var/lib/testapp"]`, `health_check_ports=[8080]`, `decommission_after_hours=0`.
Assert: `/var/lib/testapp/data.txt` on dest contains `"hello-from-source"`.

**Phase 4 — Cutover**
Assert: EIP now associated to dest instance. Assert: source instance stopped.

**Phase 5 — Rollback**
Trigger rollback. Assert: EIP reassociated to source. Assert: source instance running. Assert: dest instance stopped.

**Phase 6 — Teardown**
Terminate both instances. Release EIP. Delete EBS snapshot.

Source AMI: Ubuntu 20.04 with agent (separate from DC AMI — new smoke AMI key `/nexplane/smoke-amis/ubuntu-20-agent/latest`).
Dest AMI: Ubuntu 22.04 with agent (`/nexplane/smoke-amis/ubuntu-22-agent/latest`).

## Global Constraints

- Dest OS version must be strictly greater than source OS version; preflight fails otherwise
- Rollback must be possible at any point before decommission fires
- `ROLLBACK_CAPABILITY = "full"` until decommission; `"irreversible"` after
- Phase 5 final rsync runs before source is stopped — source must be stopped before traffic is swapped
- `decommission_after_hours=0` means manual only; no RecurringJob is created
- Snapshot helpers extracted from `os_upgrade.py` must not change `os_upgrade.py` behavior
- All agent jobs use `dispatch_agent_job()` from `_dispatch.py` — no direct SSH from executor
- On-prem hosts: snapshot is skipped with a warning, not a failure; rollback still works via source restart
