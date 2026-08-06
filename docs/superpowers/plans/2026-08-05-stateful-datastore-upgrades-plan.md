# Stateful Datastore Upgrades — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to execute task-by-task. Each task is independent. Use checkbox syntax for tracking.

**Goal:** Implement upgrade/migration CR types for 7 stateful datastores via nexplane_agent executor pattern with AMI-cached smoke tests.

**Architecture:** Each CR: preflight → snapshot → rolling upgrade → verify → rollback. All executors dispatch via `dispatch_agent_job`. Smoke tests use cached AMIs, run full CR lifecycle including rollback.

---

## Task 1: `redis_cluster_migration`

- [x] Create `backend/app/connectors/executors/nexplane_agent/redis_cluster_migration.py`
- [x] Create `backend/app/connectors/change_type_definitions/redis_cluster_migration.json`
- [x] Add `redis_cluster_migration` to ChangeType enum in `backend/app/models/change_request.py` (in stateful datastore upgrades section)
- [x] Create `backend/alembic/versions/sdu001_add_redis_cluster_migration_change_type.py`
- [x] Add catalog entry in `backend/app/connectors/catalog/nexplane_agent.json`
- [x] Create `backend/tests/smoke/test_smoke_redis_cluster_migration.py`

**ROLLBACK_CAPABILITY:** `"full"` — RDB restore for version_upgrade; cluster reset + RDB restore for standalone_to_cluster.

**Executor flow:** preflight_redis_upgrade → redis_bgsave_snapshot → redis_inplace_upgrade OR redis_cluster_init → verify_redis_cluster

**Smoke:** EC2 from `/nexplane/smoke-amis/redis-cluster/6.2`, CR: version_upgrade 6.2→7.2

---

## Task 2: `cassandra_rolling_upgrade`

- [x] Create `backend/app/connectors/executors/nexplane_agent/cassandra_rolling_upgrade.py`
- [x] Create `backend/app/connectors/change_type_definitions/cassandra_rolling_upgrade.json`
- [x] Add `cassandra_rolling_upgrade` to ChangeType enum
- [x] Create `backend/alembic/versions/sdu002_add_cassandra_rolling_upgrade_change_type.py`
- [x] Add catalog entry
- [x] Create `backend/tests/smoke/test_smoke_cassandra_rolling_upgrade.py`

**ROLLBACK_CAPABILITY:** `"partial"` — per-node snapshot restore; upgraded nodes may require snapshot restore.

**Executor flow:** preflight_cassandra_upgrade → cassandra_snapshot → (per node: cassandra_drain_node → cassandra_upgrade_node) → cassandra_upgradesstables → verify

**Smoke:** EC2 from `/nexplane/smoke-amis/cassandra/4.0`, CR: 4.0→4.1

---

## Task 3: `mongodb_rs_upgrade`

- [x] Create `backend/app/connectors/executors/nexplane_agent/mongodb_rs_upgrade.py`
- [x] Create `backend/app/connectors/change_type_definitions/mongodb_rs_upgrade.json`
- [x] Add `mongodb_rs_upgrade` to ChangeType enum
- [x] Create `backend/alembic/versions/sdu003_add_mongodb_rs_upgrade_change_type.py`
- [x] Add catalog entry
- [x] Create `backend/tests/smoke/test_smoke_mongodb_rs_upgrade.py`

**ROLLBACK_CAPABILITY:** `"partial"` — before FCV set: mongodump restore + binary downgrade. After FCV: irreversible.

**Executor flow:** preflight_mongo_rs_upgrade → mongodump_snapshot → (per FCV hop: upgrade secondaries → stepdown primary → upgrade old primary → wait election → set FCV) → verify

**Smoke:** EC2 from `/nexplane/smoke-amis/mongodb-rs/6.0`, CR: 6.0→7.0

---

## Task 4: `cockroachdb_cluster_upgrade`

