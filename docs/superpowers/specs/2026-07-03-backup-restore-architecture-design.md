# Backup & Restore Architecture Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the backup/restore executor into a pluggable three-abstraction architecture (capture strategy × storage backend × restore strategy) supporting machine-level and data-level backups across all cloud environments and on-premises infrastructure, then exercise the AWS path with an enhanced live smoke test.

**Architecture:** Three independent abstractions — how data is captured, where it is stored, and how it is restored — are wired together by a thin dispatcher. All strategy implementations conform to a shared interface so new clouds and capture methods can be added without touching core logic. The smoke test proves the AWS path end-to-end and serves as the reference implementation for future strategy smoke suites.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy 2.x async, boto3 (AWS), google-cloud-storage (GCS), azure-storage-blob, oci (OCI SDK), paramiko/asyncssh (SSH), WinRM (Windows), APScheduler (RecurringJob), pytest-style smoke runner

---

## Global Constraints

- All new strategy modules expose exactly two async functions: `backup(params, creds) → artifact_refs` and `rollback(artifact_refs, creds)`
- All new storage backend modules expose exactly three async functions: `upload(local_path, dest_key, config) → uri`, `download(uri, local_path, config)`, `delete(uri, config)`
- `BackupTarget.capture_strategy` defaults to `"ebs_snapshot"` — no migration required for existing targets
- `BackupStorage.storage_type` enum extended to include `"oci_object_storage"` — existing rows unaffected
- SSM is used only in the smoke test harness for data verification — it is never called by the backup or restore executor itself
- Core backup and restore functionality must be cloud-agnostic: no AWS SDK calls outside `backup_strategies/ebs_snapshot.py`, `backup_strategies/mgn_replication.py`, `restore_strategies/launch_ami.py`, `restore_strategies/import_image.py`, and `storage_backends/s3.py`
- Smoke test follows existing file conventions: `phase_*` naming, `fail()` for assertion failures, `--email`/`--password` CLI args, REST client via httpx
- FILO rollback order (B3 → B2 → B1) is unchanged

---

## Section 1: Backup Tiers

Two tiers are first-class, not a hierarchy:

**Machine backup** captures a running system to a bootable or fully restorable state. Restore produces a running instance. Source is always a server (EC2, bare metal, VM).

**Data backup** captures specific data — files, directories, database dumps, storage prefixes — to an artifact in a storage backend. Restore delivers data to a path, database, or storage location. No instance provisioning required.

`BackupTarget` gains a `backup_tier` column (`"machine"` | `"data"`) that determines which capture and restore strategies are valid. The column is nullable and defaults to `"machine"` for backward compatibility.

---

## Section 2: Capture Strategies

### Machine-tier strategies

| `capture_strategy` | Mechanism | Produces | Platform |
|---|---|---|---|
| `ebs_snapshot` | EBS snapshot + AMI creation | AMI ID + snapshot IDs | AWS |
| `mgn_replication` | AWS MGN agent continuous replication → cutover | AMI ID | AWS |
| `disk2vhd` | WinRM → Disk2vhd (VSS-aware) → upload | `.vhdx` artifact URI | Windows bare metal / Hyper-V |
| `lvm_snapshot` | SSH → LVM snapshot → dd → upload | raw image artifact URI | Linux bare metal |

`disk2vhd` is VSS-aware, which means it coordinates with the Windows VSS framework including the AD VSS writer. This gives consistent capture of Domain Controllers without quiescing the machine — NTDS.dit, SYSVOL, and registry hives are all captured in a transactionally consistent state.

### Data-tier strategies

| `capture_strategy` | Mechanism | Produces | Notes |
|---|---|---|---|
| `local_files` | SSH/agent → rsync/tar of a local path | `.tar.gz` artifact URI | Any Linux/Windows host |
| `nfs_files` | SSH/agent → rsync/tar from NFS mount | `.tar.gz` artifact URI | NFS path in params |
| `database_dump` | pg_dump / mysqldump / mongodump via DB connector | `.sql` / `.bson` artifact URI | Self-hosted DBs |
| `managed_db_snapshot` | Cloud-managed DB snapshot API | snapshot reference URI | RDS, Cloud SQL, Azure DB |
| `storage_sync` | Cross-backend sync (S3→GCS, NFS→S3, etc.) | manifest of synced objects | Storage-to-storage |

`managed_db_snapshot` accepts a `managed_db_connector_id` param and calls the appropriate cloud API (RDS `create_db_snapshot`, Cloud SQL `databases.export`, Azure Database for PostgreSQL `backups.create`). The snapshot reference is stored in `artifact_refs` as an opaque URI parseable by the corresponding restore strategy.

---

## Section 3: Storage Backends

All artifact uploads (non-AMI) and manifests land in a configured `BackupStorage` record. The storage backend is selected from `BackupTarget.storage_id`.

