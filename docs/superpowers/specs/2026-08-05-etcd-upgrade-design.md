# etcd Cluster Rolling Upgrade Design

**Goal:** Upgrade an etcd cluster to a target version one member at a time, maintaining quorum throughout, with snapshot-based rollback capability.

**Architecture:** The executor upgrades cluster members sequentially — followers first, then the leader — pausing after each to confirm health before proceeding. Quorum is never intentionally violated: the executor refuses to touch the next member until the previous one reports healthy. A full snapshot is taken on the leader before any member is touched, providing a restore backstop if data directory corruption occurs.

## Phases

1. **Preflight** — Run `etcdctl endpoint health --cluster` to confirm all endpoints are healthy. Run `etcdctl endpoint status --cluster` to record the current leader, member IDs, raft term, and DB sizes. Verify cluster quorum ((N/2)+1 members healthy). Check running etcd version via `etcdctl version`. Validate target version is at most +1 minor from current (etcd supports one minor upgrade at a time). Confirm the etcd data directory has at least 2x the current DB size free for snapshot storage.

2. **Snapshot** — Run `etcdctl snapshot save /var/lib/etcd/backup/snapshot-<timestamp>.db` on the current leader. Verify integrity with `etcdctl snapshot status`. Record the full member list via `etcdctl member list -w json` and store in CR state for rollback reference.

3. **Upgrade** — Process followers first; move the leader to the end of the queue. For each member: (1) confirm it is not the current leader (`etcdctl endpoint status` isLeader field); (2) stop etcd (`systemctl stop etcd`); (3) download and swap the binary from the configured `binary_url_template`; (4) start etcd (`systemctl start etcd`); (5) poll `etcdctl endpoint health` for this member until healthy within `member_healthy_timeout_seconds`; (6) confirm the member appears in `etcdctl member list` with status "started"; (7) advance to the next member. For the leader: trigger transfer first via `etcdctl move-leader <follower-member-id>`, then apply the same per-member sequence.

4. **Verify** — Run `etcdctl endpoint health --cluster` — all endpoints must report healthy. Run `etcdctl endpoint status --cluster` and confirm a consistent raft term and committed index across members. Write a probe key (`etcdctl put /nexplane/upgrade-probe ok`) and read it back from every endpoint to confirm data replication is functional.

5. **Rollback** — Per-member rollback: stop etcd, swap back the old binary, restart. This is safe because quorum is maintained while any single member is down. Full-cluster rollback (all members regressed): stop all members, run `etcdctl snapshot restore` on each node using the pre-upgrade snapshot to reinitialize the data directory, then restart — this is disruptive and requires a coordinated cluster restart.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target etcd version, e.g. `"3.5.12"` |
| `members` | list[dict] | yes | — | List of `{member_id, name, peer_url, client_url, ssh_host}` |
| `etcdctl_endpoints` | string | yes | — | Comma-separated client endpoint list |
| `etcdctl_cacert` | string | no | — | Path to CA cert for TLS authentication |
| `etcdctl_cert` | string | no | — | Path to client certificate |
| `etcdctl_key` | string | no | — | Path to client private key |
| `binary_url_template` | string | no | GitHub releases URL | Template URL with `{version}` placeholder for binary download |
| `snapshot_path` | string | no | `/var/lib/etcd/backup` | Directory to write the pre-upgrade snapshot |
| `member_healthy_timeout_seconds` | int | no | `120` | How long to poll for member health before failing |
| `dry_run` | bool | no | `false` | Plan and validate without executing any changes |

## Rollback Capability

**FULL** for per-member binary swap — swapping back the old binary while the rest of the cluster holds quorum is safe and non-disruptive. **IRREVERSIBLE** only in the case of data directory corruption, where snapshot restore is available as a backstop but requires stopping all members simultaneously (a cluster restart). The executor flags the snapshot path in the approval summary so operators know the restore artifact exists before approving.

## Smoke Test Requirements

A 3-node etcd cluster on EC2, provisioned with etcd's `--initial-cluster` multi-member configuration (docker-compose or systemd units). Smoke test upgrades from etcd 3.4.x to 3.5.x, exercising: preflight health check, snapshot creation and status verification, per-member sequential upgrade with health polling, probe key write/read across all endpoints, and per-member rollback of the last upgraded node. AMI snapshot the cluster state after initial provisioning to avoid re-bootstrapping on repeated runs.

## Key Risks

- **Split-brain during leader transfer:** If `etcdctl move-leader` succeeds but the new leader is slow to stabilize, the next upgrade step may target a node that briefly believes it is still the leader. The executor validates the new leader's identity in `etcdctl endpoint status` before proceeding, and will not start the old leader's upgrade until the transfer is confirmed.
- **Version skip:** etcd supports only one minor version upgrade at a time (e.g., 3.4 → 3.5, not 3.4 → 3.6). The preflight check enforces this constraint and blocks the CR with an explicit error if a skip is attempted, preventing a situation where downgrade becomes impossible due to an unsupported version gap.
- **Disk exhaustion during snapshot:** On a heavily loaded cluster the DB can be large; if the data directory fills during snapshot, etcd will crash. The preflight disk check (2x DB size free) guards against this, and `dry_run` mode runs all preflight checks without writing anything.
