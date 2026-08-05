# CockroachDB Rolling Upgrade Design

**Goal:** Perform a safe, node-by-node binary upgrade of a CockroachDB cluster, with an explicit finalization gate before issuing `SET CLUSTER SETTING version` to lock in the new cluster version.

**Architecture:** CRDB natively supports mixed-version operation, so each node can be drained, upgraded, and restarted independently while the cluster continues serving traffic. The executor drains leases off each node before stopping it, polls the node status API for rejoin confirmation, and proceeds to the next node only after the upgraded node is fully live and available. Version finalization is a hard gate requiring explicit operator re-confirmation — once issued, the cluster setting cannot be rolled back, and the executor prevents rollback registration for that step.

## Phases

1. **Preflight** — Run `cockroach node status --all` and assert every node has `is_live=true` and `is_available=true`. Retrieve current cluster version via `SHOW CLUSTER SETTING version` and confirm `target_version` is exactly one minor version ahead (CRDB does not support skipping minor versions). Check for running schema changes via `SHOW JOBS WHERE job_type = 'SCHEMA CHANGE' AND status = 'running'` — any active schema change must complete before proceeding. Check per-node disk headroom (upgrade staging requires free space). Verify replication factor via `SHOW ZONE CONFIGURATION FOR RANGE default` — RF≥3 is required for safe rolling restarts. If `backup_before_upgrade: true` and a backup connector is configured, trigger a BACKUP CR and wait for completion before proceeding.

2. **Snapshot** — Record full `cockroach node status --all` output, current `SHOW CLUSTER SETTING version` value, and a `SHOW RANGES` summary. Store node-level binary versions and SSH host mappings for each node as rollback state on the FILO stack. If a backup was triggered in preflight, record the backup job ID and destination in the snapshot.

3. **Upgrade** — Iterate over nodes in order: (1) drain the node gracefully via `cockroach node drain <node-id> --host=<addr>` with up to `drain_timeout_seconds` — this migrates range leases and active connections off the node; (2) stop the service via `systemctl stop cockroachdb` over SSH; (3) swap the binary using `package_manager` (apt, yum, or direct download from `binary_url`); (4) start the service via `systemctl start cockroachdb`; (5) poll `cockroach node status <node-id>` until `is_live=true`; (6) poll until `is_available=true` before advancing to the next node, up to `node_rejoin_timeout_seconds`. After all nodes are running the new binary, the cluster operates in mixed-version mode — safe for reads and writes but new features are gated. Executor pauses and requires `finalize: true` to be set (or a second approval action) before issuing `cockroach sql -e "SET CLUSTER SETTING version = '<target_version>'"`. If `dry_run: true`, all steps are logged but no SSH or SQL write commands are executed.

4. **Verify** — After finalization: confirm `SHOW CLUSTER SETTING version` matches `target_version`. Run `cockroach node status --all` and assert all nodes are live and available. Check `SHOW JOBS` for any auto-migration jobs triggered by finalization and wait for them to complete. Run `cockroach doctor` if available on the target version. Log all results into the CR execution record.

5. **Rollback** — Before finalization: for each node that was upgraded (in reverse FILO order), stop the service, swap back the original binary or package version, start the service, and poll for `is_live=true` and `is_available=true`. The cluster re-enters mixed-version mode during rollback and exits it cleanly once all nodes are back on the original binary. After `SET CLUSTER SETTING version` is issued: rollback is **IRREVERSIBLE** — CRDB cluster settings cannot be decremented. The executor removes the finalization step from the FILO rollback stack immediately upon execution and surfaces a hard error if platform-level rollback is attempted after that point.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target CRDB version, e.g. `"23.2.4"` |
| `nodes` | list[dict] | yes | — | List of `{node_id, host, port, ssh_host}` objects for each cluster node |
| `sql_host` | string | yes | — | Host used for SQL and admin CLI connections |
| `sql_port` | int | no | `26257` | SQL wire protocol port |
| `http_port` | int | no | `8080` | Admin HTTP port used for health checks |
| `package_manager` | string | no | `"apt"` | Binary upgrade method: `"apt"`, `"yum"`, or `"binary"` |
| `binary_url` | string | no | — | Direct download URL for the new `cockroach` binary (used when `package_manager` is `"binary"`) |
| `drain_timeout_seconds` | int | no | `300` | Per-node drain timeout before forcing stop |
| `node_rejoin_timeout_seconds` | int | no | `300` | Per-node timeout waiting for `is_live=true` and `is_available=true` after restart |
| `finalize` | bool | no | `false` | Must be explicitly `true` to execute `SET CLUSTER SETTING version`; executor blocks until set |
| `backup_before_upgrade` | bool | no | `true` | Trigger a BACKUP CR before upgrade if a backup connector is configured |
| `dry_run` | bool | no | `false` | Log all actions without executing SSH or SQL writes |

## Rollback Capability

**PARTIAL.** The upgrade is fully reversible before version finalization — each node can be downgraded in FILO order by swapping back the original binary and restarting. The cluster safely tolerates mixed-version mode during rollback. After `SET CLUSTER SETTING version` is issued, rollback is **IRREVERSIBLE**: CRDB does not permit cluster setting version rollback. The executor enforces this by (a) requiring explicit `finalize: true`, (b) displaying a hard warning before execution, and (c) immediately deregistering the finalization step from the FILO rollback stack so platform-level rollback is rejected with a clear error rather than attempting an impossible undo.

## Smoke Test Requirements

A 3-node CockroachDB cluster is required. The recommended setup is three `cockroach start` processes on the EC2 runner in a tmux session or via Docker Compose, initialized with `cockroach init`, running version 23.1. The smoke must exercise: preflight checks (including schema change guard), per-node drain and binary swap, the `finalize` gate (first run with `false` to confirm it blocks, then `true` to confirm it executes), and full post-finalization verification. A rollback phase must also run — before finalization — confirming all three nodes return to their original binary with `is_live=true`. The backup preflight path should be exercised if an S3 backup connector is available in the smoke environment.

## Key Risks

- **Drain stall blocks rolling restart:** If a node holds range leases that cannot be transferred (e.g., under-replicated ranges), `cockroach node drain` will hang until `drain_timeout_seconds` is exceeded. Mitigated by verifying replication factor (RF≥3) in preflight and surfacing drain timeouts as CR failures with the affected node ID and suggested remediation.
- **Schema change collision causes data inconsistency:** Running a schema change during a rolling upgrade can leave the cluster in an inconsistent state if the schema change spans version boundaries. Mitigated by the preflight `SHOW JOBS` check — the CR hard-blocks if any schema change job is active, requiring the operator to wait or cancel it before proceeding.
- **Finalization is irreversible and auto-migration jobs may be slow:** After `SET CLUSTER SETTING version`, CRDB runs internal migration jobs that can take minutes to hours on large clusters. Mitigated by the `finalize` hard gate giving operators time to assess readiness, and by the post-finalization verify phase polling `SHOW JOBS` until all migration jobs complete before marking the CR successful.
