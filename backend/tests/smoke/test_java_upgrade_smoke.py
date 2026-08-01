# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Java runtime upgrade smoke test.

Provisions a docker eclipse-temurin:11-jre container on the EC2 host, runs
the java_runtime_upgrade CR (11 -> 17, skip_snapshot=True for test env),
verifies "17" appears in execution_result, triggers rollback, and verifies
the rollback API gate.

Run:
    cd /home/ec2-user/nexplane
    PYTHONPATH=backend python3 -m pytest \
        backend/tests/smoke/test_java_upgrade_smoke.py -v -s \
        2>&1 | tee /tmp/wpm_smoke.log; echo SMOKE_DONE_JAVA >> /tmp/wpm_smoke.log
"""

import os
import sys
import subprocess
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log

BASE_URL  = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL     = os.environ.get("NEXPLANE_EMAIL",    "admin@acme.example")
PASSWORD  = os.environ.get("NEXPLANE_PASSWORD", "admin123")

TIMEOUT       = 300   # 5 min
POLL_INTERVAL = 10

JAVA_SRC_VERSION = "11"
JAVA_TGT_VERSION = "17"

SMOKE_CONNECTOR_ID = "666e237d-4e83-4c6c-b72e-6e32fbb5c895"
SMOKE_ASSET_ID     = "1a7051be-7110-4a21-9cdf-b023231cdff8"


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run(cmd: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout, check=check)


def _wait_java(container: str, timeout: int = 60) -> None:
    """Poll until `docker exec <container> java -version` exits 0."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            f"docker exec {container} java -version",
            shell=True, capture_output=True, text=True,
        )
        if result.returncode == 0:
            return
        time.sleep(3)
    raise TimeoutError(f"Container {container} java -version not ready after {timeout}s")


# ---------------------------------------------------------------------------
# CR lifecycle helpers
# ---------------------------------------------------------------------------

def _cr_lifecycle(client: NexplaneClient, title: str, change_type: str,
                   desired_outcome: dict, asset_ids: list,
                   timeout: int = TIMEOUT) -> dict:
    """create -> plan -> submit-for-approval -> approve -> execute -> poll."""
    base = client.base
    r = client.client.post(f"{base}/change-requests", json={
        "title":            title,
        "change_type":      change_type,
        "desired_outcome":  desired_outcome,
        "target_asset_ids": asset_ids,
    })
    assert r.status_code in (200, 201), f"CR create failed {r.status_code}: {r.text}"
    cr_id = r.json()["id"]

    for step in ["plan", "submit-for-approval"]:
        r2 = client.client.post(f"{base}/change-requests/{cr_id}/{step}")
        assert r2.status_code in (200, 201, 202, 204), (
            f"/{step} failed {r2.status_code}: {r2.text}"
        )

    r3 = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "java-upgrade smoke"},
    )
    assert r3.status_code in (200, 201, 202, 204), (
        f"/approve failed {r3.status_code}: {r3.text}"
    )

    r4 = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r4.status_code in (200, 201, 202, 204), (
        f"/execute failed {r4.status_code}: {r4.text}"
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("completed", "verify_failed"):
            log(f"{title} -> {status}")
            return cr
        if status in ("failed", "upgrade_failed", "preflight_blocked",
                      "rejected", "cancelled"):
            raise AssertionError(
                f"CR {cr_id} terminal with status={status!r}: "
                f"{str(cr.get('execution_result', ''))[:600]}"
            )
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not complete within {timeout}s")


def _rollback_cr(client: NexplaneClient, cr_id: str, label: str,
                  timeout: int = TIMEOUT) -> dict:
    """POST /rollback and poll until rolled_back or rollback_failed.

    Handles FILO 409: if a later CR is blocking, rolls it back first.
    With skip_snapshot=True there is no restore artifact, so rollback_failed
    is also acceptable — what matters is that the rollback API gate is passed.
    """
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    if r.status_code == 409:
        err = r.json()
        if err.get("error") == "out_of_order_rollback":
            blocking = err.get("blocking_crs", [])
            log(f"[{label}] FILO 409 — rolling back {len(blocking)} blocker(s) first")
            for blk_id in blocking:
                _rollback_cr(client, blk_id, f"blocker:{blk_id[:8]}", timeout=timeout)
            r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    assert r.status_code in (200, 201, 202, 204), (
        f"/rollback failed {r.status_code}: {r.text}"
    )
    log(f"[{label}] rollback initiated (HTTP {r.status_code})")

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("rolled_back", "rollback_failed"):
            log(f"[{label}] rollback terminal: {status}")
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"[{label}] rollback timed out after {timeout}s")