| `storage_type` | SDK | Notes |
|---|---|---|
| `s3` | boto3 | Existing impl extracted to module |
| `gcs` | google-cloud-storage | `gs://bucket/key` URI scheme |
| `azure_blob` | azure-storage-blob | `https://account.blob.core.windows.net/container/key` |
| `oci_object_storage` | oci | `oci://namespace/bucket/key` URI scheme |
| `nfs` | standard filesystem | Mounted path in `config.mount_path`; no SDK |
| `local` | standard filesystem | `config.base_path`; test/dev only |

Each backend module receives the `BackupStorage.config` JSONB dict for credentials and bucket/path config. Credentials for cloud backends are resolved from the associated connector record (same pattern as other executors) — never stored directly in `config`.

---

## Section 4: Restore Strategies

### Machine-tier restore strategies

| `restore_strategy` | Mechanism | Input | Platform |
|---|---|---|---|
| `launch_ami` | EC2 RunInstances from AMI | AMI ID from artifact_refs | AWS |
| `import_image` | Upload VHD/raw → cloud image import → launch | artifact URI from storage backend | AWS EC2 Import, Azure Image Import, GCP Import |
| `in_place` | SSM/WinRM commands to restore filesystem in place | artifact URI | Any; irreversible |

### Data-tier restore strategies

| `restore_strategy` | Mechanism | Input |
|---|---|---|
| `file_restore_to_path` | Pull tar from storage backend → extract to target path | artifact URI + target path |
| `database_restore` | Pull dump from storage backend → pipe into target DB via connector | artifact URI + DB connector ID |
| `storage_restore` | Push artifact back to origin location or new storage destination | artifact URI + target storage config |

---

## Section 5: Executor Refactor

### File structure

```
backend/app/connectors/executors/nexplane_agent/
  server_backup.py              ← thin dispatcher (replaces current monolith)
  restore_server.py             ← thin dispatcher (replaces current monolith)
  backup_strategies/
    __init__.py
    ebs_snapshot.py             ← current server_backup.py logic extracted here
    mgn_replication.py          ← stub (interface defined, raises NotImplementedError)
    disk2vhd.py                 ← stub
    lvm_snapshot.py             ← stub
    local_files.py
    nfs_files.py
    database_dump.py
    managed_db_snapshot.py      ← stub
    storage_sync.py             ← stub
  storage_backends/
    __init__.py
    s3.py                       ← current S3 upload/delete logic extracted here
    gcs.py                      ← stub
    azure_blob.py               ← stub
    oci_object_storage.py       ← stub
    nfs.py
    local.py
  restore_strategies/
    __init__.py
    launch_ami.py               ← current restore_server.py logic extracted here
    import_image.py             ← stub
    in_place.py                 ← current "same" mode extracted here
    file_restore_to_path.py
    database_restore.py
    storage_restore.py          ← stub
```

Stubs raise `NotImplementedError` with a message naming the strategy and the expected interface. This makes the strategy registry discoverable and testable without shipping incomplete implementations.

### Dispatcher interface

`server_backup.py` resolves `capture_strategy` from the `BackupTarget` record, imports the corresponding module from `backup_strategies/`, and calls:

```python
artifact_refs = await strategy_module.backup(params, creds)
```

`restore_server.py` resolves `restore_strategy` from the source backup CR's `artifact_refs.restore_strategy` field, imports from `restore_strategies/`, and calls:

```python
result = await strategy_module.restore(artifact_refs, params, creds)
```

Rollback follows the same pattern: each strategy module's `rollback(artifact_refs, creds)` is called by the dispatcher.

### artifact_refs schema

`artifact_refs` is a JSONB field on the ChangeRequest. Its shape is strategy-dependent but always includes:

```json
{
  "capture_strategy": "ebs_snapshot",
  "restore_strategy": "launch_ami",
  "backup_tier": "machine",
  "captured_at": "2026-07-03T12:00:00Z",
  "storage_type": "s3",
  ...strategy-specific fields...
}
```

Strategy-specific fields for `ebs_snapshot`: `ami_id`, `snapshot_ids`, `bucket_or_path`, `prefix`.
Strategy-specific fields for `local_files`/`nfs_files`: `artifact_uri`, `source_path`, `file_count`, `size_bytes`.
Strategy-specific fields for `database_dump`: `artifact_uri`, `db_type`, `db_name`, `dump_format`.
Strategy-specific fields for `managed_db_snapshot`: `snapshot_reference_uri`, `provider`, `region`.

---

## Section 6: Data Model Changes

### `BackupTarget`

Add columns:
- `backup_tier: String` — `"machine"` | `"data"`, nullable, server default `"machine"`
- `capture_strategy: String` — strategy key (e.g. `"ebs_snapshot"`), nullable, server default `"ebs_snapshot"`

