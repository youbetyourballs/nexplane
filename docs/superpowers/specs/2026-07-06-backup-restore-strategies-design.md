# Backup Restore Strategies — GCS Backend, storage_restore, database_restore

## Goal

Complete the restore side of three already-implemented backup strategies by implementing: (1) the GCS storage backend so GCP buckets can hold backup artifacts, (2) `storage_restore` so storage_sync backups can be restored to a target location, and (3) `database_restore` for Postgres, MySQL, and MongoDB so database_dump backups can be restored to a target database. Each ships with a live smoke phase.

## Architecture

Three components built agent-mediated style (approach A): the executor downloads the artifact from the storage backend, then runs the restore operation against the target. This mirrors how `database_dump` and `file_restore_to_path` work today.

### GCS storage backend

**File:** `backend/app/connectors/executors/nexplane_agent/storage_backends/gcs.py`

Replaces the `NotImplementedError` stub with a full implementation of the 5-method storage backend interface, identical in signature to `s3.py`:

- `upload(local_path, dest_key, config) -> str` — uploads file, returns `gcs://bucket/key` URI
- `download(uri, local_path, config) -> None` — downloads by `gcs://bucket/key` URI
- `delete(uri, config) -> None` — deletes single object by URI
- `put_bytes(key, data, config) -> str` — uploads raw bytes, returns URI
- `delete_prefix(prefix, config) -> dict` — deletes all blobs under prefix, returns `{"deleted_count": N}`

Uses `google-cloud-storage` (`google-cloud-storage>=2.18.0` already in `requirements.txt` from the GCP monitoring work). Credentials come from `config["service_account_key_json"]` (service account key JSON string or dict) and `config["bucket"]`. If `config` lacks `service_account_key_json`, falls back to Application Default Credentials (ADC) for local dev. URI scheme: `gcs://bucket/key`.

