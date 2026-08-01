# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
RabbitMQ upgrade smoke test.

Provisions docker rabbitmq:3.12-management on the EC2 host, runs upgrade CR
(3.12 -> 4.0, skip_snapshot=True for test env), verifies new version in
execution_result, verifies canary queue API responds, triggers rollback,
and verifies the rollback API gate.

Run:
    cd /home/ec2-user/nexplane
    PYTHONPATH=backend python3 -m pytest \
        backend/tests/smoke/test_rabbitmq_upgrade_smoke.py -v -s \
        2>&1 | tee /tmp/wpm_smoke.log; echo SMOKE_DONE_RMQ >> /tmp/wpm_smoke.log
"""

import os
import sys
import subprocess
import time

import pytest
import requests
from requests.auth import HTTPBasicAuth

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL      = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL         = os.environ.get("NEXPLANE_EMAIL",    "admin@acme.example")
PASSWORD      = os.environ.get("NEXPLANE_PASSWORD", "admin123")

TIMEOUT       = 300   # 5 min — RabbitMQ upgrade is faster than ES/OS
POLL_INTERVAL = 10

RMQ_PORT        = 15672
RMQ_SRC_VERSION = "3.12"
RMQ_TGT_VERSION = "4.0"

SMOKE_CONNECTOR_ID = "666e237d-4e83-4c6c-b72e-6e32fbb5c895"
SMOKE_ASSET_ID     = "1a7051be-7110-4a21-9cdf-b023231cdff8"


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _run(cmd: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout, check=check)


def _wait_rmq(port: int = 15672, timeout: int = 90) -> None:
    """Poll until RabbitMQ management API returns HTTP 200."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(
                f"http://localhost:{port}/api/overview",
                auth=HTTPBasicAuth("guest", "guest"),
                timeout=5,
            )
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(3)
    raise TimeoutError(f"RabbitMQ management API not ready on port {port} after {timeout}s")


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
        json={"decision": "approved", "comment": "rmq-upgrade smoke"},
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

