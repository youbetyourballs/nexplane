# MinIO Rolling Upgrade Design

**Goal:** Upgrade a distributed MinIO cluster from one release version to the next by replacing the server binary on each node sequentially, without interrupting object storage availability.

**Architecture:** MinIO's erasure-coded distributed mode tolerates node absence during upgrade — the cluster continues serving reads and writes while individual nodes are stopped, updated, and restarted one at a time. The CR uses the MinIO Admin API (`mc admin`) for health checks and admin operations, and SSH for binary replacement on each node. The executor enforces a strict one-node-at-a-time loop and refuses to proceed to the next node until the cluster reports full health.

## Phases

1. **Preflight** — `mc admin info <alias>` to confirm all nodes are online and all drives are healthy (no offline drives); check current version matches expected baseline; run `mc admin heal <alias>` to confirm no healing is in progress; verify erasure set configuration (node count, drives per node, and derived fault tolerance); check object lock and versioning settings that may change behavior across versions; confirm `test_bucket` exists or can be created.

2. **Snapshot** — `mc admin config export <alias>` dumps the full server configuration to a timestamped env file; `mc admin policy list` + `mc admin policy info` exports all IAM policies to JSON; `mc admin user list` and `mc admin group list` record identity configuration; record the MD5 hash and file path of the current MinIO binary on each node via SSH so rollback can verify binary integrity before restoring.

3. **Upgrade** — For each node in sequence: (1) `systemctl stop minio` via SSH; (2) download the target binary from `binary_url`, verify SHA256 checksum against `binary_checksum_url`; (3) replace `/usr/local/bin/minio` and `chmod +x`; (4) `systemctl start minio`; (5) poll `mc admin info` until this node appears online within `node_rejoin_timeout_seconds`; (6) poll `mc admin heal` until no healing is active within `heal_check_timeout_seconds`; (7) proceed to the next node. If `dry_run` is true, all SSH and `mc` commands are logged but not executed.

4. **Verify** — `mc admin info <alias>` shows all nodes online and all drives healthy; all nodes report the target version string; run an end-to-end probe: `mc cp /tmp/nexplane-probe <alias>/<test_bucket>/upgrade-probe`, `mc cat` it back, then `mc rm` it; check Prometheus metrics endpoint for elevated error rates.

5. **Rollback** — For each node (FILO order): SSH to node; `systemctl stop minio`; verify stored MD5 hash against the backup binary path; restore previous binary; `systemctl start minio`; poll until node rejoins cluster. Erasure-coded object data is stored independently of the binary and is never modified by this CR.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | MinIO release tag, e.g. `RELEASE.2024-01-13T07-53-03Z` |
| `alias` | string | yes | — | `mc` alias configured in the MinIO connector |
| `nodes` | list[dict] | yes | — | `[{name, ssh_host}]` matching the cluster node list |
| `binary_url` | string | no | `https://dl.min.io/server/minio/release/linux-amd64/minio` | URL for target binary |
| `binary_checksum_url` | string | no | derived from `binary_url` | SHA256SUMS URL for verification |
| `node_rejoin_timeout_seconds` | int | no | `120` | Max wait for a node to rejoin after restart |
| `heal_check_timeout_seconds` | int | no | `60` | Max wait for heal check to report clean |
| `test_bucket` | string | no | `nexplane-upgrade-probe` | Bucket used for read/write verification |
| `dry_run` | bool | no | `false` | Log all actions without executing |

## Rollback Capability

**FULL.** The MinIO binary is the only artifact changed on each node. Erasure-coded object data is written to drives and is completely independent of the server binary — restoring the previous binary does not affect any stored objects. The CR caches the previous binary path and MD5 hash per node before replacement, enabling verified per-node rollback in reverse order.

## Smoke Test Requirements

A 4-node MinIO distributed cluster provisioned via Docker Compose on an EC2 instance (erasure set size 4, one drive per container). The smoke run upgrades from one pinned RELEASE tag to the next, writes objects before the upgrade begins, reads them back after each node upgrade, and confirms all nodes report the target version at completion. Rollback phase restores the prior binary on all four nodes and confirms the cluster returns to the original version. No AMI caching required — Docker Compose startup is fast enough for direct provisioning.

## Key Risks

- **Checksum mismatch on download.** A corrupted or mismatched binary would prevent the node from starting, leaving the cluster in a degraded state. The CR verifies the SHA256 checksum before replacing the binary and aborts the node upgrade if verification fails, leaving the node on the prior binary.
- **Node fails to rejoin within timeout.** If a node does not come back online after restart (e.g., config incompatibility with new version), the CR halts the rolling loop and triggers rollback for all already-upgraded nodes in FILO order before the cluster loses quorum.
- **Healing active at preflight.** Upgrading while erasure healing is in progress reduces effective fault tolerance and can stall healing indefinitely. The CR refuses to start if `mc admin heal` reports any active healing jobs.
