# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
DB Major Version Upgrade Smoke Tests.

Provisions Docker containers on the EC2 smoke host via subprocess,
drives CRs through the full Nexplane lifecycle, and verifies rollback.

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_db_major_version_upgrade_smoke.py -v -s

Skip conditions:
    - 'smoke_db_asset_id' missing from AWS connector credentials in platform DB
    - No AgentRegistration for the smoke asset (agent not running)
"""

import os
import sys
import subprocess
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
# Docker infra helpers (run on the host via subprocess)
# ---------------------------------------------------------------------------

def _run(cmd: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a shell command on the EC2 host."""
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout, check=check)


def _wait_docker(check_cmd: str, timeout: int = 90, poll: int = 3) -> None:
    """Poll until check_cmd exits 0 or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(check_cmd, shell=True, capture_output=True)
        if r.returncode == 0:
            return
        time.sleep(poll)
    raise TimeoutError(f"Timed out waiting for: {check_cmd}")


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
        "target_asset_ids": asset_ids,
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
    """POST /rollback and poll until rolled_back.

    Handles FILO 409: if a later CR is blocking, rolls it back first then retries.
    """
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    if r.status_code == 409:
        err = r.json()
        if err.get("error") == "out_of_order_rollback":
            blocking = err.get("blocking_crs", [])
            log(f"[{label}] FILO 409 — rolling back {len(blocking)} blocking CR(s) first")
            for blk_id in blocking:
                _rollback_cr(client, blk_id, f"blocker:{blk_id[:8]}", timeout=timeout)
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
    """Extract the step result from the CR's execution_runs."""
    # CR API returns execution_runs[].result.execution.steps[0].result
    runs = cr.get("execution_runs") or []
    if runs:
        run_result = runs[0].get("result") or {}
        steps = run_result.get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


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
        # Roll back any leftover applied CRs so the FILO stack starts clean.
        base = self.client.base
        r = self.client.client.post(f"{base}/assets/{self.asset_id}/rollback-all")
        if r.status_code not in (200, 201, 202, 204, 404):
            log(f"setup rollback-all returned {r.status_code}: {r.text[:200]}")

    # -----------------------------------------------------------------------
    # Phase DB_UPGRADE_POSTGRES
    # -----------------------------------------------------------------------

    def test_db_upgrade_postgres(self):
        """
        DB_UPGRADE_POSTGRES: Docker postgres:12 -> postgres:16 via dump_restore.
        Verifies data survives upgrade and rollback restores original version.
        """
        asset_id = self.asset_id

        # --- Infra setup: launch postgres:12 container via subprocess ---
        log("DB_UPGRADE_POSTGRES: launching postgres:12 container")
        _run("docker rm -f pg12 2>/dev/null || true", check=False)
        _run(
            "docker run -d --name pg12 -e POSTGRES_PASSWORD=testpw -p 15432:5432 postgres:12"
        )
        _wait_docker(
            "docker exec pg12 pg_isready -U postgres",
            timeout=90,
        )
        # Seed canary data
        _run(
            "docker exec pg12 psql -U postgres -c \""
            "CREATE TABLE IF NOT EXISTS smoke_canary (id serial, val text); "
            "INSERT INTO smoke_canary(val) VALUES ('before-upgrade');\""
        )
        log("DB_UPGRADE_POSTGRES: postgres:12 container ready with canary data")

        try:
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
                    "db_port": 15432,
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
            snap_id = result["snapshot_result"].get("snapshot_id")
            log(f"DB_UPGRADE_POSTGRES: upgrade completed, snap={snap_id}")

            # Verify version via direct check on the host (agent creates container named pg16)
            pg_version = _run(
                "PGPASSWORD=testpw psql -h localhost -p 15433 -U postgres -c 'SELECT version();' -t 2>/dev/null || echo UNKNOWN",
                check=False,
            ).stdout.strip()
            assert "16" in pg_version, f"Expected PG16 version string, got: {pg_version!r}"
            log(f"DB_UPGRADE_POSTGRES: version confirmed: {pg_version[:80]}")

            # Verify canary data survived upgrade
            canary = _run(
                "PGPASSWORD=testpw psql -h localhost -p 15433 -U postgres -c 'SELECT val FROM smoke_canary;' -t 2>/dev/null || echo MISSING",
                check=False,
            ).stdout.strip()
            assert "before-upgrade" in canary, f"Canary data missing post-upgrade: {canary!r}"
            log("DB_UPGRADE_POSTGRES: canary data survived upgrade")

            # --- Rollback ---
            log("DB_UPGRADE_POSTGRES: triggering rollback")
            cr_rb = _rollback_cr(self.client, cr_id, "pg-upgrade-rollback")
            assert cr_rb.get("status") == "rolled_back", f"Rollback not confirmed: {cr_rb.get('status')}"
            rb_result = _execution_result(cr_rb)
            log(f"DB_UPGRADE_POSTGRES: rollback complete, strategy={rb_result.get('strategy')}")

            # Verify version is back to 12 (restored to original port 15432)
            pg_version_after = _run(
                "PGPASSWORD=testpw psql -h localhost -p 15432 -U postgres -c 'SELECT version();' -t 2>/dev/null || echo UNKNOWN",
                check=False,
            ).stdout.strip()
            assert "12" in pg_version_after, (
                f"Expected PG12 after rollback, got: {pg_version_after!r}"
            )
            log("DB_UPGRADE_POSTGRES: PASSED — rollback restored version 12")
        finally:
            # Cleanup
            _run("docker rm -f pg12 pg16 2>/dev/null || true", check=False)

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
        _run("docker rm -f mysql57 2>/dev/null || true", check=False)
        _run(
            "docker run -d --name mysql57 -e MYSQL_ROOT_PASSWORD=testpw -p 13306:3306 mysql:5.7"
        )
        _wait_docker(
            "docker exec mysql57 mysql -uroot -ptestpw -e 'SELECT 1' 2>/dev/null",
            timeout=120,
        )
        _run(
            "docker exec mysql57 mysql -uroot -ptestpw -e \""
            "CREATE DATABASE IF NOT EXISTS smoke; "
            "USE smoke; "
            "CREATE TABLE IF NOT EXISTS canary (val varchar(100)); "
            "INSERT INTO canary VALUES ('before-upgrade');\""
        )
        log("DB_UPGRADE_MYSQL: mysql:5.7 container ready with canary data")

        try:
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
                    "db_port": 13306,
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

            # Verify version (agent creates container named mysql8.0, mapped to port 13307)
            mysql_ver = _run(
                "mysql -h 127.0.0.1 --protocol=tcp -P 13307 -uroot -ptestpw --skip-column-names -e 'SELECT @@version;' 2>/dev/null || echo UNKNOWN",
                check=False,
            ).stdout.strip()
            assert "8.0" in mysql_ver, f"Expected MySQL 8.0 version, got: {mysql_ver!r}"
            log(f"DB_UPGRADE_MYSQL: version confirmed: {mysql_ver[:80]}")

            # Verify canary
            canary = _run(
                "mysql -h 127.0.0.1 --protocol=tcp -P 13307 -uroot -ptestpw smoke --skip-column-names -e 'SELECT val FROM canary;' 2>/dev/null || echo MISSING",
                check=False,
            ).stdout.strip()
            assert "before-upgrade" in canary, f"Canary missing post-upgrade: {canary!r}"
            log("DB_UPGRADE_MYSQL: canary data survived upgrade")

            # --- Rollback ---
            cr_rb = _rollback_cr(self.client, cr_id, "mysql-upgrade-rollback")
            assert cr_rb.get("status") == "rolled_back", f"Rollback not confirmed: {cr_rb.get('status')}"

            mysql_ver_after = _run(
                "mysql -h 127.0.0.1 --protocol=tcp -P 13306 -uroot -ptestpw --skip-column-names -e 'SELECT @@version;' 2>/dev/null || echo UNKNOWN",
                check=False,
            ).stdout.strip()
            assert "5.7" in mysql_ver_after, f"Expected MySQL 5.7 after rollback: {mysql_ver_after!r}"
            log("DB_UPGRADE_MYSQL: PASSED — rollback restored version 5.7")
        finally:
            _run("docker rm -f mysql57 mysql8.0 2>/dev/null || true", check=False)

    # -----------------------------------------------------------------------
    # Phase DB_UPGRADE_MONGODB
    # -----------------------------------------------------------------------

    def test_db_upgrade_mongodb(self):
        """
        DB_UPGRADE_MONGODB: Docker mongo:4.4 -> mongo:7.0 via dump/restore.
        Agent dumps from 4.4 (port 27117), restores into new mongo:7.0 container (port 27118).
        Rollback restores dump back into original 4.4 container.
        """
        asset_id = self.asset_id

        # --- Infra setup ---
        log("DB_UPGRADE_MONGODB: launching mongo:4.4 container")
        _run("docker rm -f mongo44 mongo70 2>/dev/null || true", check=False)
        _run("docker run -d --name mongo44 -p 27117:27017 mongo:4.4")
        _wait_docker(
            "docker exec mongo44 mongo --quiet --eval 'db.runCommand({ping:1})'",
            timeout=90,
        )
        _run(
            "docker exec mongo44 mongo --quiet --eval "
            "\"db.getSiblingDB('smoke').canary.insert({val: 'before-upgrade'})\"",
        )
        log("DB_UPGRADE_MONGODB: mongo:4.4 ready with canary doc")

        try:
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
                    "db_port": 27117,
                },
                [asset_id],
                timeout=1800,
            )
            cr_id = cr["id"]
            result = _execution_result(cr)

            assert result.get("status") in ("completed", "verify_failed"), (
                f"Unexpected status: {result}"
            )
            log(f"DB_UPGRADE_MONGODB: upgrade completed, snap={result.get('snapshot_result', {}).get('dump_path')}")

            # Upgraded container runs on port 27118 (27117 + 1), named mongo70
            mongo_ver = _run(
                "mongosh --host localhost --port 27118 --quiet --eval \"db.version()\" 2>/dev/null || echo UNKNOWN",
                check=False,
            ).stdout.strip()
            assert "7.0" in mongo_ver, f"Expected MongoDB 7.0, got: {mongo_ver!r}"
            log(f"DB_UPGRADE_MONGODB: version confirmed: {mongo_ver[:80]}")

            # Verify canary doc migrated
            canary = _run(
                "mongosh --host localhost --port 27118 smoke --quiet --eval "
                "\"JSON.stringify(db.canary.findOne())\" 2>/dev/null || echo MISSING",
                check=False,
            ).stdout.strip()
            assert "before-upgrade" in canary, f"Canary doc missing post-upgrade: {canary!r}"
            log("DB_UPGRADE_MONGODB: canary data survived upgrade")

            # --- Rollback ---
            cr_rb = _rollback_cr(self.client, cr_id, "mongo-upgrade-rollback", timeout=1800)
            assert cr_rb.get("status") == "rolled_back", f"Rollback not confirmed: {cr_rb.get('status')}"
            rb_result = _execution_result(cr_rb)
            log(f"DB_UPGRADE_MONGODB: rollback complete, strategy={rb_result.get('strategy')}")

            # Original mongo44 (port 27117) should still have 4.4 data
            mongo_ver_after = _run(
                "docker exec mongo44 mongo --quiet --eval \"db.version()\" 2>/dev/null || echo UNKNOWN",
                check=False,
            ).stdout.strip()
            assert "4.4" in mongo_ver_after, (
                f"Expected MongoDB 4.4 after rollback, got: {mongo_ver_after!r}"
            )
            log("DB_UPGRADE_MONGODB: PASSED — rollback restored version 4.4")
        finally:
            _run("docker rm -f mongo44 mongo70 2>/dev/null || true", check=False)
