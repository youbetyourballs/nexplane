# MongoDB Replica Set Rolling Upgrade Design

**Goal:** Perform a safe, rolling binary upgrade of a MongoDB replica set across all members, with an explicit finalization gate before bumping the Feature Compatibility Version.

**Architecture:** Secondaries are upgraded one at a time before stepping down the primary, ensuring the replica set remains available throughout. The executor polls `rs.status()` after each member restart to confirm re-convergence before proceeding. FCV finalization is a hard gate requiring explicit operator re-confirmation — it is the point of no return and is treated as a second approval step within the CR lifecycle.

## Phases

1. **Preflight** — Connect via pymongo and verify all replica set members report `stateStr` of `PRIMARY` or `SECONDARY` (no `RECOVERING`, `UNKNOWN`, or `ROLLBACK`). Confirm current FCV via `db.adminCommand({getParameter:1, featureCompatibilityVersion:1})`. Validate that `target_version` is either a same-major patch or exactly +1 major version. Check oplog window via `db.getSiblingDB('local').oplog.rs.stats().maxSize` — must exceed 24 hours to guarantee a secondary can safely fall behind during restart. Capture full `rs.conf()` and per-member `buildInfo` versions.

2. **Snapshot** — Record `rs.conf()`, `rs.status()` output, current FCV string, and binary version of each member. Store as executor state keyed to this CR's rollback stack entry. Confirm oplog retention is sufficient for worst-case restart duration. No external backup is triggered (oplog retention is the rollback vehicle for pre-FCV rollback).

3. **Upgrade** — Iterate over secondaries in order: (1) stop `mongod` via `systemctl stop mongod` over SSH; (2) swap binary using configured `package_manager` (apt, yum, or binary swap to `binary_path`); (3) start `mongod` via `systemctl start mongod`; (4) poll `rs.status()` until the member's `stateStr == "SECONDARY"` and `health == 1`, up to `member_rejoin_timeout_seconds`; (5) verify oplog sync — member `optimeDate` must be within 10 seconds of primary before proceeding to the next node. After all secondaries are upgraded: issue `rs.stepDown(stepdown_timeout_seconds)`, wait for a new primary to be elected, then upgrade the former primary as a trailing secondary using the same per-node sequence. Once all members are on the target binary, executor pauses and requires `finalize_fcv: true` to be set (or a second approval action) before issuing `db.adminCommand({setFeatureCompatibilityVersion: "X.Y"})`. If `dry_run: true`, all steps are logged but no SSH or pymongo write commands are executed.

4. **Verify** — After FCV bump: confirm `rs.status()` shows all members healthy, run `db.adminCommand({buildInfo:1}).version` on each member and assert it matches `target_version`, and confirm `getParameter featureCompatibilityVersion` matches the target major. Log all results into the CR execution record.

5. **Rollback** — Before FCV bump: for each member that was upgraded (in reverse order per FILO stack), stop `mongod`, swap back the original binary, start `mongod`, and wait for `SECONDARY` state. Re-check `rs.status()` for full convergence. After FCV bump: rollback is **IRREVERSIBLE** — MongoDB cannot downgrade FCV once finalized. Executor surfaces a hard warning at the finalization gate and prevents rollback registration after FCV is bumped.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target mongod version, e.g. `"7.0.5"` |
| `replica_set_name` | string | yes | — | Name of the replica set |
| `members` | list[string] | yes | — | `host:port` list for all replica set members |
| `admin_user` | string | no | `"admin"` | MongoDB admin username |
| `admin_password` | string | no | — | Admin password; sourced from connector credentials if omitted |
| `auth_db` | string | no | `"admin"` | Authentication database |
| `package_manager` | string | no | `"apt"` | Binary upgrade method: `"apt"`, `"yum"`, or `"binary"` |
| `binary_path` | string | no | — | Path to pre-staged new `mongod` binary (used when `package_manager` is `"binary"`) |
| `stepdown_timeout_seconds` | int | no | `60` | Timeout for `rs.stepDown()` call |
| `member_rejoin_timeout_seconds` | int | no | `300` | Per-member timeout waiting for `SECONDARY` state after restart |
| `finalize_fcv` | bool | no | `false` | Must be explicitly `true` to execute `setFeatureCompatibilityVersion`; executor blocks until set |
| `dry_run` | bool | no | `false` | Log all actions without executing SSH or pymongo writes |

## Rollback Capability

**PARTIAL.** The upgrade is fully reversible before FCV finalization — each node can be downgraded in FILO order using the snapshotted binary version. After `setFeatureCompatibilityVersion` is issued, rollback is **IRREVERSIBLE**: MongoDB does not support FCV downgrade. The executor enforces this by (a) requiring an explicit `finalize_fcv: true` parameter, (b) displaying a hard warning that rollback will be impossible, and (c) removing the FCV step from the FILO rollback stack once it executes, so platform-level rollback attempts are rejected with a clear error rather than silently failing.

## Smoke Test Requirements

A 3-node MongoDB replica set is required. The recommended setup is a Docker Compose file on the EC2 runner with three `mongod` containers (e.g., mongo:6.0 → mongo:7.0 image swap), configured as a replica set via `rs.initiate()`. The smoke must exercise: preflight checks, secondary-by-secondary upgrade, stepdown and primary re-election, the `finalize_fcv` gate (first run with `false` to confirm it blocks, then `true` to confirm it proceeds), and FCV verification. A rollback phase must also run — before FCV bump — confirming all three nodes return to their original binary and `SECONDARY` state.

## Key Risks

- **Oplog window exhaustion during slow restart:** If a member takes longer than the oplog retention window to rejoin, it cannot sync and requires a full initial sync. Mitigated by checking oplog window in preflight (>24h required) and by per-member rejoin timeouts that abort the CR if a node doesn't recover in time.
- **Stepdown stall blocks upgrade:** If `rs.stepDown()` fails because no secondary is caught up, the primary stays primary and the upgrade hangs. Mitigated by verifying oplog sync (within 10 seconds) before attempting stepdown and surfacing the error with clear remediation instructions.
- **FCV bump is irreversible:** Accidentally finalizing FCV before confirming all application compatibility leaves no rollback path. Mitigated by the `finalize_fcv` hard gate, explicit operator re-confirmation requirement, and a pre-finalization warning logged to the CR audit trail.