Migration: `ALTER TABLE backup_targets ADD COLUMN backup_tier VARCHAR DEFAULT 'machine', ADD COLUMN capture_strategy VARCHAR DEFAULT 'ebs_snapshot'`.

### `BackupStorage`

Extend `storage_type` accepted values in application code to include `"oci_object_storage"`. The column is already a plain `String` — no DB migration needed, only a validator update.

---

## Section 7: Smoke Test Enhancements

The existing `test_backup_scheduler_live.py` is enhanced with three changes. The FILO rollback sequence (B3→B2→B1) is unchanged.

### B0: SSM sentinel (new phase, runs before B1)

Before any backup executes, write a sentinel file to the source EC2 instance via an SSM command CR:

```
echo "{uuid}" > /home/ec2-user/nexplane-smoke-marker-{uuid}
```

The UUID is generated by the smoke test and stored in phase state. This is pure test infrastructure — the backup executor never touches SSM.

### B4: Run-now fix

Remove the graceful skip. When `POST /recurring-jobs/{id}/run-now` returns a CR ID:
- If CR status is `awaiting_approval`, manually approve: `POST /change-requests/{id}/approve` with `{"decision": "approved", "comment": "smoke auto-approve"}`.
- Then poll for `completed` status (30s timeout).
- If run-now returns 500 or 404, `fail()` immediately — do not skip.

### R2: SSM data verification (replaces current running-state-only check)

After the restored instance is `running`:

1. Poll SSM reachability: `GET /assets/{restored_instance_id}/check` or equivalent until SSM agent responds (60s timeout, 5s interval).
2. Create an SSM command CR targeting the restored instance:
   ```
   cat /home/ec2-user/nexplane-smoke-marker-{uuid}
   ```
3. Assert the output contains exactly the sentinel UUID written in B0.

This proves the restored instance contains the filesystem state from before the backup — data-proof level verification. SSM is used only as the verification transport; it is not part of the backup or restore executor.

---

## Section 8: Error Handling

- **Strategy not implemented:** Dispatcher catches `NotImplementedError` from stub modules and returns a `plan_blocked` CR status with message `"capture_strategy {name} is not yet implemented"`.
- **Storage backend unreachable:** Upload/download functions raise `BackupStorageError` (new exception class); dispatcher surfaces this as CR execution failure with the error message.
- **Partial backup failure:** If EBS snapshot creation succeeds but AMI creation fails, rollback deletes the created snapshots. Each strategy module is responsible for rolling back its own partial state.
- **Restore to terminated instance:** `launch_ami` checks that the source AMI still exists before launching; if deregistered, surfaces a clear error rather than an EC2 API exception.
- **SSM timeout in smoke:** If SSM reachability check times out after 60s, `fail()` with message indicating the restored instance is running but SSM-unreachable — distinct from data mismatch.

---

## Section 9: Testing

### Unit tests

- `test_backup_dispatcher.py`: Asserts that dispatcher selects the correct strategy module for each `capture_strategy` value; asserts `NotImplementedError` is surfaced correctly for stub strategies.
- `test_storage_backends.py`: Mocked upload/download/delete for each backend; asserts URI schemes and config key handling.
- `test_artifact_refs_schema.py`: Validates that each strategy's `artifact_refs` output contains required keys.

### Smoke test

`test_backup_scheduler_live.py` — existing file enhanced per Section 7. Phases:

- `BACKUP_SENTINEL` (B0): Write SSM sentinel to source instance
- `BACKUP_S3` (B1): S3 manifest backup via `local_files` strategy (existing)
- `BACKUP_AMI` (B2): EBS snapshot + AMI via `ebs_snapshot` strategy (existing)
- `BACKUP_SCHEDULE` (B3): RecurringJob create + schedule (existing)
- `BACKUP_RUN_NOW` (B4): Run-now with mandatory approval (fixed)
- `RESTORE_AMI` (R2): Launch from AMI + SSM UUID verification (enhanced)
- `ROLLBACK` (FILO): B3→B2→B1 (unchanged)

Run command:
```bash
docker compose exec -T backend python tests/smoke/test_backup_scheduler_live.py \
    --email admin@acme.example --password admin123
```

---

## Deferred (out of scope)

- WAL/binlog continuous archiving (point-in-time recovery) — requires a streaming pipeline separate from the CR lifecycle; design separately
- Incremental backups — delta tracking on top of full backups; follow-on spec
- `mgn_replication`, `disk2vhd`, `lvm_snapshot`, `managed_db_snapshot`, `storage_sync`, `import_image`, `storage_restore` implementations — stubs ship now; implementations ship alongside their connector smoke suites
- GCS, Azure Blob, OCI Object Storage backend implementations — stubs ship now; implementations ship when those connector smoke suites are built
- Backup encryption at rest — key management design required; separate spec
