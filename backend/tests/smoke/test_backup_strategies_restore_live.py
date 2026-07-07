# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
#!/usr/bin/env python3
"""
Live smoke tests for backup/restore strategy completion:
  GCS_BACKEND    — in-process calls against real GCP bucket
  STORAGE_RESTORE — full CR lifecycle: storage_sync backup → storage_restore
  DB_RESTORE      — full CR lifecycle: database_dump + database_restore (Postgres/MySQL/MongoDB)

Run from EC2 runner inside backend container:
    python tests/smoke/test_backup_strategies_restore_live.py \
        --base-url http://localhost:8000 \
        --email admin@acme.example \
        --password admin123 \
        --phases GCS_BACKEND,STORAGE_RESTORE,DB_RESTORE
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

import boto3

_IN_CONTAINER = os.path.exists("/.dockerenv") or os.path.exists("/app/app")
if _IN_CONTAINER and "/app" not in sys.path:
    sys.path.insert(0, "/app")
if os.path.dirname(__file__) not in sys.path:
    sys.path.insert(0, os.path.dirname(__file__))

from smoke_helpers import (
    NexplaneClient,
    get_connector_creds_from_db,
    log,
    fail,
    make_base_parser,
    _get_aws_boto3_client,
)

SMOKE_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _wait_cr(client, cr_id, timeout=300, poll=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.get(f"/change-requests/{cr_id}")
        status = cr.get("status")
        if status in ("completed", "failed", "rollback_completed", "rollback_failed"):
            return cr
        time.sleep(poll)
    fail(f"CR {cr_id} did not complete within {timeout}s")


def _execute_cr(client, cr_id, timeout=300):
    client.post(f"/change-requests/{cr_id}/execute")
    return _wait_cr(client, cr_id, timeout=timeout)


def _rollback_cr(client, cr_id, extra_params=None, timeout=300):
    body = extra_params or {}
    client.post(f"/change-requests/{cr_id}/rollback", json=body)
    return _wait_cr(client, cr_id, timeout=timeout)


def _create_cr(client, change_type, params, connector_id=None, asset_ids=None):
    body = {
        "title": f"smoke-{change_type}-{uuid.uuid4().hex[:6]}",
        "change_type": change_type,
        "desired_outcome": params,
    }
    if connector_id:
        body["connector_id"] = connector_id
    if asset_ids:
        body["target_asset_ids"] = asset_ids
    cr = client.post("/change-requests", json=body)
    # plan
    client.post(f"/change-requests/{cr['id']}/plan")
    _wait_cr_planned(client, cr["id"])
    # submit for approval then approve
    client.post(f"/change-requests/{cr['id']}/submit-for-approval")
    client.post(f"/change-requests/{cr['id']}/approve", json={"decision": "approved"})
    return cr["id"]


def _wait_cr_planned(client, cr_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.get(f"/change-requests/{cr_id}")
        if cr.get("status") in ("planned", "awaiting_approval"):
            return
        if cr.get("status") in ("failed",):
            fail(f"CR {cr_id} failed during planning")
        time.sleep(3)
    fail(f"CR {cr_id} did not reach planned state within {timeout}s")


def _get_execution_result(cr):
    runs = cr.get("execution_runs") or []
    if runs:
        return (runs[0].get("result") or {})
    result = cr.get("result") or {}
    return result


def _get_artifact_refs(cr):
    result = _get_execution_result(cr)
    refs = result.get("artifact_refs")
    if refs:
        return refs
    for step in (result.get("execution") or {}).get("steps", []):
        refs = (step.get("result") or {}).get("artifact_refs")
        if refs:
            return refs
    return {}


# ── PHASE_GCS_BACKEND ─────────────────────────────────────────────────────────

def run_phase_gcs_backend(args):
    log("PHASE_GCS_BACKEND: testing GCS backend in-process against real GCP infra")

    # Load GCP connector creds from DB
    gcp_creds = get_connector_creds_from_db("gcp")
    if not gcp_creds:
        fail("PHASE_GCS_BACKEND: no GCP connector creds found in DB — register a GCP connector first")

    bucket = (
        getattr(args, "gcs_bucket", None)
        or gcp_creds.get("bucket")
        or gcp_creds.get("gcs_bucket")
    )
    if not bucket:
        fail(
            "PHASE_GCS_BACKEND: GCP connector creds missing 'bucket'/'gcs_bucket' field and "
            "--gcs-bucket not provided. Pass --gcs-bucket <name> or add 'gcs_bucket' to the "
            "GCP connector credentials."
        )

    key_json = (
        gcp_creds.get("service_account_key_json")
        or gcp_creds.get("service_account_key")
        or gcp_creds.get("credentials_json")
    )
    config = {"bucket": bucket}
    if key_json:
        config["service_account_key_json"] = key_json

    import asyncio
    from app.connectors.executors.nexplane_agent.storage_backends import gcs

    async def _run():
        test_key = "smoke/gcs_backend_test.txt"
        test_data = b"nexplane-gcs-smoke"
        expected_uri = f"gcs://{bucket}/{test_key}"

        # put_bytes
        uri = await gcs.put_bytes(test_key, test_data, config)
        assert uri == expected_uri, f"put_bytes returned {uri!r}, expected {expected_uri!r}"
        log(f"  put_bytes → {uri}")

        # download
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        try:
            await gcs.download(uri, tmp_path, config)
            with open(tmp_path, "rb") as f:
                contents = f.read()
            assert contents == test_data, f"downloaded {contents!r}, expected {test_data!r}"
            log("  download → contents match")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        # delete
        await gcs.delete(uri, config)
        log("  delete → no exception")

        # verify deletion
        with tempfile.NamedTemporaryFile(delete=False) as tmp2:
            tmp2_path = tmp2.name
        try:
            raised = False
            try:
                await gcs.download(uri, tmp2_path, config)
            except Exception:
                raised = True
            assert raised, "expected exception downloading deleted blob, got none"
            log("  verify deletion → raises as expected")
        finally:
            try:
                os.unlink(tmp2_path)
            except Exception:
                pass

        # delete_prefix (already deleted — should return 0)
        result = await gcs.delete_prefix("smoke/", config)
        assert result["deleted_count"] == 0, f"expected deleted_count=0, got {result}"
        log(f"  delete_prefix('smoke/') → {result}")

        # list_prefix
        await gcs.put_bytes("smoke/list_test_1.txt", b"a", config)
        await gcs.put_bytes("smoke/list_test_2.txt", b"b", config)
        uris = await gcs.list_prefix("smoke/", config)
        assert len(uris) >= 2, f"expected ≥2 URIs, got {uris}"
        log(f"  list_prefix('smoke/') → {len(uris)} objects")
        # cleanup
        await gcs.delete_prefix("smoke/", config)
        log("  cleanup → smoke/ prefix cleared")

    asyncio.run(_run())
    log("PHASE_GCS_BACKEND: PASSED")


# ── PHASE_STORAGE_RESTORE ─────────────────────────────────────────────────────

def run_phase_storage_restore(client, aws_connector_id, instance_id, args):
    log("PHASE_STORAGE_RESTORE: CR lifecycle — storage_sync → storage_restore → rollback")

    aws_creds = get_connector_creds_from_db("aws")
    s3_client = boto3.client(
        "s3",
        region_name=SMOKE_REGION,
        aws_access_key_id=aws_creds.get("access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key"),
    )

    # Setup: ensure smoke bucket and source object exist
    smoke_bucket = aws_creds.get("bucket") or aws_creds.get("s3_bucket") or "nexplane-smoke-backups"
    # Create bucket if it doesn't exist
    try:
        s3_client.head_bucket(Bucket=smoke_bucket)
        log(f"  bucket s3://{smoke_bucket} exists")
    except Exception:
        log(f"  creating bucket s3://{smoke_bucket}")
        try:
            if SMOKE_REGION == "us-east-1":
                s3_client.create_bucket(Bucket=smoke_bucket)
            else:
                s3_client.create_bucket(
                    Bucket=smoke_bucket,
                    CreateBucketConfiguration={"LocationConstraint": SMOKE_REGION},
                )
        except Exception as e:
            fail(f"PHASE_STORAGE_RESTORE: could not create bucket {smoke_bucket}: {e}")
    source_key = "smoke/storage_restore_source.txt"
    s3_client.put_object(Bucket=smoke_bucket, Key=source_key, Body=b"nexplane-storage-restore-smoke")
    log(f"  seeded s3://{smoke_bucket}/{source_key}")

    # CR1: storage_sync backup
    source_storage_id = aws_creds.get("backup_storage_id") or _get_or_create_backup_storage(
        client, aws_connector_id, smoke_bucket, "smoke/"
    )
    cr1_id = _create_cr(
        client,
        "server_backup",
        {
            "capture_strategy": "storage_sync",
            "source_storage_id": source_storage_id,
            "backup_storage_id": source_storage_id,
            "source_prefix": "smoke/",
        },
        connector_id=aws_connector_id,
    )
    cr1 = _execute_cr(client, cr1_id, timeout=120)
    if cr1["status"] != "completed":
        fail(f"CR1 storage_sync failed: {cr1.get('status')}")
    refs = _get_artifact_refs(cr1)
    assert refs.get("dest_prefix"), f"CR1 artifact_refs missing dest_prefix: {refs}"
    log(f"  CR1 storage_sync completed, dest_prefix={refs['dest_prefix']}")

    # CR2: storage_restore
    cr2_id = _create_cr(
        client,
        "restore_server",
        {
            "restore_strategy": "storage_restore",
            "source_backup_cr_id": cr1_id,
            "target_storage_type": "s3",
            "target_bucket": smoke_bucket,
            "target_prefix": "smoke/restored/",
        },
        connector_id=aws_connector_id,
    )
    cr2 = _execute_cr(client, cr2_id, timeout=120)
    if cr2["status"] != "completed":
        fail(f"CR2 storage_restore failed: {cr2.get('status')}")
    cr2_result = _get_execution_result(cr2)
    restored_uris = cr2_result.get("restored_uris", [])
    assert len(restored_uris) > 0, f"CR2 returned no restored_uris: {cr2_result}"
    log(f"  CR2 storage_restore completed, {len(restored_uris)} objects restored")

    # SDK verify: object exists at target
    for uri in restored_uris:
        # uri is s3://bucket/key
        parts = uri.replace("s3://", "").split("/", 1)
        b, k = parts[0], parts[1]
        try:
            s3_client.head_object(Bucket=b, Key=k)
            log(f"  SDK verify: {uri} exists ✓")
        except Exception as e:
            fail(f"SDK verify failed for {uri}: {e}")

    # Rollback LIFO: CR2 first, then CR1
    rb2 = _rollback_cr(client, cr2_id)
    if rb2["status"] not in ("rollback_completed",):
        fail(f"CR2 rollback failed: {rb2.get('status')}")
    # Verify objects gone
    for uri in restored_uris:
        parts = uri.replace("s3://", "").split("/", 1)
        b, k = parts[0], parts[1]
        try:
            s3_client.head_object(Bucket=b, Key=k)
            fail(f"Object {uri} still exists after rollback")
        except s3_client.exceptions.ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                log(f"  SDK verify rollback: {uri} gone ✓")
            else:
                raise
    log("  CR2 rollback completed, objects deleted")

    rb1 = _rollback_cr(client, cr1_id)
    if rb1["status"] not in ("rollback_completed",):
        fail(f"CR1 rollback failed: {rb1.get('status')}")
    log("  CR1 rollback completed")

    log("PHASE_STORAGE_RESTORE: PASSED")


def _get_or_create_backup_storage(client, connector_id, bucket, prefix):
    """Get or create a backup_storage record for the smoke bucket."""
    storages = client.get("/backup-storages")
    if isinstance(storages, dict):
        storages = storages.get("items", storages.get("data", []))
    if not isinstance(storages, list):
        storages = []
    for s in storages:
        if s.get("bucket") == bucket:
            return s["id"]
    storage = client.post("/backup-storages", json={
        "name": f"smoke-s3-{bucket[:20]}",
        "storage_type": "s3",
        "connector_id": connector_id,
        "bucket": bucket,
        "prefix": prefix,
    })
    return storage["id"]


# ── PHASE_DB_RESTORE ──────────────────────────────────────────────────────────

def _wait_docker(cmd, timeout=60, poll=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(cmd, capture_output=True, shell=True)
        if result.returncode == 0:
            return
        time.sleep(poll)
    fail(f"Timed out waiting for: {cmd}")


def run_phase_db_restore_postgres(client, ssh_connector_id, rds_host, rds_user, rds_password, rds_db, args):
    log("  DB_RESTORE/postgres: seeding RDS test table")
    # Seed via SSH to agent
    seed_sql = (
        "DROP TABLE IF EXISTS nexplane_restore_smoke; "
        "CREATE TABLE nexplane_restore_smoke (id serial, val text); "
        "INSERT INTO nexplane_restore_smoke VALUES (1, 'smoke');"
    )
    _run_psql_via_api(client, ssh_connector_id, rds_host, rds_user, rds_password, rds_db, seed_sql)

    aws_creds = get_connector_creds_from_db("aws")
    smoke_bucket = aws_creds.get("bucket") or "nexplane-smoke-backups"

    # CR1: database_dump
    cr1_id = _create_cr(
        client,
        "server_backup",
        {
            "capture_strategy": "database_dump",
            "db_type": "postgres",
            "db_host": rds_host,
            "db_port": 5432,
            "database_name": rds_db,
            "db_user": rds_user,
            "db_password": rds_password,
            "backup_storage_id": _get_or_create_backup_storage_by_type(
                client, "s3", smoke_bucket, "backups/smoke/"
            ),
        },
        connector_id=ssh_connector_id,
    )
    cr1 = _execute_cr(client, cr1_id, timeout=180)
    if cr1["status"] != "completed":
        fail(f"Postgres CR1 database_dump failed: {cr1.get('status')}")
    refs = _get_artifact_refs(cr1)
    assert refs.get("artifact_uri"), f"CR1 missing artifact_uri: {refs}"
    log(f"  CR1 database_dump completed, artifact={refs['artifact_uri']}")

    # CR2: database_restore
    copy_db = "nexplane_restore_smoke_copy"
    cr2_id = _create_cr(
        client,
        "restore_server",
        {
            "restore_strategy": "database_restore",
            "source_backup_cr_id": cr1_id,
            "target_db_host": rds_host,
            "target_db_port": 5432,
            "target_db_name": copy_db,
            "target_db_user": rds_user,
            "target_db_password": rds_password,
        },
        connector_id=ssh_connector_id,
    )
    cr2 = _execute_cr(client, cr2_id, timeout=300)
    if cr2["status"] != "completed":
        fail(f"Postgres CR2 database_restore failed: {cr2.get('status')}")
    log(f"  CR2 database_restore completed")

    # Verify row count
    count = _query_count_via_api(
        client, ssh_connector_id, rds_host, rds_user, rds_password, copy_db,
        "SELECT COUNT(*) FROM nexplane_restore_smoke"
    )
    assert count >= 1, f"Expected ≥1 rows in copy DB, got {count}"
    log(f"  SDK verify: {count} rows in {copy_db}.nexplane_restore_smoke ✓")

    # Rollback LIFO
    rb2 = _rollback_cr(client, cr2_id, extra_params={
        "confirm_drop": True,
        "target_db_host": rds_host,
        "target_db_port": 5432,
        "target_db_name": copy_db,
        "target_db_user": rds_user,
        "target_db_password": rds_password,
        "db_type": "postgres",
    })
    if rb2["status"] not in ("rollback_completed",):
        fail(f"Postgres CR2 rollback failed: {rb2.get('status')}")
    log(f"  CR2 rollback completed — {copy_db} dropped")

    rb1 = _rollback_cr(client, cr1_id)
    if rb1["status"] not in ("rollback_completed",):
        fail(f"Postgres CR1 rollback failed: {rb1.get('status')}")
    log("  CR1 rollback completed — artifact deleted")
    log("  DB_RESTORE/postgres: PASSED")


def run_phase_db_restore_mysql(client, ssh_connector_id, args):
    log("  DB_RESTORE/mysql: launching Docker MySQL container")
    subprocess.run("docker rm -f smoke-mysql 2>/dev/null || true", shell=True)
    subprocess.run(
        "docker run -d --name smoke-mysql "
        "-e MYSQL_ROOT_PASSWORD=smokepass -e MYSQL_DATABASE=smokedb "
        "-p 3307:3306 mysql:8",
        shell=True, check=True,
    )
    log("  waiting for MySQL to be ready (~30s)")
    _wait_docker(
        "docker exec smoke-mysql mysql -h 127.0.0.1 -P 3306 -uroot -psmokepass smokedb -e 'SELECT 1'",
        timeout=90,
    )

    seed_cmd = (
        "docker exec smoke-mysql mysql -h 127.0.0.1 -P 3306 -uroot -psmokepass smokedb "
        "-e \"CREATE TABLE IF NOT EXISTS t (id int); INSERT INTO t VALUES (42)\""
    )
    subprocess.run(seed_cmd, shell=True, check=True)
    log("  seeded smokedb.t")

    aws_creds = get_connector_creds_from_db("aws")
    smoke_bucket = aws_creds.get("bucket") or "nexplane-smoke-backups"

    cr3_id = _create_cr(
        client,
        "server_backup",
        {
            "capture_strategy": "database_dump",
            "db_type": "mysql",
            "db_host": "127.0.0.1",
            "db_port": 3307,
            "database_name": "smokedb",
            "db_user": "root",
            "db_password": "smokepass",
            "backup_storage_id": _get_or_create_backup_storage_by_type(
                client, "s3", smoke_bucket, "backups/smoke/"
            ),
        },
        connector_id=ssh_connector_id,
    )
    cr3 = _execute_cr(client, cr3_id, timeout=180)
    if cr3["status"] != "completed":
        subprocess.run("docker rm -f smoke-mysql", shell=True)
        fail(f"MySQL CR3 database_dump failed: {cr3.get('status')}")
    log("  CR3 database_dump (mysql) completed")

    cr4_id = _create_cr(
        client,
        "restore_server",
        {
            "restore_strategy": "database_restore",
            "source_backup_cr_id": cr3_id,
            "target_db_host": "127.0.0.1",
            "target_db_port": 3307,
            "target_db_name": "smokedb_copy",
            "target_db_user": "root",
            "target_db_password": "smokepass",
        },
        connector_id=ssh_connector_id,
    )
    cr4 = _execute_cr(client, cr4_id, timeout=300)
    if cr4["status"] != "completed":
        subprocess.run("docker rm -f smoke-mysql", shell=True)
        fail(f"MySQL CR4 database_restore failed: {cr4.get('status')}")
    log("  CR4 database_restore (mysql) completed")

    # Verify
    result = subprocess.run(
        "docker exec smoke-mysql mysql -h 127.0.0.1 -P 3306 -uroot -psmokepass smokedb_copy "
        "-e 'SELECT COUNT(*) FROM t'",
        shell=True, capture_output=True, text=True,
    )
    assert result.returncode == 0 and "1" in result.stdout, f"MySQL verify failed: {result.stdout}"
    log("  SDK verify: row count ✓")

    # Rollback
    rb4 = _rollback_cr(client, cr4_id, extra_params={
        "confirm_drop": True,
        "target_db_host": "127.0.0.1",
        "target_db_port": 3307,
        "target_db_name": "smokedb_copy",
        "target_db_user": "root",
        "target_db_password": "smokepass",
        "db_type": "mysql",
    })
    if rb4["status"] not in ("rollback_completed",):
        fail(f"MySQL CR4 rollback failed: {rb4.get('status')}")
    rb3 = _rollback_cr(client, cr3_id)
    if rb3["status"] not in ("rollback_completed",):
        fail(f"MySQL CR3 rollback failed: {rb3.get('status')}")
    subprocess.run("docker rm -f smoke-mysql", shell=True)
    log("  DB_RESTORE/mysql: PASSED")


def run_phase_db_restore_mongodb(client, ssh_connector_id, args):
    log("  DB_RESTORE/mongodb: launching Docker MongoDB container")
    subprocess.run("docker rm -f smoke-mongo 2>/dev/null || true", shell=True)
    subprocess.run(
        "docker run -d --name smoke-mongo -p 27018:27017 mongo:6",
        shell=True, check=True,
    )
    log("  waiting for MongoDB to be ready (~15s)")
    time.sleep(15)

    seed_cmd = (
        "docker exec smoke-mongo mongosh --port 27017 "
        "--eval \"db.getSiblingDB('smokedb').col.insertOne({x:1})\""
    )
    subprocess.run(seed_cmd, shell=True, check=True)
    log("  seeded smokedb.col")

    aws_creds = get_connector_creds_from_db("aws")
    smoke_bucket = aws_creds.get("bucket") or "nexplane-smoke-backups"

    cr5_id = _create_cr(
        client,
        "server_backup",
        {
            "capture_strategy": "database_dump",
            "db_type": "mongodb",
            "db_host": "127.0.0.1",
            "db_port": 27018,
            "database_name": "smokedb",
            "db_user": "",
            "db_password": "",
            "backup_storage_id": _get_or_create_backup_storage_by_type(
                client, "s3", smoke_bucket, "backups/smoke/"
            ),
        },
        connector_id=ssh_connector_id,
    )
    cr5 = _execute_cr(client, cr5_id, timeout=180)
    if cr5["status"] != "completed":
        subprocess.run("docker rm -f smoke-mongo", shell=True)
        fail(f"MongoDB CR5 database_dump failed: {cr5.get('status')}")
    log("  CR5 database_dump (mongodb) completed")

    cr6_id = _create_cr(
        client,
        "restore_server",
        {
            "restore_strategy": "database_restore",
            "source_backup_cr_id": cr5_id,
            "target_db_host": "127.0.0.1",
            "target_db_port": 27018,
            "target_db_name": "smokedb_copy",
            "target_db_user": "",
            "target_db_password": "",
        },
        connector_id=ssh_connector_id,
    )
    cr6 = _execute_cr(client, cr6_id, timeout=300)
    if cr6["status"] != "completed":
        subprocess.run("docker rm -f smoke-mongo", shell=True)
        fail(f"MongoDB CR6 database_restore failed: {cr6.get('status')}")
    log("  CR6 database_restore (mongodb) completed")

    # Verify doc count
    result = subprocess.run(
        "docker exec smoke-mongo mongosh --port 27017 "
        "--eval \"db.getSiblingDB('smokedb_copy').col.countDocuments({})\"",
        shell=True, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"MongoDB verify failed: {result.stderr}"
    log(f"  SDK verify: {result.stdout.strip()} docs in smokedb_copy.col ✓")

    # Rollback
    rb6 = _rollback_cr(client, cr6_id, extra_params={
        "confirm_drop": True,
        "target_db_host": "127.0.0.1",
        "target_db_port": 27018,
        "target_db_name": "smokedb_copy",
        "target_db_user": "",
        "target_db_password": "",
        "db_type": "mongodb",
    })
    if rb6["status"] not in ("rollback_completed",):
        fail(f"MongoDB CR6 rollback failed: {rb6.get('status')}")
    rb5 = _rollback_cr(client, cr5_id)
    if rb5["status"] not in ("rollback_completed",):
        fail(f"MongoDB CR5 rollback failed: {rb5.get('status')}")
    subprocess.run("docker rm -f smoke-mongo", shell=True)
    log("  DB_RESTORE/mongodb: PASSED")


def run_phase_db_restore(client, aws_connector_id, instance_id, args):
    log("PHASE_DB_RESTORE: database_dump + database_restore for Postgres/MySQL/MongoDB")

    # Locate SSH connector (nexplane_agent connector for the smoke EC2 instance)
    ssh_connector_id = _get_or_create_ssh_connector(client, instance_id, aws_connector_id)

    # Postgres sub-phase: use existing RDS instance from smoke infra
    rds_host = os.getenv("SMOKE_RDS_HOST", "")
    rds_user = os.getenv("SMOKE_RDS_USER", "postgres")
    rds_password = os.getenv("SMOKE_RDS_PASSWORD", "")
    rds_db = os.getenv("SMOKE_RDS_DB", "nexplanedb")

    if rds_host and rds_password:
        run_phase_db_restore_postgres(
            client, ssh_connector_id, rds_host, rds_user, rds_password, rds_db, args
        )
    else:
        log("  DB_RESTORE/postgres: skipped (set SMOKE_RDS_HOST + SMOKE_RDS_PASSWORD env vars to enable)")

    run_phase_db_restore_mysql(client, ssh_connector_id, args)
    run_phase_db_restore_mongodb(client, ssh_connector_id, args)

    log("PHASE_DB_RESTORE: PASSED")


# ── Connector helpers ─────────────────────────────────────────────────────────

def _get_or_create_ssh_connector(client, instance_id, aws_connector_id):
    """Return or create a nexplane_agent SSH connector for the smoke EC2 instance."""
    # Check for existing connector named nexplane-smoke-ssh
    connectors = client.get("/connectors")
    if isinstance(connectors, dict):
        connectors = connectors.get("items", connectors.get("data", []))
    if not isinstance(connectors, list):
        connectors = []
    for c in connectors:
        if c.get("name") == "nexplane-smoke-ssh" and c.get("connector_type") == "nexplane_agent":
            return c["id"]

    # Get instance private IP
    aws_creds = get_connector_creds_from_db("aws")
    ec2 = boto3.client(
        "ec2",
        region_name=SMOKE_REGION,
        aws_access_key_id=aws_creds.get("access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key"),
    )
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    # Get SSH private key from SSM
    ssm = boto3.client(
        "ssm",
        region_name=SMOKE_REGION,
        aws_access_key_id=aws_creds.get("access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key"),
    )
    try:
        pk = ssm.get_parameter(Name="/nexplane/smoke/ssh-private-key", WithDecryption=True)["Parameter"]["Value"]
    except Exception:
        pk = ""

    conn = client.post("/connectors", json={
        "name": "nexplane-smoke-ssh",
        "connector_type": "nexplane_agent",
        "description": "smoke SSH connector for database_dump/restore tests",
    })
    conn_id = conn["id"]
    client.put(f"/connectors/{conn_id}/credentials", json={
        "credentials": {
            "hostname": private_ip,
            "username": "ec2-user",
            "private_key": pk,
        }
    })
    return conn_id


def _run_psql_via_api(client, ssh_connector_id, host, user, password, db, sql):
    """Execute SQL against Postgres by running a psql command via a CR on the SSH connector."""
    cr_id = _create_cr(
        client,
        "ssm_command",
        {
            "instance_id": "self",
            "command": f"PGPASSWORD={password} psql -h {host} -U {user} {db} -c \"{sql}\"",
        },
        connector_id=ssh_connector_id,
    )
    cr = _execute_cr(client, cr_id, timeout=60)
    if cr["status"] != "completed":
        fail(f"psql seed CR failed: {cr.get('status')}")


def _query_count_via_api(client, ssh_connector_id, host, user, password, db, sql):
    """Execute a count query via psql over SSH and return the integer result."""
    cr_id = _create_cr(
        client,
        "ssm_command",
        {
            "instance_id": "self",
            "command": (
                f"PGPASSWORD={password} psql -h {host} -U {user} {db} -t -c \"{sql}\""
            ),
        },
        connector_id=ssh_connector_id,
    )
    cr = _execute_cr(client, cr_id, timeout=60)
    result = _get_execution_result(cr)
    output = result.get("output") or result.get("stdout") or ""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            return int(stripped)
    return 0


def _get_or_create_backup_storage_by_type(client, storage_type, bucket, prefix):
    storages = client.get("/backup-storages")
    if isinstance(storages, dict):
        storages = storages.get("items", storages.get("data", []))
    if not isinstance(storages, list):
        storages = []
    for s in storages:
        if s.get("bucket") == bucket and s.get("storage_type") == storage_type:
            return s["id"]
    aws_creds = get_connector_creds_from_db("aws")
    storage = client.post("/backup-storages", json={
        "name": f"smoke-{storage_type}-{bucket[:20]}",
        "storage_type": storage_type,
        "bucket": bucket,
        "prefix": prefix,
        "config": {
            "bucket": bucket,
            "aws_access_key_id": aws_creds.get("access_key_id", ""),
            "aws_secret_access_key": aws_creds.get("secret_access_key", ""),
            "region": SMOKE_REGION,
        },
    })
    return storage["id"]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = make_base_parser("Backup strategies restore live smoke test")
    parser.add_argument(
        "--phases",
        default="GCS_BACKEND,STORAGE_RESTORE,DB_RESTORE",
        help="Comma-separated phases to run",
    )
    parser.add_argument("--aws-connector-id", default="666e237d-bfcf-43a5-ae24-f1a0b4f2c5cc")
    parser.add_argument("--instance-id", default="i-050bab85006f0b73c")
    parser.add_argument("--gcs-bucket", default="", help="GCS bucket for GCS_BACKEND phase (overrides connector creds)")
    args = parser.parse_args()

    phases = [p.strip().upper() for p in args.phases.split(",")]

    client = None
    if any(p in phases for p in ("STORAGE_RESTORE", "DB_RESTORE")):
        client = NexplaneClient(args.base_url, args.email, args.password)

    passed = []
    failed_phases = []

    for phase in phases:
        try:
            if phase == "GCS_BACKEND":
                run_phase_gcs_backend(args)
            elif phase == "STORAGE_RESTORE":
                run_phase_storage_restore(client, args.aws_connector_id, args.instance_id, args)
            elif phase == "DB_RESTORE":
                run_phase_db_restore(client, args.aws_connector_id, args.instance_id, args)
            else:
                log(f"Unknown phase: {phase}", ok=False)
                failed_phases.append(phase)
                continue
            passed.append(phase)
        except SystemExit:
            failed_phases.append(phase)
        except Exception as e:
            log(f"PHASE {phase} raised exception: {e}", ok=False)
            import traceback
            traceback.print_exc()
            failed_phases.append(phase)

    print()
    if failed_phases:
        print(f"FAILED PHASES: {', '.join(failed_phases)}")
        sys.exit(1)
    else:
        print(f"ALL SELECTED PHASES PASSED: {', '.join(passed)}")


if __name__ == "__main__":
    main()