No-creds guard: if `config` is empty or has no bucket, raise `ValueError` with a clear message (unlike other backends which have a mock path — storage backends don't have a connector context, so we raise rather than mock).

The GCS backend is already registered as a stub in `storage_backends/__init__.py` via lazy import — no registry change needed. Once the stub raises `NotImplementedError` are replaced with real code, the registry entry works automatically.

### storage_restore restore strategy

**File:** `backend/app/connectors/executors/nexplane_agent/restore_strategies/storage_restore.py`

Replaces the `NotImplementedError` stub.

**`restore(params, asset_ids, connector) -> dict`:**

1. Load `artifact_refs` from `params["source_backup_cr_id"]` using `_load_source_artifact_refs()` (same helper used by `file_restore_to_path`)
2. Extract `storage_type`, `config` (source), and `keys` (list of `artifact_uri` strings) from `artifact_refs`
3. Load target config from params: `target_storage_type` (defaults to `artifact_refs["storage_type"]`), `target_bucket`, `target_prefix`
4. For each artifact URI: call `source_backend.download(uri, tmp_path, source_config)` → `target_backend.upload(tmp_path, target_key, target_config)` → collect returned target URI
5. Return `{"status": "completed", "restore_strategy": "storage_restore", "restored_uris": [...], "target_storage_type": ..., "target_bucket": ..., "target_prefix": ..., "restored_at": ...}`

**`rollback(params, execution_result, connector) -> dict`:**

Iterates `execution_result["restored_uris"]`, calls `target_backend.delete(uri, target_config)` for each. Returns `{"rolled_back": True, "deleted_count": N}`.

If `restored_uris` is empty or missing, returns `{"rolled_back": False, "reason": "no restored_uris in execution_result"}`.

### database_dump extension (MySQL + MongoDB)

**File:** `backend/app/connectors/executors/nexplane_agent/backup_strategies/database_dump.py`

Currently only supports Postgres (`_SUPPORTED_DB_TYPES = ("postgres",)`). Extend to support MySQL and MongoDB before database_restore can work for those types.

Add to `_SUPPORTED_DB_TYPES`: `("postgres", "mysql", "mongodb")`

Add dump branches in `_sync_dump_and_upload()`:

- **MySQL:** `mysqldump -h {host} -P {port} -u {user} -p{pw} {dbname} | gzip` → same `.sql.gz` pattern
- **MongoDB:** `mongodump --host {host} --port {port} -u {user} -p {pw} --authenticationDatabase admin --db {dbname} --archive | gzip` → `.archive.gz` extension

The dump key suffix changes by type: `.sql.gz` for Postgres/MySQL, `.archive.gz` for MongoDB. Store `dump_format` in `artifact_refs` so `database_restore` knows which restore command to use.

No changes to the `rollback` function (deletes the artifact URI regardless of type).

### database_restore restore strategy

**File:** `backend/app/connectors/executors/nexplane_agent/restore_strategies/database_restore.py`

Replaces the `NotImplementedError` stub.

**`restore(params, asset_ids, connector) -> dict`:**

1. Load `artifact_refs` from `params["source_backup_cr_id"]`
2. Read `db_type` from `artifact_refs["db_type"]` (set by `database_dump`)
3. Read SSH credentials from connector (same pattern as `database_dump` — connector creds, then inline `ssh_creds`, then asset connector fallback)
4. Read target DB params: `target_db_host`, `target_db_port`, `target_db_name`, `target_db_user`, `target_db_password` from params
5. Download dump artifact from storage backend to `/tmp/{uuid}.sql.gz` on the executor
6. Upload via SFTP to `/tmp/{uuid}.sql.gz` on the agent host (SSH)
7. Run restore command via SSH:
   - **Postgres:** `gunzip -c /tmp/{uuid}.sql.gz | PGPASSWORD={pw} psql -h {host} -p {port} -U {user} {dbname}`
   - **MySQL:** `gunzip -c /tmp/{uuid}.sql.gz | mysql -h {host} -P {port} -u {user} -p{pw} {dbname}`
   - **MongoDB:** `gunzip -c /tmp/{uuid}.sql.gz | mongorestore --host {host} --port {port} --username {user} --password {pw} --authenticationDatabase admin --archive --db {dbname}`
8. Verify: run connectivity check via SSH:
   - Postgres: `PGPASSWORD={pw} psql -h {host} -p {port} -U {user} {dbname} -c "SELECT 1"`
   - MySQL: `mysql -h {host} -P {port} -u {user} -p{pw} {dbname} -e "SELECT 1"`
   - MongoDB: `mongosh --host {host} --port {port} -u {user} -p {pw} --eval "db.runCommand({ping:1})"`
9. Clean up temp files on both executor and agent
10. Return `{"status": "completed", "restore_strategy": "database_restore", "db_type": ..., "target_db_name": ..., "restored_at": ...}`

**`rollback(params, execution_result, connector) -> dict`:**

Requires `params["confirm_drop"] == True` (always — prevents accidental data loss). If not set, returns `{"rolled_back": False, "reason": "confirm_drop not set — set confirm_drop=true to drop the restored database"}`.

Drop commands (via SSH):
- Postgres: `PGPASSWORD={pw} psql -h {host} -p {port} -U {user} -c "DROP DATABASE IF EXISTS {dbname}"`
- MySQL: `mysql -h {host} -P {port} -u {user} -p{pw} -e "DROP DATABASE IF EXISTS {dbname}"`
- MongoDB: `mongosh --host {host} --port {port} -u {user} -p {pw} --eval "db.getSiblingDB('{dbname}').dropDatabase()"`

Returns `{"rolled_back": True, "dropped_database": dbname}`.

## Smoke Phases

Three phases in `backend/tests/smoke/test_backup_strategies_restore_live.py`, run as `--phases GCS_BACKEND,STORAGE_RESTORE,DB_RESTORE`.

### PHASE_GCS_BACKEND

No CR lifecycle needed — tests the storage backend directly via in-process calls (same pattern as unit tests, but against real GCP infra).

1. Load GCP connector credentials from DB (`get_connector_creds_from_db`)
2. Call `gcs.put_bytes("smoke/gcs_backend_test.txt", b"nexplane-gcs-smoke", config)` → assert returns `gcs://bucket/smoke/gcs_backend_test.txt`
3. Download to temp file → assert contents match
4. Call `gcs.delete(uri, config)` → assert no exception
5. Verify deletion: call `gcs.download(uri, tmp, config)` → assert raises (blob not found)
6. Call `gcs.delete_prefix("smoke/", config)` → assert `deleted_count == 0` (already deleted)

Pass: all assertions green. No rollback needed (artifact deleted in-phase).

### PHASE_STORAGE_RESTORE

Uses the full CR lifecycle.

1. **Setup:** ensure S3 smoke bucket exists; upload a known test object `smoke/storage_restore_source.txt` directly via boto3
2. **CR1 — storage_sync backup:** `change_type=server_backup`, `capture_strategy=storage_sync`, source pointing to `smoke/storage_restore_source.txt` → execute → verify artifact_refs has `keys`
3. **CR2 — storage_restore:** `change_type=restore_server`, `restore_strategy=storage_restore`, `source_backup_cr_id=CR1.id`, `target_storage_type=s3`, `target_bucket=smoke-bucket`, `target_prefix=smoke/restored/` → execute → SDK verify: boto3 `head_object` confirms restored object exists
4. **Rollback (LIFO):** rollback CR2 → SDK verify object gone; rollback CR1 → verify source artifact gone

Pass: `ALL SELECTED PHASES PASSED`.

### PHASE_DB_RESTORE

Uses the full CR lifecycle. MySQL and MongoDB run as Docker containers on the EC2 runner; Postgres uses the existing RDS instance (same infra as managed_db_snapshot smoke — check AMI cache first).

**Postgres sub-phase:**
1. Create a test table in the smoke RDS instance: `CREATE TABLE nexplane_restore_smoke (id serial, val text); INSERT INTO nexplane_restore_smoke VALUES (1, 'smoke')`
2. CR1 — `database_dump` CR → execute → verify artifact in S3
3. CR2 — `database_restore` CR, `target_db_name=nexplane_restore_smoke_copy`, `confirm_drop=true` → execute → verify: `SELECT COUNT(*) FROM nexplane_restore_smoke` in the copy DB returns 1
4. Rollback CR2 → verify `nexplane_restore_smoke_copy` is gone; rollback CR1 → verify artifact deleted from S3

**MySQL sub-phase:**
1. `docker run -d --name smoke-mysql -e MYSQL_ROOT_PASSWORD=smokepass -e MYSQL_DATABASE=smokedb -p 3307:3306 mysql:8`; wait for ready (30s)
2. Seed: `mysql -h 127.0.0.1 -P 3307 -uroot -psmokepass smokedb -e "CREATE TABLE t (id int); INSERT INTO t VALUES (42)"`
3. CR3 — `database_dump` (mysql, localhost:3307) → execute
4. CR4 — `database_restore` (mysql, `target_db_name=smokedb_copy`) → execute → verify row count
5. LIFO rollback CR4 → CR3; `docker rm -f smoke-mysql`

**MongoDB sub-phase:**
1. `docker run -d --name smoke-mongo -p 27018:27017 mongo:6`; wait 15s
2. Seed: `mongosh --port 27018 --eval "db.getSiblingDB('smokedb').col.insertOne({x:1})"`
3. CR5 — `database_dump` (mongodb, localhost:27018) → execute
4. CR6 — `database_restore` (mongodb, `target_db_name=smokedb_copy`) → execute → verify doc count
5. LIFO rollback CR6 → CR5; `docker rm -f smoke-mongo`

Pass: all three sub-phases complete with `ALL SELECTED PHASES PASSED`.

## Global Constraints

- SPDX header on every new/modified Python file: `# SPDX-License-Identifier: AGPL-3.0-only` + `# Copyright (C) 2024-2026 Nexplane, Inc.`
- `google-cloud-storage>=2.18.0` already in `requirements.txt` — no change needed
- `paramiko` already in `requirements.txt` — no change needed
- `asyncio.get_running_loop()` for `run_in_executor` in restore strategies (these are called from async context, unlike backup strategies which use `get_event_loop()`)
- GCS URI scheme: `gcs://bucket/key` (mirrors S3's `s3://bucket/key`)
- `confirm_drop=true` always required for `database_restore` rollback — no exceptions
- Smoke tests run on EC2 runner via SSM command CR pattern (not locally)
- No mocks — all three phases run against real GCP/AWS/Docker infra
- Docker must be available on the EC2 runner (it is — used by other smoke phases)
- MySQL and MongoDB CLI tools (`mysql`, `mongosh`, `mongorestore`) must be available on the agent host — install via `docker exec` if not present on the runner directly, or run restore commands inside the container

## Files Touched

**New/modified:**
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/database_dump.py` — extend to support MySQL + MongoDB
- `backend/app/connectors/executors/nexplane_agent/storage_backends/gcs.py` — implement (currently stub)
- `backend/app/connectors/executors/nexplane_agent/restore_strategies/storage_restore.py` — implement (currently stub)
- `backend/app/connectors/executors/nexplane_agent/restore_strategies/database_restore.py` — implement (currently stub)
- `backend/tests/smoke/test_backup_strategies_restore_live.py` — new smoke test file

**No changes needed:**
- `storage_backends/__init__.py` — GCS already registered via lazy import
- `restore_strategies/__init__.py` — database_restore and storage_restore already registered
- `server_backup.py` / `restore_server.py` — dispatchers already route by strategy name
- Alembic migrations — no new change types or DB schema changes
- `gcp.json` catalog — no new CR types
- `requirements.txt` — `google-cloud-storage` already present