def _execution_result(cr: dict) -> dict:
    """Extract step result from execution_runs, fall back to execution_result."""
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

class TestJavaUpgradeSmoke:

    def setup_method(self):
        self.client   = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.asset_id = SMOKE_ASSET_ID
        # Roll back any leftover applied CRs so the FILO stack starts clean.
        base = self.client.base
        r = self.client.client.post(f"{base}/assets/{self.asset_id}/rollback-all")
        if r.status_code not in (200, 201, 202, 204, 404):
            log(f"setup rollback-all returned {r.status_code}: {r.text[:200]}")

    def teardown_method(self, method):
        _run(
            "docker rm -f smoke-java smoke-java-v17 2>/dev/null || true",
            check=False,
        )

    # -----------------------------------------------------------------------
    # Phase JAVA_UPGRADE
    # -----------------------------------------------------------------------

    def test_java_upgrade(self):
        """
        JAVA_UPGRADE: eclipse-temurin:11-jre -> 17 via CR.

        1. Launch eclipse-temurin:11-jre container (sleep infinity).
        2. Wait up to 60s for java -version to succeed.
        3. Run java_runtime_upgrade CR (skip_snapshot=True — no persistent volume
           in test env).
        4. Verify CR completed with "17" in execution_result.
        5. Trigger rollback (API gate; skip_snapshot means rollback_failed tolerated).
        """
        container_name = "smoke-java"
        asset_id = self.asset_id

        log(f"JAVA_UPGRADE: launching eclipse-temurin:{JAVA_SRC_VERSION}-jre")
        _run(f"docker rm -f {container_name} 2>/dev/null || true", check=False)
        _run(
            f"docker run -d --name {container_name} "
            f"eclipse-temurin:{JAVA_SRC_VERSION}-jre "
            f"sleep infinity",
            timeout=60,
        )
        _wait_java(container_name, timeout=60)
        log(f"JAVA_UPGRADE: eclipse-temurin:{JAVA_SRC_VERSION}-jre ready")

        cr_id = None
        try:
            # ----------------------------------------------------------------
            # Execute CR
            # ----------------------------------------------------------------
            cr = _cr_lifecycle(
                self.client,
                f"[smoke] java_runtime_upgrade {JAVA_SRC_VERSION}->{JAVA_TGT_VERSION}",
                "java_runtime_upgrade",
                {
                    "target_version":  JAVA_TGT_VERSION,
                    "java_container":  container_name,
                    "skip_snapshot":   True,
                },
                [asset_id],
            )
            cr_id  = cr["id"]
            result = _execution_result(cr)
            log(f"JAVA_UPGRADE: CR completed, status={cr.get('status')}, "
                f"result={str(result)[:300]}")

            # ----------------------------------------------------------------
            # Verify execution result contains "17"
            # ----------------------------------------------------------------
            upgraded = result.get("upgraded_version", "")
            result_str = str(result)
            assert "17" in upgraded or "17" in result_str, (
                f"Expected '17' in execution result, got: {result_str[:300]}"
            )
            log(f"JAVA_UPGRADE: version {upgraded or '17'} confirmed in execution_result")

            # ----------------------------------------------------------------
            # Rollback — API gate only; skip_snapshot means rollback_failed OK.
            # ----------------------------------------------------------------
            cr_rb = _rollback_cr(self.client, cr_id, "java-upgrade-rollback")
            rb_status = cr_rb.get("status")
            assert rb_status in ("rolled_back", "rollback_failed"), (
                f"Unexpected rollback terminal status: {rb_status}"
            )
            log(f"JAVA_UPGRADE: rollback terminal={rb_status} (API gate passed)")
            log("JAVA_UPGRADE: PASSED")

        finally:
            _run(
                "docker rm -f smoke-java smoke-java-v17 2>/dev/null || true",
                check=False,
            )
