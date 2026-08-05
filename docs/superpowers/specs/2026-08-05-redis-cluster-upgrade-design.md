# Redis Cluster Rolling Upgrade Design

**Goal:** Perform a zero-downtime rolling upgrade of a Redis Sentinel or Redis Cluster deployment, upgrading replicas before primaries with automated failover coordination and RDB snapshot-based rollback capability.

**Architecture:** The executor operates node-by-node in rolling fashion — replicas first, primaries last — querying cluster state before each step to ensure health before proceeding. In Sentinel mode the executor triggers an explicit `SENTINEL FAILOVER` to demote the current master before upgrading it. In Cluster mode slot coverage is verified via `redis-cli --cluster check` after each node restart before advancing. Rollback restores RDB snapshots and downgrades packages in reverse node order.

## Phases

1. **Preflight** — Run `redis-cli cluster info` (Cluster mode) or `redis-cli -p <sentinel-port> SENTINEL masters` (Sentinel mode) and assert `cluster_state:ok` with all 16384 slots covered. Verify no nodes are in `FAIL` or `PFAIL` state. Check persistence config: confirm AOF or RDB is enabled on each node via `redis-cli CONFIG GET save` and `redis-cli CONFIG GET appendonly`. Measure replication lag on all replicas via `redis-cli INFO replication | grep master_repl_offset`; abort if any replica exceeds `max_replica_lag_seconds`. Record current Redis version per node via `redis-cli INFO server | grep redis_version`.

2. **Snapshot** — Issue `redis-cli BGSAVE` on each node and poll `redis-cli LASTSAVE` until the timestamp advances (confirming the dump completed). Copy the resulting `dump.rdb` to `rdb_backup_path` with a timestamped filename per node. Save `redis-cli CLUSTER NODES` output to capture the full cluster topology map. In Sentinel mode, record `SENTINEL MASTER <master-name>` output to capture current master identity.

3. **Upgrade** — Replicas first, then primaries. Per node: (1) if replica, verify replication lag is within threshold; (2) stop Redis — `systemctl stop redis` or `docker stop <container>`; (3) upgrade via package manager (`apt-get install -y redis=<target_version>` / `yum install -y redis-<target_version>`) or replace binary from `binary_url`; (4) start Redis — `systemctl start redis` or `docker start <container>`; (5) poll `redis-cli PING` until responsive. For Cluster mode: run `redis-cli --cluster check <host>:<port>` and assert 16384 slots covered before advancing. For Sentinel mode after all replicas are upgraded: run `redis-cli -p <sentinel-port> SENTINEL FAILOVER <sentinel_master_name>` to elect an already-upgraded replica as master, then upgrade the demoted (former) master node last.

4. **Verify** — `redis-cli PING` on every node. For Cluster mode: `redis-cli cluster info | grep cluster_state` must return `ok`; `redis-cli cluster info | grep cluster_slots_ok` must return `16384`. For Sentinel mode: `redis-cli -p <sentinel-port> SENTINEL MASTER <master-name>` confirms a healthy master. Run `redis-cli INFO replication` on each node to confirm replica linkage and zero replication errors.

5. **Rollback** — Working in reverse node order: (1) `systemctl stop redis`; (2) downgrade package to previous version or restore prior binary; (3) restore `dump.rdb` from `rdb_backup_path` to the Redis data directory; (4) `systemctl start redis`; (5) poll `redis-cli PING`. Emit a warning in execution logs if any writes occurred after upgrade — RDB rollback is only lossless if the data directory was not overwritten by a post-upgrade `BGSAVE`. AOF-enabled deployments can recover point-in-time without data loss; RDB-only deployments may lose writes made after the snapshot.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Redis version to install, e.g. `"7.2.4"` |
| `mode` | string | no | `"cluster"` | `"sentinel"` or `"cluster"` |
| `nodes` | list[string] | yes | — | All node addresses as `host:port`, e.g. `["10.0.1.5:6379", "10.0.1.6:6379"]` |
| `sentinel_master_name` | string | no | — | Required when `mode=sentinel`; name from `sentinel.conf` |
| `package_manager` | string | no | `"apt"` | `"apt"`, `"yum"`, or `"binary"` |
| `binary_url` | string | no | — | Download URL for Redis binary; required when `package_manager=binary` |
| `rdb_backup_path` | string | no | `"/var/lib/redis/backup"` | Destination directory for RDB snapshot copies |
| `max_replica_lag_seconds` | int | no | `30` | Abort preflight if any replica lags behind primary by more than this value |
| `dry_run` | bool | no | `false` | Run preflight and snapshot only; skip upgrade and verify |

## Rollback Capability

**PARTIAL.** Rollback is safe and lossless if no writes reached the cluster after the upgrade began, or if AOF (`appendonly yes`) is enabled — in which case the AOF can be replayed to reconstruct state. For RDB-only deployments, any writes committed after the snapshot and before rollback are lost. The executor logs a `ROLLBACK_DATA_LOSS_RISK` warning in the execution result when it detects post-upgrade write activity (by comparing `redis-cli DBSIZE` before and after). Redis does not support loading an RDB file written by a newer version into an older binary, so snapshots must be taken before the upgrade starts — which this CR enforces in Phase 2.

## Smoke Test Requirements

Live Redis Cluster (minimum 3 primaries, 3 replicas) or Redis Sentinel (minimum 1 master, 2 replicas, 3 sentinel processes) running on EC2 nodes reachable from the platform. The smoke must test both upgrade and rollback paths. Rollback smoke must confirm the older `redis_version` is running and data written pre-upgrade is readable post-rollback. Nodes must have `apt` or `yum` access to a Redis package repository, or a `binary_url` pointing to a reachable S3 or internal artifact store.

## Key Risks

- **Cluster split-brain during primary upgrade:** If the primary is stopped before a replica has fully caught up, Cluster mode may mark slots as unavailable. Mitigated by checking replication lag at each step and refusing to proceed if lag exceeds `max_replica_lag_seconds`.
- **RDB format incompatibility on rollback:** Redis RDB versions are forward-only — an older Redis binary cannot load an RDB written by a newer version. Mitigated by capturing RDB snapshots before any node is upgraded and storing them at `rdb_backup_path` before touching binaries.
- **Sentinel failover to an unupgraded replica:** If failover selects a replica that has not yet been upgraded, a mixed-version cluster can form. Mitigated by upgrading all replicas before issuing `SENTINEL FAILOVER`, ensuring the elected master is already at `target_version`.
