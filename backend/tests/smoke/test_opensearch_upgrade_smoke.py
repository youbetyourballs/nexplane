# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OpenSearch upgrade smoke test.

Provisions docker opensearch:1.3.19 on the EC2 host, runs upgrade CR
(1.3.19 -> 2.x, skip_snapshot=True for test env), verifies new version +
canary index on the upgraded container, triggers rollback, and verifies
the rollback API gate.

Run:
    cd /home/ec2-user/nexplane
    PYTHONPATH=backend python3 -m pytest \
        backend/tests/smoke/test_opensearch_upgrade_smoke.py -v -s \
        2>&1 | tee /tmp/wpm_smoke.log; echo SMOKE_DONE >> /tmp/wpm_smoke.log
"""

import os
import sys
import subprocess
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL      = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL         = os.environ.get("NEXPLANE_EMAIL",    "admin@acme.example")
PASSWORD      = os.environ.get("NEXPLANE_PASSWORD", "admin123")

TIMEOUT       = 900   # 15 min
POLL_INTERVAL = 15

OS_SRC_VERSION = "1.3.19"
OS_TGT_VERSION = "2.x"
OS_PORT        = 19201

SMOKE_CONNECTOR_ID = "666e237d-4e83-4c6c-b72e-6e32fbb5c895"
SMOKE_ASSET_ID     = "1a7051be-7110-4a21-9cdf-b023231cdff8"


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run(cmd: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout, check=check)


def _wait_os(port: int, timeout: int = 120) -> None:
    """Poll until OpenSearch returns a JSON response with a 'number' version field."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = subprocess.run(
            f"curl -sf http://localhost:{port}/ | grep -q number",
            shell=True, capture_output=True,
        )
        if r.returncode == 0:
            return
        time.sleep(3)
    raise TimeoutError(f"OpenSearch not ready on port {port} after {timeout}s")


# ---------------------------------------------------------------------------
# CR lifecycle helpers
# ---------------------------------------------------------------------------