- [x] Create `backend/app/connectors/executors/nexplane_agent/cockroachdb_cluster_upgrade.py`
- [x] Create `backend/app/connectors/change_type_definitions/cockroachdb_cluster_upgrade.json`
- [x] Add `cockroachdb_cluster_upgrade` to ChangeType enum
- [x] Create `backend/alembic/versions/sdu004_add_cockroachdb_cluster_upgrade_change_type.py`
- [x] Add catalog entry
- [x] Create `backend/tests/smoke/test_smoke_cockroachdb_cluster_upgrade.py`

**ROLLBACK_CAPABILITY:** `"partial"` — before finalize: reinstall old binary. After finalize: irreversible.

**Executor flow:** preflight_crdb_upgrade → crdb_backup → (per node: crdb_drain_node → crdb_replace_binary → rejoin wait) → crdb_finalize_version (if auto_finalize) → verify

**Smoke:** EC2 from `/nexplane/smoke-amis/cockroachdb/23.1`, CR: 23.1→23.2 auto_finalize=false

---

## Task 5: `etcd_upgrade`

- [x] Create `backend/app/connectors/executors/nexplane_agent/etcd_upgrade.py`
- [x] Create `backend/app/connectors/change_type_definitions/etcd_upgrade.json`
- [x] Add `etcd_upgrade` to ChangeType enum
- [x] Create `backend/alembic/versions/sdu005_add_etcd_upgrade_change_type.py`
- [x] Add catalog entry
- [x] Create `backend/tests/smoke/test_smoke_etcd_upgrade.py`

**ROLLBACK_CAPABILITY:** `"full"` — etcdctl snapshot restore on all members.

**Executor flow:** preflight_etcd_upgrade → etcd_snapshot_save → (per member: etcd_replace_binary → member health check) → verify

**Smoke:** EC2 from `/nexplane/smoke-amis/etcd/3.4`, CR: 3.4→3.5

---

## Task 6: `minio_distributed_upgrade`

- [x] Create `backend/app/connectors/executors/nexplane_agent/minio_distributed_upgrade.py`
- [x] Create `backend/app/connectors/change_type_definitions/minio_distributed_upgrade.json`
- [x] Add `minio_distributed_upgrade` to ChangeType enum
- [x] Create `backend/alembic/versions/sdu006_add_minio_distributed_upgrade_change_type.py`
- [x] Add catalog entry
- [x] Create `backend/tests/smoke/test_smoke_minio_distributed_upgrade.py`

**ROLLBACK_CAPABILITY:** `"full"` — mc admin update to pinned previous version.

**Executor flow:** preflight_minio_upgrade → minio_config_export → minio_update → poll until all nodes on target version → minio_verify_cluster

**Smoke:** EC2 from `/nexplane/smoke-amis/minio/2023`, CR: upgrade to RELEASE.2024-01-01T00-00-00Z

---

## Task 7: `ceph_cluster_upgrade`

- [x] Create `backend/app/connectors/executors/nexplane_agent/ceph_cluster_upgrade.py`
- [x] Create `backend/app/connectors/change_type_definitions/ceph_cluster_upgrade.json`
- [x] Add `ceph_cluster_upgrade` to ChangeType enum
- [x] Create `backend/alembic/versions/sdu007_add_ceph_cluster_upgrade_change_type.py`
- [x] Add catalog entry
- [x] Create `backend/tests/smoke/test_smoke_ceph_cluster_upgrade.py`

**ROLLBACK_CAPABILITY:** `"partial"` — Ceph does not support downgrade. Surface daemon state; operator uses RBD/CephFS snapshots.

**Executor flow:** preflight_ceph_upgrade → ceph_set_noout → ceph_upgrade_mgr (per mgr) → ceph_upgrade_mon (per mon) → ceph_upgrade_osd (per osd) → ceph_unset_noout → ceph_verify_health

**Smoke:** EC2 from `/nexplane/smoke-amis/ceph/18.2`, CR: 18.2→19.2 (Reef→Squid)
