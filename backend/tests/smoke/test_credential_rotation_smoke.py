# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Credential Rotation Smoke Test

Phases:
  1. HAPPY PATH — two-step fan-out (IAM key + Secrets Manager), FILO rollback
  2. PAUSE/SKIP — step 1 fails (invalid secret), operator skips, step 0 rolls back only

Run from EC2:
  docker exec nexplane-backend-1 python -m pytest \\
    /app/tests/smoke/test_credential_rotation_smoke.py -v -s
"""

import os
import time
import uuid

import boto3
import pytest

from smoke_helpers import (
    NexplaneClient,
    log,
    get_connector_creds_from_db,
)

BASE_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

ROTATION_TIMEOUT = 120   # 2 min per rotation step
ROLLBACK_TIMEOUT = 120


def _get_client():
    return NexplaneClient(BASE_URL, EMAIL, PASSWORD)


def _get_aws_creds():
    creds = get_connector_creds_from_db("aws")
    if not creds:
        pytest.skip("No AWS credentials found in platform database")
    return creds


def _boto_clients(creds):
    kwargs = {
        "aws_access_key_id": creds["access_key_id"],
        "aws_secret_access_key": creds["secret_access_key"],
        "region_name": creds.get("region", "us-east-1"),
    }
    return boto3.client("iam", **kwargs), boto3.client("secretsmanager", **kwargs)


def _run_rotation_cr(client, title, steps, timeout):
    """Create -> plan -> approve -> execute -> poll until completed/paused/failed."""
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": title,
        "change_type": "credential_rotation",
        "desired_outcome": {"steps": steps},
    })
    assert resp.status_code in (200, 201), f"CR create failed {resp.status_code}: {resp.text}"
    cr_id = resp.json()["id"]

    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        assert r.status_code in (200, 201, 202, 204), f"/{path} failed {r.status_code}: {r.text}"

    r = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"},
    )
    assert r.status_code in (200, 201, 202, 204), f"/approve failed {r.status_code}: {r.text}"

    r = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r.status_code in (200, 201, 202, 204), f"/execute failed {r.status_code}: {r.text}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("completed", "paused"):
            return cr
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(f"CR {cr_id} status={status!r}: {cr}")
        time.sleep(5)
    raise TimeoutError(f"CR {cr_id} timeout after {timeout}s")


def _rollback_cr(client, cr_id, timeout):
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    assert r.status_code in (200, 201, 202, 204), f"/rollback failed {r.status_code}: {r.text}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("rolled_back", "rollback_partial", "rollback_failed"):
            return cr
        time.sleep(5)
    raise TimeoutError(f"Rollback timeout for CR {cr_id}")


def _get_steps(cr):
    """Extract steps list from CR execution result."""
    for run in cr.get("execution_runs", []):
        result = run.get("result") or {}
        steps = result.get("steps") or result.get("execution", {}).get("steps")
        if steps:
            return steps
    return []


class TestCredentialRotation:
    """Live credential rotation smoke tests."""

    @classmethod
    def setup_class(cls):
        cls.client = _get_client()
        cls.creds = _get_aws_creds()
        cls.suffix = uuid.uuid4().hex[:8]
        cls.iam_user = f"nexplane-smoke-rotation-{cls.suffix}"
        cls.secret_id = f"nexplane-smoke-rotation-{cls.suffix}"

        cls.iam, cls.sm = _boto_clients(cls.creds)

        log(f"[CRED-ROTATION] Creating smoke IAM user: {cls.iam_user}")
        cls.iam.create_user(UserName=cls.iam_user)
        # Do NOT pre-create an access key: rotate_iam_key creates one during execution.
        # Pre-creating would consume a slot of the 2-key IAM quota before Phase 1 runs.

        log(f"[CRED-ROTATION] Creating smoke secret: {cls.secret_id}")
        cls.sm.create_secret(Name=cls.secret_id, SecretString="initial-smoke-value")

    @classmethod
    def teardown_class(cls):
        log("[CRED-ROTATION] Teardown: deleting smoke IAM user and secret")
        try:
            keys = cls.iam.list_access_keys(UserName=cls.iam_user).get("AccessKeyMetadata", [])
            for key in keys:
                cls.iam.delete_access_key(UserName=cls.iam_user, AccessKeyId=key["AccessKeyId"])
            cls.iam.delete_user(UserName=cls.iam_user)
        except Exception as e:
            log(f"[CRED-ROTATION] IAM teardown warning: {e}")
        try:
            cls.sm.delete_secret(SecretId=cls.secret_id, ForceDeleteWithoutRecovery=True)
        except Exception as e:
            log(f"[CRED-ROTATION] Secret teardown warning: {e}")

    def test_phase1_happy_path_fan_out(self):
        """Two-step rotation: IAM key + Secrets Manager. Full lifecycle with FILO rollback."""
        log("[PHASE1] Starting happy path fan-out smoke")

        steps = [
            {
                "connector_type": "aws",
                "action_id": "rotate_iam_key",
                "params": {"username": self.iam_user},
                "label": "Rotate IAM key",
            },
            {
                "connector_type": "aws",
                "action_id": "rotate_secrets_manager_secret",
                "params": {"secret_id": self.secret_id, "new_value": "rotated-smoke-value"},
                "label": "Rotate Secrets Manager secret",
            },
        ]

        cr = _run_rotation_cr(
            self.client, "[SMOKE] Credential rotation happy path", steps, ROTATION_TIMEOUT
        )
        assert cr["status"] == "completed", f"Expected completed, got {cr['status']}"
        log("[PHASE1] CR completed")

        step_list = _get_steps(cr)
        assert len(step_list) == 2, f"Expected 2 steps, got {step_list}"
        assert step_list[0]["status"] == "completed", f"Step 0: {step_list[0]}"
        assert step_list[1]["status"] == "completed", f"Step 1: {step_list[1]}"
        log("[PHASE1] Both steps completed — triggering FILO rollback")

        rb_cr = _rollback_cr(self.client, cr["id"], ROLLBACK_TIMEOUT)
        assert rb_cr["status"] in ("rolled_back", "rollback_partial"), \
            f"Unexpected rollback status: {rb_cr['status']}"
        log(f"[PHASE1] Rollback status: {rb_cr['status']}")

        # Verify FILO: last completed step should appear first in rollback_steps
        for run in rb_cr.get("execution_runs", []):
            result = run.get("result") or {}
            rb_steps = result.get("rollback_steps", [])
            if rb_steps:
                assert rb_steps[0]["index"] == 1, \
                    f"Expected step index 1 first in rollback (FILO), got {rb_steps[0]['index']}"
                log(f"[PHASE1] FILO order confirmed: rollback_steps={[s['index'] for s in rb_steps]}")
                break

        log("[PHASE1] PASS")

    def test_phase2_pause_skip_path(self):
        """Step 0 succeeds, step 1 fails (bad secret id), operator skips, rollback only unwinds step 0."""
        log("[PHASE2] Starting pause/skip smoke")

        steps = [
            {
                "connector_type": "aws",
                "action_id": "rotate_iam_key",
                "params": {"username": self.iam_user},
                "label": "Rotate IAM key",
            },
            {
                "connector_type": "aws",
                "action_id": "rotate_secrets_manager_secret",
                "params": {"secret_id": "nexplane-smoke-nonexistent-secret-xyz", "new_value": "x"},
                "label": "Rotate nonexistent secret (forced failure)",
            },
        ]

        cr = _run_rotation_cr(
            self.client, "[SMOKE] Credential rotation pause/skip", steps, ROTATION_TIMEOUT
        )
        assert cr["status"] == "paused", f"Expected paused, got {cr['status']}"
        log("[PHASE2] CR paused as expected")

        step_list = _get_steps(cr)
        assert step_list[0]["status"] == "completed", f"Step 0 should be completed: {step_list[0]}"
        assert step_list[1]["status"] == "failed", f"Step 1 should be failed: {step_list[1]}"
        log("[PHASE2] Step 0 completed, step 1 failed — skipping step 1")

        base = self.client.base
        r = self.client.client.post(f"{base}/change-requests/{cr['id']}/skip-step")
        assert r.status_code in (200, 201, 202), f"skip-step failed {r.status_code}: {r.text}"

        # Wait for CR to complete (skip-step triggers async resume)
        deadline = time.time() + ROTATION_TIMEOUT
        while time.time() < deadline:
            cr_updated = self.client.client.get(f"{base}/change-requests/{cr['id']}").json()
            if cr_updated["status"] == "completed":
                break
            if cr_updated["status"] in ("failed", "paused"):
                # If already last step (no more pending after skip), skip-step sets completed directly
                break
            time.sleep(3)

        cr_updated = self.client.client.get(f"{base}/change-requests/{cr['id']}").json()
        assert cr_updated["status"] == "completed", \
            f"Expected completed after skip, got {cr_updated['status']}"
        log("[PHASE2] CR completed after skip")

        step_list2 = _get_steps(cr_updated)
        assert step_list2[0]["status"] == "completed"
        assert step_list2[1]["status"] == "skipped"
        log("[PHASE2] Step statuses confirmed: [completed, skipped]")

        log("[PHASE2] Triggering rollback — only step 0 should unwind")
        rb_cr = _rollback_cr(self.client, cr_updated["id"], ROLLBACK_TIMEOUT)
        # rollback_failed is acceptable here: step 0 is rotate_iam_key which explicitly returns
        # rolled_back=False (manual deactivation required). Skipped step 1 must not appear.
        assert rb_cr["status"] in ("rolled_back", "rollback_partial", "rollback_failed"), \
            f"Unexpected rollback status: {rb_cr['status']}"

        for run in rb_cr.get("execution_runs", []):
            result = run.get("result") or {}
            rb_steps = result.get("rollback_steps", [])
            if rb_steps:
                indices = [s["index"] for s in rb_steps]
                assert 1 not in indices, f"Skipped step 1 should not appear in rollback: {indices}"
                assert 0 in indices, f"Step 0 should appear in rollback: {indices}"
                log(f"[PHASE2] Rollback correctly excludes skipped step: {indices}")
                break

        log("[PHASE2] PASS")