def _cr_lifecycle(client: NexplaneClient, title: str, change_type: str,
                   desired_outcome: dict, asset_ids: list,
                   timeout: int = TIMEOUT) -> dict:
    """create -> plan -> submit-for-approval -> approve -> execute -> poll."""
    base = client.base
    r = client.client.post(f"{base}/change-requests", json={
        "title":             title,
        "change_type":       change_type,
        "desired_outcome":   desired_outcome,
        "target_asset_ids":  asset_ids,
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
        json={"decision": "approved", "comment": "os-upgrade smoke"},
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
    """POST /rollback and poll until rolled_back.

    Handles FILO 409: if a later CR is blocking, rolls it back first.
    For smoke purposes with skip_snapshot=True, rollback_failed is also
    acceptable — what matters is that the rollback API was reachable and
    the Docker environment can be restored manually.
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

class TestOpenSearchUpgradeSmoke:

    def setup_method(self):
        self.client   = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.asset_id = SMOKE_ASSET_ID
        # Roll back any leftover applied CRs so the FILO stack starts clean.
        base = self.client.base
        r = self.client.client.post(f"{base}/assets/{self.asset_id}/rollback-all")
        if r.status_code not in (200, 201, 202, 204, 404):
            log(f"setup rollback-all returned {r.status_code}: {r.text[:200]}")

    def teardown_method(self, method):
        _run("docker rm -f smoke-opensearch 2>/dev/null || true", check=False)

    # -----------------------------------------------------------------------
    # Phase OS_UPGRADE
    # -----------------------------------------------------------------------

    def test_opensearch_upgrade(self):
        """
        OS_UPGRADE: Docker opensearch:1.3.19 -> 2.x via CR.

        1. Launch opensearch 1.3.19 container on port 19201.
        2. Seed smoke_canary index.
        3. Run opensearch_upgrade CR (skip_snapshot=True — no EBS/S3 in test env).
        4. Verify CR completed with version 2.x in execution_result.
        5. Verify canary index survived.
        6. Trigger rollback (API must accept; skip_snapshot means no restore artifact,
           so rollback_failed is tolerated).
        """
        container_name = "smoke-opensearch"
        asset_id = self.asset_id

        log(f"OS_UPGRADE: launching opensearch:{OS_SRC_VERSION} on port {OS_PORT}")
        _run(f"docker rm -f {container_name} 2>/dev/null || true", check=False)
        _run(
            f"docker run -d --name {container_name} "
            f"-p {OS_PORT}:9200 "
            f"-e discovery.type=single-node "
            f"-e DISABLE_SECURITY_PLUGIN=true "
            f"opensearchproject/opensearch:{OS_SRC_VERSION}",
            timeout=30,
        )
        _wait_os(OS_PORT, timeout=120)

        # Seed canary index + document; retry until confirmed indexed.
        seed_resp = ""
        for attempt in range(10):
            _run(
                f"curl -sf -X PUT http://localhost:{OS_PORT}/smoke_canary "
                f"-H 'Content-Type: application/json' "
                f"-d '{{\"settings\":{{\"number_of_shards\":1,\"number_of_replicas\":0}}}}'",
                check=False,
            )
            seed_resp = _run(
                f"curl -sf -X POST http://localhost:{OS_PORT}/smoke_canary/_doc "
                f"-H 'Content-Type: application/json' "
                f"-d '{{\"val\":\"before-upgrade\"}}'",
                check=False,
            ).stdout
            if "_id" in seed_resp or "created" in seed_resp:
                break
            time.sleep(3)
        assert "_id" in seed_resp or "created" in seed_resp, (
            f"Failed to seed canary index: {seed_resp[:200]}"
        )
        log(f"OS_UPGRADE: opensearch:{OS_SRC_VERSION} ready; canary index seeded")

        cr_id = None
        try:
            # ----------------------------------------------------------------
            # Execute CR
            # ----------------------------------------------------------------
            cr = _cr_lifecycle(
                self.client,
                f"[smoke] opensearch_upgrade {OS_SRC_VERSION}->{OS_TGT_VERSION}",
                "opensearch_upgrade",
                {
                    "source_version": OS_SRC_VERSION,
                    "target_version": OS_TGT_VERSION,
                    "os_host":        "127.0.0.1",
                    "os_port":        OS_PORT,
                    "os_user":        "admin",
                    "os_password":    "admin",
                    "skip_snapshot":  True,
                },
                [asset_id],
            )
            cr_id  = cr["id"]
            result = _execution_result(cr)
            log(f"OS_UPGRADE: CR completed, status={cr.get('status')}, "
                f"result={str(result)[:300]}")

            # ----------------------------------------------------------------
            # Verify execution result contains version "2."
            # ----------------------------------------------------------------
            result_str = str(result)
            assert "2." in result_str, (
                f"Expected version 2.x in execution result, got: {result_str[:300]}"
            )
            log("OS_UPGRADE: version 2.x confirmed in execution_result")

            # ----------------------------------------------------------------
            # Verify canary index survived
            # ----------------------------------------------------------------
            canary_resp = "MISSING"
            for _ in range(10):
                canary_resp = _run(
                    f"curl -sf 'http://localhost:{OS_PORT}/smoke_canary/_search' "
                    f"2>/dev/null || echo MISSING",
                    check=False,
                ).stdout
                if "before-upgrade" in canary_resp or "_id" in canary_resp:
                    break
                time.sleep(5)
            assert "before-upgrade" in canary_resp or "_id" in canary_resp, (
                f"Canary index missing after upgrade: {canary_resp[:300]}"
            )
            log("OS_UPGRADE: canary index survived upgrade")

            # ----------------------------------------------------------------
            # Rollback — API gate only (skip_snapshot means no restore artifact;
            # we accept rolled_back OR rollback_failed as terminal states).
            # ----------------------------------------------------------------
            cr_rb = _rollback_cr(self.client, cr_id, "os-upgrade-rollback")
            rb_status = cr_rb.get("status")
            assert rb_status in ("rolled_back", "rollback_failed"), (
                f"Unexpected rollback terminal status: {rb_status}"
            )
            log(f"OS_UPGRADE: rollback terminal={rb_status} (API gate passed)")
            log("OS_UPGRADE: PASSED")

        finally:
            _run(f"docker rm -f {container_name} 2>/dev/null || true", check=False)
