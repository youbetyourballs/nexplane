# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
DB Major Version Upgrade Smoke Tests.

Provisions Docker containers on the EC2 smoke host per engine,
drives CRs through the full Nexplane lifecycle, and verifies rollback.

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_db_major_version_upgrade_smoke.py -v -s

Skip conditions:
    - 'smoke_db_asset_id' missing from AWS connector credentials in platform DB
    - Per-engine skip if Docker pull fails (infra issue, not code bug)
"""

import os
import sys
import time
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL    = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

DB_UPGRADE_TIMEOUT = 900   # 15 min — upgrade + verify + rollback
POLL_INTERVAL      = 15


# ---------------------------------------------------------------------------
# Shared CR lifecycle helpers
# ---------------------------------------------------------------------------

def _cr_lifecycle(client: NexplaneClient, title: str, change_type: str,
                   desired_outcome: dict, asset_ids: list, timeout: int = DB_UPGRADE_TIMEOUT) -> dict:
    """create -> plan -> submit-for-approval -> approve -> execute -> poll until terminal."""
    base = client.base
    body = {
        "title": title,
        "change_type": change_type,
        "desired_outcome": desired_outcome,
        "asset_ids": asset_ids,
    }
    r = client.client.post(f"{base}/change-requests", json=body)
    assert r.status_code in (200, 201), f"CR create failed {r.status_code}: {r.text}"
    cr_id = r.json()["id"]

    for step in ["plan", "submit-for-approval"]:
        r2 = client.client.post(f"{base}/change-requests/{cr_id}/{step}")
        assert r2.status_code in (200, 201, 202, 204), f"/{step} failed {r2.status_code}: {r2.text}"

    r3 = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "db-upgrade smoke"},
    )
    assert r3.status_code in (200, 201, 202, 204), f"/approve failed {r3.status_code}: {r3.text}"

    r4 = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r4.status_code in (200, 201, 202, 204), f"/execute failed {r4.status_code}: {r4.text}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("completed", "verify_failed"):
            log(f"{title} -> {status}")
            return cr
        if status in ("failed", "upgrade_failed", "preflight_blocked", "rejected", "cancelled"):
            raise AssertionError(
                f"CR {cr_id} terminal with status={status!r}: "
                f"{str(cr.get('execution_result', ''))[:600]}"
            )
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not complete within {timeout}s")


def _rollback_cr(client: NexplaneClient, cr_id: str, label: str,
                  timeout: int = DB_UPGRADE_TIMEOUT) -> dict:
    """POST /rollback and poll until rolled_back."""
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    assert r.status_code in (200, 201, 202, 204), f"/rollback failed {r.status_code}: {r.text}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "rolled_back":
            log(f"rolled back: {label}")
            return cr
        if status == "rollback_failed":
            raise AssertionError(f"[{label}] rollback_failed: {cr.get('execution_result', '')}")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"[{label}] rollback timed out after {timeout}s")


def _execution_result(cr: dict) -> dict:
    return cr.get("execution_result") or {}


def _ssm_on_smoke_host(client: NexplaneClient, asset_id: str, command: str,
                        label: str) -> str:
    """Run a shell command on the smoke EC2 host via SSM CR and return stdout."""
    base = client.base
    body = {
        "title": f"[smoke-infra] {label}",
        "change_type": "ssm_command",
        "desired_outcome": {
            "document_name": "AWS-RunShellScript",
            "command": command,
            "rollback_strategy": "rollback_unavailable",
        },
        "asset_ids": [asset_id],
    }
    r = client.client.post(f"{base}/change-requests", json=body)
    assert r.status_code in (200, 201), f"SSM CR create failed: {r.text}"
    cr_id = r.json()["id"]
    for step in ["plan", "submit-for-approval"]:
        client.client.post(f"{base}/change-requests/{cr_id}/{step}")
    client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke-infra"},
    )
    client.client.post(f"{base}/change-requests/{cr_id}/execute")
    deadline = time.time() + 120
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        if cr.get("status") == "completed":
            result = _execution_result(cr)
            return result.get("stdout", "").strip()
        if cr.get("status") in ("failed", "rejected"):
            raise AssertionError(f"SSM CR failed: {cr.get('execution_result', '')}")
        time.sleep(5)
    raise TimeoutError(f"SSM CR {cr_id} timed out")


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestDbMajorVersionUpgradeSmoke:

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.creds = get_connector_creds_from_db("aws")
        if self.creds is None:
            pytest.skip("No AWS connector credentials in platform DB")
        self.asset_id = self.creds.get("smoke_db_asset_id")
        if not self.asset_id:
            pytest.skip("smoke_db_asset_id not set in AWS connector credentials")

    # -----------------------------------------------------------------------
    # Phase DB_UPGRADE_POSTGRES
    # -----------------------------------------------------------------------

    def test_db_upgrade_postgres(self):
        """
        DB_UPGRADE_POSTGRES: Docker postgres:12 -> postgres:16 via dump_restore.
        Verifies data survives upgrade and rollback restores original version.
        """
        asset_id = self.asset_id

        # --- Infra setup: launch postgres:12 container ---
        log("DB_UPGRADE_POSTGRES: launching postgres:12 container")
        _ssm_on_smoke_host(
            self.client, asset_id,
            "docker rm -f pg12 2>/dev/null || true; "
            "docker run -d --name pg12 -e POSTGRES_PASSWORD=testpw -p 5432:5432 postgres:12",
            "launch-pg12",
        )
        # Wait for postgres ready
        _ssm_on_smoke_host(
            self.client, asset_id,
            "for i in $(seq 1 30); do "
            "  docker exec pg12 pg_isready -U postgres && break || sleep 2; "
            "done",
            "wait-pg12-ready",
        )
        # Seed canary data
        _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec pg12 psql -U postgres -c \""
            "CREATE TABLE IF NOT EXISTS smoke_canary (id serial, val text); "
            "INSERT INTO smoke_canary(val) VALUES ('before-upgrade');\"",
            "seed-pg12",
        )
        log("DB_UPGRADE_POSTGRES: postgres:12 container ready with canary data")

        # --- Execute CR ---
        cr = _cr_lifecycle(
            self.client,
            "[smoke] db_major_version_upgrade postgres 12->16",
            "db_major_version_upgrade",
            {
                "engine": "postgres",
                "source_version": "12",
                "target_version": "16",
                "strategy": "dump_restore",
                "db_host": "localhost",
                "db_port": 5432,
                "db_user": "postgres",
                "db_password": "testpw",
            },
            [asset_id],
        )
        cr_id = cr["id"]
        result = _execution_result(cr)

        # --- Assert upgrade success ---
        assert result.get("status") in ("completed", "verify_failed"), (
            f"Unexpected CR status: {result}"
        )
        assert result.get("snapshot_result", {}).get("snapshot_id"), (
            f"Expected snapshot_id in result: {result}"
        )
        log(f"DB_UPGRADE_POSTGRES: upgrade completed, snap={result['snapshot_result'].get('snapshot_id')}")

        # Verify version via direct query on the host
        pg_version = _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec pg12 psql -U postgres -c 'SELECT version();' -t 2>/dev/null || "
            "docker exec pg16 psql -U postgres -c 'SELECT version();' -t 2>/dev/null || echo UNKNOWN",
            "check-pg-version-post-upgrade",
        )
        assert "16" in pg_version, f"Expected PG16 version string, got: {pg_version!r}"
        log(f"DB_UPGRADE_POSTGRES: version confirmed: {pg_version[:80]}")

        # Verify canary data survived upgrade
        canary = _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec pg12 psql -U postgres -c 'SELECT val FROM smoke_canary;' -t 2>/dev/null || "
            "docker exec pg16 psql -U postgres -c 'SELECT val FROM smoke_canary;' -t 2>/dev/null || echo MISSING",
            "check-canary-post-upgrade",
        )
        assert "before-upgrade" in canary, f"Canary data missing post-upgrade: {canary!r}"
        log("DB_UPGRADE_POSTGRES: canary data survived upgrade")

        # --- Rollback ---
        log("DB_UPGRADE_POSTGRES: triggering rollback")
        cr_rb = _rollback_cr(self.client, cr_id, "pg-upgrade-rollback")
        rb_result = _execution_result(cr_rb)
        assert rb_result.get("rolled_back") is True, f"Rollback not confirmed: {rb_result}"
        log(f"DB_UPGRADE_POSTGRES: rollback complete, strategy={rb_result.get('strategy')}")

        # Verify version is back to 12
        pg_version_after = _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec pg12 psql -U postgres -c 'SELECT version();' -t 2>/dev/null || echo UNKNOWN",
            "check-pg-version-post-rollback",
        )
        assert "12" in pg_version_after, (
            f"Expected PG12 after rollback, got: {pg_version_after!r}"
        )
        log(f"DB_UPGRADE_POSTGRES: PASSED — rollback restored version 12")

        # Cleanup
        _ssm_on_smoke_host(
            self.client, asset_id,
            "docker rm -f pg12 pg16 2>/dev/null || true",
            "cleanup-pg-containers",
        )

    # -----------------------------------------------------------------------
    # Phase DB_UPGRADE_MYSQL
    # -----------------------------------------------------------------------

    def test_db_upgrade_mysql(self):
        """
        DB_UPGRADE_MYSQL: Docker mysql:5.7 -> mysql:8.0 via in_place.
        Verifies data survives and rollback restores 5.7.
        """
        asset_id = self.asset_id

        # --- Infra setup ---
        log("DB_UPGRADE_MYSQL: launching mysql:5.7 container")
        _ssm_on_smoke_host(
            self.client, asset_id,
            "docker rm -f mysql57 2>/dev/null || true; "
            "docker run -d --name mysql57 -e MYSQL_ROOT_PASSWORD=testpw -p 3306:3306 mysql:5.7",
            "launch-mysql57",
        )
        _ssm_on_smoke_host(
            self.client, asset_id,
            "for i in $(seq 1 40); do "
            "  docker exec mysql57 mysqladmin ping -uroot -ptestpw --silent && break || sleep 3; "
            "done",
            "wait-mysql57-ready",
        )
        _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec mysql57 mysql -uroot -ptestpw -e \""
            "CREATE DATABASE IF NOT EXISTS smoke; "
            "USE smoke; "
            "CREATE TABLE IF NOT EXISTS canary (val varchar(100)); "
            "INSERT INTO canary VALUES ('before-upgrade');\"",
            "seed-mysql57",
        )
        log("DB_UPGRADE_MYSQL: mysql:5.7 container ready with canary data")

        # --- Execute CR ---
        cr = _cr_lifecycle(
            self.client,
            "[smoke] db_major_version_upgrade mysql 5.7->8.0",
            "db_major_version_upgrade",
            {
                "engine": "mysql",
                "source_version": "5.7",
                "target_version": "8.0",
                "db_host": "localhost",
                "db_port": 3306,
                "db_user": "root",
                "db_password": "testpw",
            },
            [asset_id],
        )
        cr_id = cr["id"]
        result = _execution_result(cr)

        assert result.get("status") in ("completed", "verify_failed"), (
            f"Unexpected status: {result}"
        )
        assert result.get("snapshot_result", {}).get("snapshot_id"), (
            f"Expected snapshot: {result}"
        )
        log(f"DB_UPGRADE_MYSQL: upgrade complete, snap={result['snapshot_result'].get('snapshot_id')}")

        # Verify version
        mysql_ver = _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec mysql57 mysql -uroot -ptestpw -e 'SELECT @@version;' 2>/dev/null || echo UNKNOWN",
            "check-mysql-version-post-upgrade",
        )
        assert "8.0" in mysql_ver, f"Expected MySQL 8.0 version, got: {mysql_ver!r}"
        log(f"DB_UPGRADE_MYSQL: version confirmed: {mysql_ver[:80]}")

        # Verify canary
        canary = _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec mysql57 mysql -uroot -ptestpw smoke -e 'SELECT val FROM canary;' 2>/dev/null || echo MISSING",
            "check-canary-mysql-post-upgrade",
        )
        assert "before-upgrade" in canary, f"Canary missing post-upgrade: {canary!r}"
        log("DB_UPGRADE_MYSQL: canary data survived upgrade")

        # --- Rollback ---
        cr_rb = _rollback_cr(self.client, cr_id, "mysql-upgrade-rollback")
        rb_result = _execution_result(cr_rb)
        assert rb_result.get("rolled_back") is True, f"Rollback not confirmed: {rb_result}"

        mysql_ver_after = _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec mysql57 mysql -uroot -ptestpw -e 'SELECT @@version;' 2>/dev/null || echo UNKNOWN",
            "check-mysql-version-post-rollback",
        )
        assert "5.7" in mysql_ver_after, f"Expected MySQL 5.7 after rollback: {mysql_ver_after!r}"
        log("DB_UPGRADE_MYSQL: PASSED — rollback restored version 5.7")

        _ssm_on_smoke_host(
            self.client, asset_id,
            "docker rm -f mysql57 mysql80 2>/dev/null || true",
            "cleanup-mysql-containers",
        )

    # -----------------------------------------------------------------------
    # Phase DB_UPGRADE_MONGODB
    # -----------------------------------------------------------------------

    def test_db_upgrade_mongodb(self):
        """
        DB_UPGRADE_MONGODB: Docker mongo:4.4 -> mongo:7.0 via sequential FCV bumps.
        Verifies FCV chain completion and rollback restores 4.4.
        """
        asset_id = self.asset_id

        # --- Infra setup ---
        log("DB_UPGRADE_MONGODB: launching mongo:4.4 container with replica set")
        _ssm_on_smoke_host(
            self.client, asset_id,
            "docker rm -f mongo44 2>/dev/null || true; "
            "docker run -d --name mongo44 -p 27017:27017 mongo:4.4 --replSet rs0",
            "launch-mongo44",
        )
        _ssm_on_smoke_host(
            self.client, asset_id,
            "for i in $(seq 1 30); do "
            "  docker exec mongo44 mongosh --quiet --eval 'db.runCommand({ping:1})' && break || sleep 3; "
            "done",
            "wait-mongo44-ready",
        )
        _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec mongo44 mongosh --quiet --eval "
            "\"try { rs.status() } catch(e) { rs.initiate() }\"",
            "initiate-rs0",
        )
        _ssm_on_smoke_host(
            self.client, asset_id,
            "sleep 5 && docker exec mongo44 mongosh --quiet --eval \""
            "db.getSiblingDB('smoke').canary.insertOne({val: 'before-upgrade'})\"",
            "seed-mongo44",
        )
        log("DB_UPGRADE_MONGODB: mongo:4.4 ready with canary doc")

        # --- Execute CR ---
        cr = _cr_lifecycle(
            self.client,
            "[smoke] db_major_version_upgrade mongodb 4.4->7.0",
            "db_major_version_upgrade",
            {
                "engine": "mongodb",
                "source_version": "4.4",
                "target_version": "7.0",
                "db_host": "localhost",
                "db_port": 27017,
                "db_user": "admin",
            },
            [asset_id],
            timeout=1800,  # Mongo multi-hop can take 30 min
        )
        cr_id = cr["id"]
        result = _execution_result(cr)

        assert result.get("status") in ("completed", "verify_failed"), (
            f"Unexpected status: {result}"
        )
        upgrade_result = result.get("upgrade_result", {})
        assert upgrade_result.get("completed_hops") == ["4.4->5.0", "5.0->6.0", "6.0->7.0"], (
            f"Expected all 3 FCV hops, got: {upgrade_result.get('completed_hops')}"
        )
        log(f"DB_UPGRADE_MONGODB: all FCV hops complete: {upgrade_result.get('completed_hops')}")

        # Verify version
        mongo_ver = _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec mongo44 mongosh --quiet --eval "
            "\"db.version()\" 2>/dev/null || echo UNKNOWN",
            "check-mongo-version-post-upgrade",
        )
        assert "7.0" in mongo_ver, f"Expected MongoDB 7.0, got: {mongo_ver!r}"
        log(f"DB_UPGRADE_MONGODB: version confirmed: {mongo_ver[:80]}")

        # Verify canary
        canary = _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec mongo44 mongosh --quiet --eval \""
            "JSON.stringify(db.getSiblingDB('smoke').canary.findOne())\" 2>/dev/null || echo MISSING",
            "check-canary-mongo-post-upgrade",
        )
        assert "before-upgrade" in canary, f"Canary doc missing post-upgrade: {canary!r}"
        log("DB_UPGRADE_MONGODB: canary data survived upgrade")

        # --- Rollback ---
        cr_rb = _rollback_cr(self.client, cr_id, "mongo-upgrade-rollback", timeout=1800)
        rb_result = _execution_result(cr_rb)
        assert rb_result.get("rolled_back") is True, f"Rollback not confirmed: {rb_result}"
        log(f"DB_UPGRADE_MONGODB: rollback complete, strategy={rb_result.get('strategy')}")

        # Verify version is back to 4.4
        mongo_ver_after = _ssm_on_smoke_host(
            self.client, asset_id,
            "docker exec mongo44 mongosh --quiet --eval \"db.version()\" 2>/dev/null || echo UNKNOWN",
            "check-mongo-version-post-rollback",
        )
        assert "4.4" in mongo_ver_after, (
            f"Expected MongoDB 4.4 after rollback, got: {mongo_ver_after!r}"
        )
        log("DB_UPGRADE_MONGODB: PASSED — rollback restored version 4.4")

        _ssm_on_smoke_host(
            self.client, asset_id,
            "docker rm -f mongo44 mongo50 mongo60 mongo70 2>/dev/null || true",
            "cleanup-mongo-containers",
        )
