# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import os
import time
import pytest
import sys

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

PHASE = "OCI_ROLLBACK"
TIMEOUT = 300  # OCI instance state changes can be slow


def _run_cr(client, label, action_id, params, timeout=TIMEOUT):
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {"connector_type": "oci", "action_id": action_id, "params": params},
    })
    if resp.status_code not in (200, 201):
        raise AssertionError(f"[{label}] CR create failed {resp.status_code}: {resp.text}")
    cr_id = resp.json()["id"]

    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        if r.status_code not in (200, 201, 202, 204):
            raise AssertionError(f"[{label}] /{path} failed {r.status_code}: {r.text}")

    r = client.client.post(f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"})
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /approve failed {r.status_code}: {r.text}")

    r = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /execute failed {r.status_code}: {r.text}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "completed":
            log(label)
            return cr
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(
                f"[{label}] CR {cr_id} status={status!r}: {str(cr.get('execution_runs', ''))[:400]}"
            )
        time.sleep(10)
    raise TimeoutError(f"[{label}] CR {cr_id} timeout after {timeout}s")


def _rollback_cr(client, cr_id, label, timeout=TIMEOUT):
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /rollback failed {r.status_code}: {r.text}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "rolled_back":
            log(f"rolled back: {label}")
            return cr
        if status in ("rollback_failed", "failed"):
            raise AssertionError(f"[{label}] rollback status={status!r}")
        time.sleep(10)
    raise TimeoutError(f"[{label}] rollback timeout after {timeout}s")


def _step_result(cr, rollback=False):
    for run in cr.get("execution_runs", []):
        is_rb = "rollback" in run.get("workflow_id", "")
        if is_rb != rollback:
            continue
        result = run.get("result") or {}
        # Direct _executor_fallback result (Task 16 pattern)
        if "rolled_back" in result:
            return result
        if rollback:
            steps = result.get("rollback_steps", [])
            if steps:
                return steps[0].get("result") or {}
        else:
            steps = result.get("execution", {}).get("steps", [])
            if steps:
                return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


def _get_oci_compute(creds):
    import oci
    # Creds use: user, private_key, fingerprint, tenancy, region
    config = {
        "tenancy": creds["tenancy"],
        "user": creds["user"],
        "fingerprint": creds["fingerprint"],
        "key_content": creds["private_key"],
        "region": creds["region"],
    }
    return oci.core.ComputeClient(config)


def _wait_for_instance_state(compute, instance_id, target_state, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        instance = compute.get_instance(instance_id).data
        state = instance.lifecycle_state
        if state == target_state:
            return state
        if state in ("TERMINATING", "TERMINATED"):
            raise AssertionError(f"Instance entered terminal state {state!r}")
        time.sleep(15)
    raise TimeoutError(f"Instance {instance_id} did not reach {target_state!r} within {timeout}s")


class TestOciRollbackSmoke:
    """
    End-to-end rollback smoke for the OCI stop_instance executor.
    Stops a smoke instance via CR, verifies STOPPED, rolls back (starts), verifies RUNNING.
    """

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.creds = get_connector_creds_from_db("oci")
        if self.creds is None:
            pytest.skip("No OCI credentials in platform DB")
        self.instance_id = self.creds.get("smoke_instance_id")
        if not self.instance_id:
            pytest.skip("No smoke_instance_id in OCI creds — cannot run stop_instance rollback smoke")
        self.compute = _get_oci_compute(self.creds)

    def test_stop_instance_rollback(self):
        # Verify instance is currently running before we touch it
        instance = self.compute.get_instance(self.instance_id).data
        assert instance.lifecycle_state == "RUNNING", (
            f"Smoke instance must be RUNNING before test, got: {instance.lifecycle_state!r}"
        )
        log(f"{PHASE}: instance {self.instance_id} confirmed RUNNING before test")

        # Execute stop_instance CR
        cr = _run_cr(
            self.client,
            "[smoke] stop_instance for rollback test",
            "stop_instance",
            {"instance_id": self.instance_id},
        )
        cr_id = cr["id"]

        stop_result = _step_result(cr, rollback=False)
        lifecycle = stop_result.get("lifecycle_state") or stop_result.get("state") or ""
        assert lifecycle in ("STOPPED", "STOPPING"), (
            f"Expected STOPPED/STOPPING lifecycle after stop, got: {stop_result}"
        )
        log(f"{PHASE}: stop_instance executed (cr={cr_id})")

        # Wait for instance to fully stop
        final_state = _wait_for_instance_state(self.compute, self.instance_id, "STOPPED", timeout=300)
        assert final_state == "STOPPED", f"Instance not STOPPED after CR, got: {final_state}"

        # Rollback (should start the instance)
        cr = _rollback_cr(self.client, cr_id, "rollback stop_instance")

        rollback_result = _step_result(cr, rollback=True)
        assert rollback_result.get("rolled_back") is True, (
            f"Expected rolled_back=True, got: {rollback_result}"
        )
        log(f"{PHASE}: rollback completed (cr={cr_id})")

        # Wait for instance to return to RUNNING
        final_state = _wait_for_instance_state(self.compute, self.instance_id, "RUNNING", timeout=300)
        assert final_state == "RUNNING", f"Instance not RUNNING after rollback, got: {final_state}"
        log(f"{PHASE}: instance {self.instance_id} confirmed RUNNING after rollback")
