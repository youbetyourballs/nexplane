# Cassandra Rolling Upgrade Design

**Goal:** Perform a safe, node-by-node rolling upgrade of an Apache Cassandra cluster, coordinating drain/restart sequencing with schema agreement checks and snapshot-based rollback that becomes irreversible once SSTable rewriting begins.

**Architecture:** The executor upgrades one node at a time using `nodetool drain` to flush memtables and quiesce the node before stopping it, then waits for the node to rejoin the ring and confirm schema agreement before advancing. Keyspace snapshots via `nodetool snapshot` are taken on each node before its upgrade, providing a restore path for the pre-upgrade SSTable format. After all nodes are running the target version, `nodetool upgradesstables` rewrites SSTables into the new format — this step is gated behind an explicit re-confirmation in the approval UI because it marks the point of no return for rollback.

## Phases

1. **Preflight** — Run `nodetool status` on each node; all nodes must be `UN` (Up/Normal). Any `UL`, `DN`, or `DL` node causes an immediate abort. Run `nodetool describecluster` and confirm a single schema version is reported under `Schema versions` — multiple versions indicate an in-progress migration that must resolve first. Run `nodetool gossipinfo` and verify all nodes are visible to each other. Check compaction state via `nodetool compactionstats`; abort if a major compaction is running on any node. If `target_version` crosses the 3.x → 4.x boundary, verify Java 11 is installed (`java -version`) on each node when `java_version` is set; fail early with a human-readable message if the JVM is incompatible.

2. **Snapshot** — For each node: run `nodetool snapshot -t nexplane-pre-upgrade-<timestamp>` for each keyspace in `snapshot_keyspaces` (defaults to all non-system keyspaces). Record the snapshot tag and data directory path (`/var/lib/cassandra/data/<keyspace>/snapshots/<tag>/`) in the CR's execution state. Save `nodetool ring` output to capture current token assignments. Save `nodetool describecluster` output to record schema fingerprint at snapshot time. These artifacts are written to the platform's execution state store so they are available to the rollback executor without SSH access to the node at rollback time.

3. **Upgrade** — Process nodes sequentially. Per node: (1) run `nodetool drain` and wait up to `drain_timeout_seconds` for it to complete — drain flushes memtables to SSTables and stops accepting new writes to this node; (2) `systemctl stop cassandra`; (3) upgrade the package — `apt-get install -y cassandra=<target_version>` or `yum install -y cassandra-<target_version>`; if `java_version` is specified, ensure the correct JVM is active before starting; (4) `systemctl start cassandra`; (5) poll `nodetool status` until the node returns to `UN` within `node_rejoin_timeout_seconds`; (6) run `nodetool describecluster` and assert schema agreement (single version) before proceeding to the next node. If `datacenter` is specified, skip nodes not in that DC. After all nodes are upgraded: if `run_upgradesstables=true`, pause execution and surface a re-confirmation gate in the approval UI with the message: "SSTable rewrite will begin. This makes rollback irreversible. Approve to continue or reject to defer." On approval, run `nodetool upgradesstables` on each node sequentially.

4. **Verify** — `nodetool status` on every node — all must be `UN`. `nodetool describecluster` must report a single schema version. Run `cqlsh <node_ip> -e "SELECT now() FROM system.local"` on each node to confirm CQL connectivity. If `run_upgradesstables` completed, confirm via `nodetool upgradesstables --dry-run` that no legacy-format SSTables remain.

5. **Rollback** — For each node in reverse upgrade order: (1) `nodetool drain`; (2) `systemctl stop cassandra`; (3) downgrade package to the pre-upgrade version; (4) restore snapshot SSTables — copy files from `/var/lib/cassandra/data/<keyspace>/snapshots/<tag>/` back to the keyspace data directory, replacing current SSTables; (5) `systemctl start cassandra`; (6) poll `nodetool status` until node is `UN`. After all nodes are restored, run `nodetool describecluster` to confirm schema agreement. **Rollback is blocked if `upgradesstables` completed** — the executor will surface a hard error in the rollback phase: "SSTable format has been rewritten. Rollback to prior Cassandra version is not supported. Manual intervention required."

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Cassandra version to install, e.g. `"4.1.3"` |
| `nodes` | list[string] | yes | — | Node IPs or hostnames, e.g. `["10.0.2.10", "10.0.2.11"]` |
| `datacenter` | string | no | — | Limit upgrade to nodes in this DC name (from `nodetool status`); upgrades all DCs if omitted |
| `package_manager` | string | no | `"apt"` | `"apt"` or `"yum"` |
| `snapshot_keyspaces` | list[string] | no | all non-system | Keyspaces to snapshot before each node upgrade |
| `java_version` | string | no | — | Required JVM major version, e.g. `"11"`; executor validates and fails fast on mismatch |
| `drain_timeout_seconds` | int | no | `300` | Max time to wait for `nodetool drain` to complete per node |
| `node_rejoin_timeout_seconds` | int | no | `600` | Max time to wait for a node to return to `UN` after restart |
| `run_upgradesstables` | bool | no | `true` | Run `nodetool upgradesstables` after all nodes are upgraded; set `false` to defer the irreversible step |
| `dry_run` | bool | no | `false` | Run preflight and snapshot only; skip upgrade, verify, and upgradesstables |

## Rollback Capability

**PARTIAL, transitioning to IRREVERSIBLE.** The executor tracks a `sstables_rewritten` boolean in the CR's execution state. Before `upgradesstables` runs, rollback is fully supported: snapshots restore each node's SSTables to the pre-upgrade format, and downgrading the Cassandra package returns the node to its prior state. Once `upgradesstables` completes on any node, rollback is blocked for that node — Cassandra 3.x cannot read SSTables written in the 4.x format. The re-confirmation gate before `upgradesstables` is mandatory and non-bypassable; the FILO rollback stack records the `sstables_rewritten` flag so the stack-level unwind can surface the irreversibility to the operator rather than silently failing.

## Smoke Test Requirements

Live Cassandra cluster of at least 3 nodes (RF=3) on EC2 instances reachable from the platform via private IP. The smoke must cover: preflight rejection when a node is `DN`; successful upgrade across all 3 nodes; schema agreement confirmed after each node; `nodetool upgradesstables` completion and re-confirmation gate interaction; and rollback from a state where `upgradesstables` has not yet run. A second smoke scenario should confirm the rollback-blocked error message when rollback is attempted after `upgradesstables`. Nodes must have apt or yum access to the Apache Cassandra repository for the prior and target versions.

## Key Risks

- **Schema disagreement mid-roll:** If a node fails to rejoin after upgrade and the cluster remains in a split-schema state, subsequent nodes cannot be safely upgraded. Mitigated by asserting `nodetool describecluster` shows a single schema version after each node rejoins before advancing — the executor hard-stops and surfaces a `SCHEMA_DISAGREEMENT` error with the differing version fingerprints.
- **Irreversible SSTable rewrite:** `nodetool upgradesstables` rewrites on-disk data in a format incompatible with the prior major version, permanently eliminating the rollback path. Mitigated by making `upgradesstables` a separately gated step with an explicit re-confirmation in the approval UI and a `run_upgradesstables=false` escape hatch that defers the rewrite while leaving the cluster operational on the new binary.
- **Java version mismatch on 3→4 upgrade:** Cassandra 4.x requires Java 11; starting the service with Java 8 causes immediate startup failure and leaves the node down. Mitigated by checking the active JVM during preflight when `java_version` is specified, failing the entire CR before any node is touched rather than discovering the mismatch mid-roll on node 2 or 3.
