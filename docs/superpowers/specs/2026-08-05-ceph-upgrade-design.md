# Ceph Rolling Upgrade Design

**Goal:** Orchestrate a zero-data-loss rolling upgrade of a cephadm-managed Ceph cluster by wrapping the native `ceph orch upgrade` lifecycle with pre/post health gating and full rollback via image repin.

**Architecture:** The CR delegates the daemon upgrade sequence to cephadm (which enforces MONs → MGRs → OSDs → MDSs → RGWs → RBD mirrors internally) and adds an outer safety envelope: preflight health gating, noout/norebalance flags to suppress rebalancing during the upgrade, a polling watchdog that pauses or aborts on cluster health degradation, and rollback by restarting the upgrade with the previous container image. The executor is Python async and polls `ceph orch upgrade status` every 30 seconds, emitting structured progress events to the CR log throughout.

## Phases

1. **Preflight** — Verify `ceph -s` reports HEALTH_OK (abort on HEALTH_WARN or HEALTH_ERR); confirm all OSDs are up and in via `ceph osd stat`; check `ceph pg stat` for no degraded or undersized PGs; assert used capacity < 80% via `ceph df` to ensure headroom for OSD rebalancing; verify full MON quorum via `ceph mon stat`; confirm cephadm is managing the cluster via `ceph orch status`; record current daemon version distribution from `ceph versions` and current container image tag from `ceph orch ps` output (these become the rollback anchor).

2. **Snapshot** — Export CRUSH map (`ceph osd crush dump`), auth keyring (`ceph auth export`), OSD map (`ceph osd dump`), and PG dump (`ceph pg dump`) to `/tmp/` with timestamped filenames; record `ceph versions` output; record the current container image tag pulled from `ceph orch ps`. These are stored in the CR execution record and uploaded to the connector's configured backup path.

3. **Upgrade** — Set `noout` and `norebalance` OSD flags to prevent data movement during daemon restarts. Start the upgrade: `ceph orch upgrade start --image <registry>/<repo>:<version>`. Poll `ceph orch upgrade status` every `health_poll_interval_seconds` (default 30s); log current upgrading service and progress percentage. If `ceph -s` shows HEALTH_ERR, halt the polling loop and set CR to `pending_action` state requiring operator acknowledgement before resuming. If `pause_on_health_warn` is true, apply the same pause on HEALTH_WARN. After `ceph orch upgrade status` reports no upgrade in progress, unset `noout` and `norebalance`. Poll `ceph pg stat` until all PGs are active+clean or `pg_clean_timeout_seconds` is exceeded.

4. **Verify** — Confirm `ceph -s` shows HEALTH_OK; run `ceph versions` and assert all daemons report the target version; run `ceph osd stat` to confirm all OSDs are up and in; run `rados bench` write then read against a test pool to verify I/O path is healthy; confirm all daemons are running via `ceph orch ps`.

5. **Rollback** — Issue `ceph orch upgrade stop` to halt the in-progress upgrade. Then issue `ceph orch upgrade start --image <previous-image>` using the image tag recorded during snapshot. cephadm will downgrade any already-upgraded daemons back to the previous image using the same ordered sequence. Unset `noout` and `norebalance` after rollback completes. Poll for PG clean. Note: major version rollback (e.g., Reef → Quincy) may be blocked by on-disk format changes — the CR will warn if the version delta spans a major boundary and require explicit operator confirmation.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target Ceph version, e.g. `"18.2.1"` |
| `target_image` | string | no | constructed from `target_version` | Full container image ref; overrides default Ceph registry if provided |
| `ceph_mon_host` | string | yes | — | Hostname or IP for ceph CLI execution |
| `ssh_host` | string | yes | — | SSH target for remote execution |
| `pg_clean_timeout_seconds` | int | no | `3600` | Max wait for PGs to reach active+clean after upgrade or rollback |
| `health_poll_interval_seconds` | int | no | `30` | Polling interval for upgrade status and cluster health |
| `pause_on_health_warn` | bool | no | `false` | Pause upgrade on HEALTH_WARN; default is to continue |
| `pause_on_health_err` | bool | no | `true` | Always pause on HEALTH_ERR; operator must acknowledge to resume |
| `dry_run` | bool | no | `false` | Run all preflight checks and log upgrade commands without executing them |

## Rollback Capability

**FULL** for minor version upgrades — `ceph orch upgrade start` with the previous image tag rolls back any already-upgraded daemons in the same cephadm-managed sequence. Major version rollback (e.g., Reef → Quincy) is version-specific and may be blocked by on-disk format changes; the CR detects this case, warns at plan time, and requires explicit operator confirmation before allowing the rollback path to proceed.

## Smoke Test Requirements

A 3-node Ceph cluster on EC2 deployed via cephadm, with at minimum one MON, one MGR, and three OSDs. The smoke test upgrades from Quincy (17.x) to Reef (18.x), asserts PG clean is maintained throughout, verifies the `rados bench` I/O check passes post-upgrade, and exercises the rollback path by reissuing the upgrade with the Quincy image and confirming all daemons return to the prior version. AMI cache recommended after initial cephadm deploy due to provisioning time.

## Key Risks

- **OSD rebalancing during daemon restarts:** Mitigated by setting `noout` and `norebalance` before the upgrade starts and unsetting them only after all daemons report the target version. This prevents unnecessary data movement and capacity pressure.
- **Upgrade stall on HEALTH_ERR:** If a daemon fails to start on the new image, cephadm may stall. The CR's polling watchdog detects HEALTH_ERR and pauses the upgrade loop, surfacing the error to the operator via `pending_action` rather than continuing blindly or timing out silently.
- **Major version rollback incompatibility:** On-disk OSD formats and CRUSH map encoding may change across major versions, making downgrade impossible at the storage layer even if cephadm accepts the image repin command. The CR checks the version delta at plan time and blocks major-version rollback unless the operator explicitly overrides.