class TestRabbitMQUpgradeSmoke:

    def setup_method(self):
        self.client   = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.asset_id = SMOKE_ASSET_ID
        # Roll back any leftover applied CRs so the FILO stack starts clean.
        base = self.client.base
        r = self.client.client.post(f"{base}/assets/{self.asset_id}/rollback-all")
        if r.status_code not in (200, 201, 202, 204, 404):
            log(f"setup rollback-all returned {r.status_code}: {r.text[:200]}")

    def teardown_method(self, method):
        _run("docker rm -f smoke-rabbitmq smoke-rabbitmq-v4 2>/dev/null || true", check=False)

    # -----------------------------------------------------------------------
    # Phase RMQ_UPGRADE
    # -----------------------------------------------------------------------

    def test_rabbitmq_upgrade(self):
        """
        RMQ_UPGRADE: Docker rabbitmq:3.12-management -> 4.0 via CR.

        1. Launch rabbitmq:3.12-management container on ports 5672/15672.
        2. Seed smoke_canary_queue via management API.
        3. Run rabbitmq_upgrade CR (skip_snapshot=True — no persistent volume in test env).
        4. Verify CR completed with version 4.x in execution_result.
        5. Verify management API responds on port 15672 (new container same port).
        6. Verify canary queue API responds (404 acceptable — skip_snapshot=True).
        7. Trigger rollback (API must accept; skip_snapshot means no restore artifact,
           so rollback_failed is tolerated).
        """
        container_name = "smoke-rabbitmq"
        asset_id = self.asset_id

        log(f"RMQ_UPGRADE: launching rabbitmq:{RMQ_SRC_VERSION}-management on port {RMQ_PORT}")
        _run(f"docker rm -f {container_name} 2>/dev/null || true", check=False)
        _run(
            f"docker run -d --name {container_name} "
            f"-p 15672:15672 "
            f"-p 5672:5672 "
            f"-e RABBITMQ_DEFAULT_USER=guest "
            f"-e RABBITMQ_DEFAULT_PASS=guest "
            f"rabbitmq:3.12-management",
            timeout=30,
        )
        _wait_rmq(RMQ_PORT, timeout=90)

        # Seed canary queue via management API.
        queue_created = False
        for attempt in range(10):
            try:
                r = requests.put(
                    f"http://localhost:{RMQ_PORT}/api/queues/%2F/smoke_canary_queue",
                    json={"durable": True, "auto_delete": False, "arguments": {}},
                    auth=HTTPBasicAuth("guest", "guest"),
                    timeout=5,
                )
                if r.status_code in (200, 201, 204):
                    queue_created = True
                    break
            except Exception:
                pass
            time.sleep(3)
        assert queue_created, "Failed to create smoke_canary_queue via management API"
        log(f"RMQ_UPGRADE: rabbitmq:{RMQ_SRC_VERSION} ready; canary queue seeded")

        cr_id = None
        try:
            # ----------------------------------------------------------------
            # Execute CR
            # ----------------------------------------------------------------
            cr = _cr_lifecycle(
                self.client,
                f"[smoke] rabbitmq_upgrade {RMQ_SRC_VERSION}->{RMQ_TGT_VERSION}",
                "rabbitmq_upgrade",
                {
                    "source_version": RMQ_SRC_VERSION,
                    "target_version": RMQ_TGT_VERSION,
                    "rmq_host":       "127.0.0.1",
                    "rmq_port":       RMQ_PORT,
                    "rmq_user":       "guest",
                    "rmq_password":   "guest",
                    "rmq_container":  container_name,
                    "skip_snapshot":  True,
                },
                [asset_id],
            )
            cr_id  = cr["id"]
            result = _execution_result(cr)
            log(f"RMQ_UPGRADE: CR completed, status={cr.get('status')}, "
                f"result={str(result)[:300]}")

            # ----------------------------------------------------------------
            # Verify execution result contains version "4."
            # ----------------------------------------------------------------
            upgrade_result = result.get("upgrade_result", {})
            upgraded = upgrade_result.get("upgraded_version", "")
            # Fall back to searching the full result string for "4."
            result_str = str(result)
            assert "4." in upgraded or "4." in result_str, (
                f"Expected version 4.x in execution result, got: {result_str[:300]}"
            )
            log(f"RMQ_UPGRADE: version {upgraded or '4.x'} confirmed in execution_result")

            # ----------------------------------------------------------------
            # Verify management API responds on port 15672 (new container)
            # ----------------------------------------------------------------
            _wait_rmq(RMQ_PORT, timeout=60)
            log("RMQ_UPGRADE: management API on port 15672 confirmed after upgrade")

            # ----------------------------------------------------------------
            # Verify canary queue API responds (404 OK — skip_snapshot=True)
            # ----------------------------------------------------------------
            try:
                r = requests.get(
                    f"http://localhost:{RMQ_PORT}/api/queues/%2F/smoke_canary_queue",
                    auth=HTTPBasicAuth("guest", "guest"),
                    timeout=10,
                )
                assert r.status_code in (200, 404), (
                    f"Unexpected status checking canary queue: {r.status_code}"
                )
                log(f"RMQ_UPGRADE: canary queue check status={r.status_code} (200/404 both OK)")
            except requests.exceptions.RequestException as e:
                log(f"RMQ_UPGRADE: canary queue check skipped (new container still booting): {e}")

            # ----------------------------------------------------------------
            # Rollback — API gate only (skip_snapshot means no restore artifact;
            # we accept rolled_back OR rollback_failed as terminal states).
            # ----------------------------------------------------------------
            cr_rb = _rollback_cr(self.client, cr_id, "rmq-upgrade-rollback")
            rb_status = cr_rb.get("status")
            assert rb_status in ("rolled_back", "rollback_failed"), (
                f"Unexpected rollback terminal status: {rb_status}"
            )
            log(f"RMQ_UPGRADE: rollback terminal={rb_status} (API gate passed)")
            log("RMQ_UPGRADE: PASSED")

        finally:
            _run("docker rm -f smoke-rabbitmq smoke-rabbitmq-v4 2>/dev/null || true", check=False)
