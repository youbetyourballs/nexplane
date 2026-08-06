# Stateful Datastore Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement upgrade/migration CR types for seven stateful datastores — Redis cluster migration, Cassandra rolling upgrade, MongoDB replica set upgrade, CockroachDB cluster upgrade, etcd upgrade, MinIO distributed upgrade, and Ceph cluster upgrade — all via the nexplane_agent executor pattern with AMI-cached smoke tests.

**Architecture:** Each CR type follows the standard nexplane_agent flow: preflight (detect version, validate upgrade path, check cluster health) → snapshot (service-specific backup) → rolling upgrade (coordinated, service-safe order) → verify (cluster health + data integrity) → rollback (restore from snapshot or reverse upgrade). All executors dispatch agent jobs via `dispatch_agent_job`. Smoke tests provision from cached AMIs, run the full CR lifecycle including rollback, then tear down.

**Tech Stack:** Python asyncio executors, `nexplane_agent._dispatch.dispatch_agent_job`, pytest smoke tests using `NexplaneClient` + `get_connector_creds_from_db`, boto3 for AMI cache, SSM parameter store for AMI cache keys.

## Global Constraints

- All executors live in `backend/app/connectors/executors/nexplane_agent/`
- Every executor must declare `ROLLBACK_CAPABILITY = "full" | "partial" | "irreversible"` at module level
- Every executor must implement both `async def execute(parameters, asset_ids, connector)` and `async def rollback(parameters, asset_ids, connector, execution_result)`
- `desired_outcome` is the only parameter channel — never a separate `parameters` field in CR creation
- ChangeType enum additions go in `backend/app/models/change_request.py` AND require `ALTER TYPE change_type ADD VALUE IF NOT EXISTS '...'` migration
- Every new CR type needs a `backend/app/connectors/change_type_definitions/{type}.json`
- Every new CR type needs a catalog entry in `backend/app/connectors/catalog/nexplane_agent.json`
- Smoke tests use the AMI cache pattern: build AMI on first run, cache key in SSM at `/nexplane/smoke-amis/{service}/{hash}`, reuse on subsequent runs
- Smoke tests must exercise full CR lifecycle: create → plan → approve → execute → verify → rollback
- `smoke_verified: false` in catalog until smoke passes; flip to `true` in the same commit as passing smoke
- Follow the db_major_version_upgrade.py and k8s_cluster_upgrade.py patterns exactly

---

## CR Type 1: `redis_cluster_migration`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/redis_cluster_migration.py`
- Create: `backend/app/connectors/change_type_definitions/redis_cluster_migration.json`
- Modify: `backend/app/models/change_request.py` — add `redis_cluster_migration` after `rotate_redis_password`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json` — add catalog entry
- Create: `backend/migrations/versions/XXXX_add_redis_cluster_migration_change_type.py`
- Create: `backend/tests/smoke/test_smoke_redis_cluster_migration.py`

**Parameters (all in `desired_outcome`):**
- `migration_type`: `"standalone_to_cluster"` | `"version_upgrade"` (required)
- `source_version`: e.g. `"6.2"` (required for version_upgrade)
- `target_version`: e.g. `"7.2"` (required)
- `cluster_nodes`: list of `{host, port}` for existing cluster nodes (for standalone_to_cluster, the new nodes to add)
- `rdb_backup_path`: where to save RDB snapshot (default `/tmp/nexplane-redis-backup.rdb`)
- `dry_run`: bool (default false)

**Executor flow:**
1. Preflight: `dispatch_agent_job("preflight_redis_upgrade", ...)` — detect mode (standalone/sentinel/cluster via `redis-cli INFO server`), validate target version exists, check replication lag if sentinel/cluster
2. Snapshot: `dispatch_agent_job("redis_bgsave_snapshot", ...)` — `BGSAVE`, poll `LASTSAVE`, download RDB to `rdb_backup_path`; store path in execution_result
3. Upgrade/Migrate:
   - `version_upgrade`: `dispatch_agent_job("redis_inplace_upgrade", ...)` — stop, replace binary, start, verify `redis-cli PING`
   - `standalone_to_cluster`: `dispatch_agent_job("redis_cluster_init", ...)` — `redis-cli --cluster create` with all nodes, wait for `cluster_state: ok`, migrate slots
4. Verify: `dispatch_agent_job("verify_redis_cluster", ...)` — `CLUSTER INFO` (cluster_state=ok, cluster_slots_assigned=16384), `DBSIZE` cross-check
5. Return: `{status, migration_type, source_version, target_version, cluster_nodes_verified, slot_coverage, upgraded_at}`

**Rollback:**
- `version_upgrade`: reinstall previous version binary, restart, verify PING — ROLLBACK_CAPABILITY = `"full"`
- `standalone_to_cluster`: restore RDB to standalone mode (`redis-cli --cluster reset HARD` on each node, restore RDB) — ROLLBACK_CAPABILITY = `"full"`

**change_type_definition:**
```json
{
  "change_type": "redis_cluster_migration",
  "display_name": "Redis Cluster Migration",
  "steps": [{"generic_action": "redis_cluster_migration", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable", "agent_reachable"],
  "verification_methods": ["cluster_health_check"],
  "rollback_action": "redis_cluster_migration",
  "rollback_connector_type": "nexplane_agent"
}
```

**Smoke test:** Two phases. Phase 1: launch EC2 from cached AMI (Redis 6.2 standalone), cache key `/nexplane/smoke-amis/redis-cluster/7.2`. Phase 2: CR lifecycle — `migration_type: "version_upgrade"`, source 6.2 → target 7.2. Assert `cluster_state` or standalone PING. Rollback asserts Redis responds on original version. Teardown terminates EC2.

---

## CR Type 2: `cassandra_rolling_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/cassandra_rolling_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/cassandra_rolling_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `cassandra_rolling_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file
- Create: `backend/tests/smoke/test_smoke_cassandra_rolling_upgrade.py`

**Parameters:**
- `target_version`: e.g. `"4.1"` (required)
- `source_version`: e.g. `"4.0"` (required)
- `nodes`: list of `{host, port, dc}` (required — all cluster nodes)
- `upgrade_delay_seconds`: seconds to wait between node upgrades (default 30)
- `dry_run`: bool

**Executor flow:**
1. Preflight: `dispatch_agent_job("preflight_cassandra_upgrade", ...)` — `nodetool status` (all UN), `nodetool version`, validate one-minor-version jump, check gossip compatibility
2. Snapshot: `dispatch_agent_job("cassandra_snapshot", ...)` — `nodetool snapshot` on each node, record snapshot tag
3. Rolling upgrade (per node in order):
   a. `dispatch_agent_job("cassandra_drain_node", ...)` — `nodetool drain`
   b. `dispatch_agent_job("cassandra_upgrade_node", ...)` — `systemctl stop cassandra`, apt/yum upgrade, `systemctl start cassandra`, wait for `nodetool status` UN
   c. If any node fails: stop loop (remaining nodes still on old version)
4. Post-upgrade: `dispatch_agent_job("cassandra_upgradesstables", ...)` — `nodetool upgradesstables` on each upgraded node
5. Verify: `nodetool status` all UN, `nodetool version` matches target on all nodes
6. Return: `{status, upgraded_nodes, failed_node, source_version, target_version, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"partial"` — can only restore nodes that were not yet upgraded. For upgraded nodes: downgrade package, restore from snapshot via `nodetool refresh`. Surface which nodes are on old vs new version. Nodes already upgraded cannot be safely downgraded (SSTable format); operator must restore from snapshot.

**Smoke test:** Single-node Cassandra (simulates cluster of 1 for smoke). Cached AMI `/nexplane/smoke-amis/cassandra/4.0`. Version upgrade 4.0→4.1. Assert `nodetool version` = 4.1 post-execute. Rollback brings back 4.0.

---

## CR Type 3: `mongodb_rs_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/mongodb_rs_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/mongodb_rs_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `mongodb_rs_upgrade` after `db_major_version_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file
- Create: `backend/tests/smoke/test_smoke_mongodb_rs_upgrade.py`

**Parameters:**
- `source_version`: e.g. `"6.0"` (required)
- `target_version`: e.g. `"7.0"` (required)
- `rs_members`: list of `{host, port, priority}` (required — primary last by convention)
- `admin_user`, `admin_password`: RS admin credentials
- `dry_run`: bool

**Executor flow:**
1. Preflight: connect to RS, `rs.status()`, validate all members healthy, compute FCV hop chain (same logic as `_compute_mongo_fcv_chain` in db_major_version_upgrade.py)
2. Snapshot: `mongodump --archive` on primary, store archive path
3. Per FCV hop (e.g. 6.0→7.0 is single hop; 4.4→7.0 requires 4.4→5.0→6.0→7.0):
   a. Upgrade secondaries (dispatch per secondary): stop mongod, install new package, start
   b. Step down primary: `rs.stepDown()`
   c. Upgrade old primary (now secondary)
   d. Wait for new primary election
   e. `db.adminCommand({setFeatureCompatibilityVersion: "<target>"})` — POINT OF NO RETURN
4. Verify: `db.adminCommand({getParameter:1, featureCompatibilityVersion:1})` matches target on all members
5. Return: `{status, rs_members_upgraded, fcv_chain, primary_host, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"partial"` — before `setFeatureCompatibilityVersion`: restore mongodump archive, reinstall old binaries. After FCV set: irreversible per MongoDB docs; surface this clearly in execution_result.

**Smoke test:** Single-node RS (rs.initiate with 1 member). Cached AMI `/nexplane/smoke-amis/mongodb-rs/6.0`. Upgrade 6.0→7.0. Assert FCV=7.0, RS healthy. Rollback before FCV step (dry_run path validates rollback path).

---

## CR Type 4: `cockroachdb_cluster_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/cockroachdb_cluster_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/cockroachdb_cluster_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `cockroachdb_cluster_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file
- Create: `backend/tests/smoke/test_smoke_cockroachdb_cluster_upgrade.py`

**Parameters:**
- `source_version`: e.g. `"23.1"` (required)
- `target_version`: e.g. `"23.2"` (required)
- `nodes`: list of `{host, http_port, sql_port}` (required)
- `sql_user`, `sql_password`: cluster admin
- `auto_finalize`: bool (default false — operator must explicitly trigger finalize)
- `dry_run`: bool

**Executor flow:**
1. Preflight: `SHOW CLUSTER SETTING version`, validate +1 minor, check all nodes healthy via `/_admin/v1/health`
2. Snapshot: `BACKUP INTO 's3://...'` or `cockroach dump` — store location
3. Rolling upgrade (per node):
   a. Drain: `cockroach node drain <id>`
   b. Replace binary
   c. Restart service, wait for node to rejoin (`/_admin/v1/nodes`)
4. Post-roll: all nodes on new binary, cluster still at old version setting
5. If `auto_finalize`: `SET CLUSTER SETTING version = crdb_internal.node_executable_version()` — IRREVERSIBLE
6. If not `auto_finalize`: return `{status: "awaiting_finalize", ...}` — operator runs rollback or approves finalize via separate CR
7. Verify: `SHOW CLUSTER SETTING version` matches target
8. Return: `{status, nodes_upgraded, version_finalized, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"partial"` — before finalize: reinstall old binary on each node, rolling restart. After finalize: irreversible; restore from backup.

**Smoke test:** Single-node CRDB. Cached AMI `/nexplane/smoke-amis/cockroachdb/23.1`. Upgrade 23.1→23.2 with `auto_finalize: false`. Assert nodes on new binary, version not yet finalized. Test rollback (reinstall 23.1 binary). Separate smoke run tests finalize path.

---

## CR Type 5: `etcd_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/etcd_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/etcd_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `etcd_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file
- Create: `backend/tests/smoke/test_smoke_etcd_upgrade.py`

**Parameters:**
- `source_version`: e.g. `"3.4"` (required)
- `target_version`: e.g. `"3.5"` (required)
- `members`: list of `{name, peer_url, client_url}` (required)
- `etcd_data_dir`: default `/var/lib/etcd`
- `snapshot_s3_bucket`: optional S3 bucket for snapshot upload
- `dry_run`: bool

**Executor flow:**
1. Preflight: `etcdctl endpoint health --cluster`, validate version path (+1 minor max), check cluster has quorum
2. Snapshot: `etcdctl snapshot save /tmp/nexplane-etcd-snapshot.db`, optionally upload to S3; store snapshot path
3. Rolling upgrade (per member):
   a. `dispatch_agent_job("etcd_replace_binary", ...)` — stop etcd, replace binary, start
   b. Wait for member to rejoin: `etcdctl endpoint health` for that member
   c. Verify leader election not disrupted: `etcdctl endpoint status`
4. Verify: `etcdctl endpoint status --cluster` all members healthy, version matches target
5. Return: `{status, members_upgraded, snapshot_path, leader_id, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"full"` — `etcdctl snapshot restore` on all members (wipes current data and restores snapshot). This is the canonical etcd rollback path. Surface data loss window clearly: all writes after snapshot time are lost.

**Smoke test:** Single-node etcd. Cached AMI `/nexplane/smoke-amis/etcd/3.4`. Upgrade 3.4→3.5. Assert `etcdctl endpoint status` version=3.5. Rollback restores snapshot, verify old version responds.

---

## CR Type 6: `minio_distributed_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/minio_distributed_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/minio_distributed_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `minio_distributed_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file
- Create: `backend/tests/smoke/test_smoke_minio_distributed_upgrade.py`

**Parameters:**
- `target_version`: e.g. `"RELEASE.2024-01-01T00-00-00Z"` (required — MinIO release tag)
- `nodes`: list of `{host, api_port, console_port}` (required)
- `access_key`, `secret_key`: MinIO admin credentials
- `dry_run`: bool

**Executor flow:**
1. Preflight: `mc admin info <alias>` — all nodes healthy, erasure sets intact, no healing in progress
2. Snapshot: `mc admin config export` (config backup), record current version from `mc admin info`
3. Upgrade: `mc admin update <alias> --json` — MinIO handles its own rolling upgrade (downloads new binary to all nodes, restarts in coordinated fashion)
4. Wait: poll `mc admin info` until all nodes report target version (up to 10 min)
5. Healing (if needed): `mc admin heal <alias> --recursive` if erasure format changed
6. Verify: `mc admin info` all nodes on target version, all drives online
7. Return: `{status, source_version, target_version, nodes_upgraded, heal_triggered, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"full"` — `mc admin update <alias> <previous-version-url>` pins back to specific release. MinIO supports this explicitly. If erasure format changed (rare, only on major releases), healing is required after rollback too.

**Smoke test:** Single-node MinIO (MinIO also supports single-node mode). Cached AMI `/nexplane/smoke-amis/minio/2023`. Upgrade to a recent release. Assert all drives online, version matches. Rollback to prior version.

---

## CR Type 7: `ceph_cluster_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/ceph_cluster_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/ceph_cluster_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `ceph_cluster_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file
- Create: `backend/tests/smoke/test_smoke_ceph_cluster_upgrade.py`

**Parameters:**
- `source_version`: e.g. `"18.2"` (Reef) (required)
- `target_version`: e.g. `"19.2"` (Squid) (required)
- `mgr_hosts`: list of `{host}` (required)
- `mon_hosts`: list of `{host}` (required)
- `osd_hosts`: list of `{host}` (required)
- `rgw_hosts`: optional list of `{host}` for RGW daemons
- `mds_hosts`: optional list of `{host}` for MDS daemons
- `dry_run`: bool

**Executor flow:**
Ceph mandates strict upgrade order: MGR → MON → OSD → (RGW/MDS). Mixed versions are tolerated within the compatibility window.

1. Preflight: `ceph -s` (HEALTH_OK), `ceph versions` (all on same version), validate +1 release jump
2. Snapshot: `ceph osd pool ls` + `rbd export` of critical pools to S3 is impractical at scale; instead: `ceph pg dump` state capture, `ceph osd crush dump` — stored as metadata
3. Set `noout`: `ceph osd set noout` — prevents rebalancing during upgrade
4. Upgrade MGRs: per mgr host — stop, upgrade package, start, verify `ceph mgr stat`
5. Upgrade MONs: per mon host — stop, upgrade, start, verify quorum `ceph quorum_status`
6. Upgrade OSDs: per osd host — `systemctl stop ceph-osd@*`, upgrade, start, wait for `ceph osd ok-to-stop <id>`
7. Upgrade RGW/MDS (if present)
8. Unset `noout`: `ceph osd unset noout`
9. Verify: `ceph -s` HEALTH_OK, `ceph versions` all on target
10. Return: `{status, daemons_upgraded, source_version, target_version, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"partial"` — downgrade is NOT supported by Ceph. If upgrade has not completed all OSDs: stop at current node, leave cluster in mixed-version (within tolerance window), surface which daemons are on which version. Operator must use Ceph backup (RBD snapshots, CephFS snapshots) to recover data if cluster is unhealthy.

**Smoke test:** Minimal Ceph cluster: 1 mon + 1 mgr + 3 OSD (loopback devices). Cached AMI `/nexplane/smoke-amis/ceph/18.2`. Upgrade Reef→Squid. Assert `ceph -s` HEALTH_OK, `ceph versions` all Squid. Rollback test: assert rollback execution_result surfaces `partial_rollback` with daemon state.

---

## Backlog additions (cross-cloud parity)

Add to `project_future_tasks.md` memory:
- **AWS ElastiCache Redis upgrade** — managed Redis version upgrade via ElastiCache ModifyCacheCluster API
- **GCP Memorystore Redis upgrade** — managed Redis version upgrade via Cloud Memorystore API
- **Azure Cache for Redis upgrade** — managed Redis version upgrade via Azure SDK
- **OCI Cache upgrade** — managed OCI Cache version upgrade via OCI SDK
- **AWS DocumentDB upgrade** — managed MongoDB-compatible upgrade via RDS ModifyDBCluster
- **GCP Cloud SQL upgrade** — managed Postgres/MySQL version upgrade via Cloud SQL Admin API
- **Azure Database upgrade** — managed Postgres/MySQL version upgrade via Azure Database API
- **OCI ADB/MySQL upgrade** — managed version upgrade via OCI Database API
