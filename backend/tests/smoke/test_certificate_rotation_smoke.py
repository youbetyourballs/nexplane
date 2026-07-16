# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Certificate Rotation Smoke Test

Phases:
  1. HAPPY PATH — type-A host (nginx port 8443), FILO rollback
  2. TYPE-B CONSUMER — Kubernetes secret rotation + rollback
  3. COMPROMISE TRIGGER — rollback_strategy must be 'reissue'
  4. VERIFY FAILURE → PAUSED → ROLLBACK — synthetic unreachable host
  5. AUTO-TRIGGER — expiry worker creates draft CR for near-expiry cert

Run from EC2:
  docker exec nexplane-backend-1 python -m pytest \\
    /app/tests/smoke/test_certificate_rotation_smoke.py -v -s
"""

import os
import ssl
import socket
import time
import uuid

import pytest

from smoke_helpers import (
    NexplaneClient,
    log,
    get_connector_creds_from_db,
)

BASE_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")
SMOKE_SUBJECT = "nexplane-smoke-cert.internal"
EXPIRING_SUBJECT = "nexplane-smoke-expiring.internal"
EXEC_TIMEOUT = 180
ROLLBACK_TIMEOUT = 120


def _client():
    return NexplaneClient(BASE_URL, EMAIL, PASSWORD)


def _step_ca_creds():
    creds = get_connector_creds_from_db("step_ca")
    if not creds:
        pytest.skip("No step_ca connector found")
    return creds


def _k8s_creds():
    return get_connector_creds_from_db("kubernetes")


def _run_cert_rotation_cr(client, title, subject, san=None, scan_scope=None, trigger_reason="scheduled",
                           verify_timeout_seconds=5, timeout=EXEC_TIMEOUT):
    base = client.base
    payload = {
        "title": title,
        "change_type": "certificate_rotation",
        "desired_outcome": {
            "subject": subject,
            "san": san or [subject],
            "not_after": "720h",
            "trigger_reason": trigger_reason,
            "scan_scope": scan_scope or ["nexplane_agent"],
            "verify_timeout_seconds": verify_timeout_seconds,
        },
    }
    r = client.client.post(f"{base}/change-requests", json=payload)
    assert r.status_code in (200, 201), f"CR create failed {r.status_code}: {r.text}"
    cr_id = r.json()["id"]

    for path in ["plan", "submit-for-approval"]:
        r2 = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        assert r2.status_code in (200, 201, 202, 204), f"/{path} failed {r2.status_code}: {r2.text}"

    r3 = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"},
    )
    assert r3.status_code in (200, 201, 202, 204), f"/approve failed {r3.status_code}: {r3.text}"

    r4 = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r4.status_code in (200, 201, 202, 204), f"/execute failed {r4.status_code}: {r4.text}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("completed", "paused"):
            return cr
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(f"CR {cr_id} unexpected status={status!r}: {cr}")
        time.sleep(5)
    raise TimeoutError(f"CR {cr_id} timed out after {timeout}s")


def _rollback_cr(client, cr_id, timeout=ROLLBACK_TIMEOUT):
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    assert r.status_code in (200, 201, 202, 204), f"/rollback failed {r.status_code}: {r.text}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("rolled_back", "rolled_back_with_warnings", "rollback_partial", "rollback_failed"):
            return cr
        time.sleep(5)
    raise TimeoutError(f"Rollback timed out for CR {cr_id}")


def _get_execution_result(cr):
    """Return the forward execution result (has dependents/rotation_result)."""
    for run in cr.get("execution_runs", []):
        if "rollback" in (run.get("workflow_id") or ""):
            continue
        result = run.get("result") or {}
        if "dependents" in result:
            return result
        inner = result.get("execution") or {}
        if "dependents" in inner:
            return inner
    return {}


def _get_rollback_result(cr):
    """Return the rollback execution result (has rollback_steps)."""
    for run in cr.get("execution_runs", []):
        if "rollback" not in (run.get("workflow_id") or ""):
            continue
        result = run.get("result") or {}
        if "rollback_steps" in result:
            return result
    # Fallback: check all runs for rollback_steps
    for run in cr.get("execution_runs", []):
        result = run.get("result") or {}
        if "rollback_steps" in result:
            return result
    return {}


def _tls_fingerprint(host, port=8443, timeout=5):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                import hashlib
                der = ssock.getpeercert(binary_form=True)
                return hashlib.sha256(der).hexdigest()
    except Exception:
        return None


def _register_asset(client, hostname, port, asset_type="server", connector_type="nexplane_agent", extra=None):
    """Register a server asset in the platform DB so scan can find it."""
    base = client.base
    payload = {
        "name": hostname,
        "asset_type": asset_type,
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {"port": port, "hostname": hostname},
        **(extra or {}),
    }
    r = client.client.post(f"{base}/assets", json=payload)
    assert r.status_code in (200, 201), f"Asset register failed {r.status_code}: {r.text}"
    return r.json()["id"]


def _delete_asset(client, asset_id):
    base = client.base
    client.client.delete(f"{base}/assets/{asset_id}")


class TestCertificateRotation:
    """Live certificate rotation smoke tests — 5 phases."""

    @classmethod
    def setup_class(cls):
        cls.client = _client()
        cls.step_ca_creds = _step_ca_creds()
        cls.k8s_creds = _k8s_creds()
        cls.smoke_asset_ids = []

        log("[CERT-ROTATION] setup: issuing initial cert for smoke nginx")
        # Issue initial cert via step_ca executor directly for nginx setup
        base = cls.client.base
        r = cls.client.client.post(f"{base}/change-requests", json={
            "title": "[SMOKE-SETUP] Initial cert for smoke nginx",
            "change_type": "certificate_rotation",
            "desired_outcome": {
                "subject": SMOKE_SUBJECT,
                "san": [SMOKE_SUBJECT],
                "not_after": "720h",
                "trigger_reason": "scheduled",
                "scan_scope": ["nexplane_agent"],  # zero-dependent run — asset not registered yet, but non-empty scope passes validation
                "verify_timeout_seconds": 1,
            },
        })
        if r.status_code in (200, 201):
            cr_id = r.json()["id"]
            for path in ["plan", "submit-for-approval"]:
                cls.client.client.post(f"{base}/change-requests/{cr_id}/{path}")
            cls.client.client.post(f"{base}/change-requests/{cr_id}/approve",
                                   json={"decision": "approved", "comment": "smoke-setup"})
            cls.client.client.post(f"{base}/change-requests/{cr_id}/execute")
            deadline = time.time() + 60
            while time.time() < deadline:
                cr = cls.client.client.get(f"{base}/change-requests/{cr_id}").json()
                if cr.get("status") in ("completed", "failed", "paused"):
                    break
                time.sleep(3)

        # Register smoke host asset — nginx runs on port 443 on nexplane_default network
        asset_id = _register_asset(cls.client, SMOKE_SUBJECT, 443)
        cls.smoke_asset_ids.append(asset_id)
        log(f"[CERT-ROTATION] Registered smoke host asset: {asset_id}")

    @classmethod
    def teardown_class(cls):
        log("[CERT-ROTATION] teardown: removing smoke assets")
        for asset_id in cls.smoke_asset_ids:
            try:
                _delete_asset(cls.client, asset_id)
            except Exception as e:
                log(f"[CERT-ROTATION] teardown warning: {e}")

    def test_phase1_happy_path_type_a(self):
        """Happy path: type-A host rotation and FILO rollback."""
        log("[PHASE1] Starting type-A host rotation")

        cr = _run_cert_rotation_cr(
            self.client,
            "[SMOKE] Cert rotation phase 1 — happy path",
            SMOKE_SUBJECT,
            scan_scope=["nexplane_agent"],
            verify_timeout_seconds=5,
        )
        assert cr["status"] == "completed", f"Expected completed, got {cr['status']}: {cr}"
        log("[PHASE1] CR completed")

        result = _get_execution_result(cr)
        dependents = result.get("dependents", [])
        rotation_result = result.get("rotation_result", {})
        assert rotation_result.get("fingerprint"), "rotation_result.fingerprint must be set"

        verified_dependents = [d for d in dependents if (d.get("verify_result") or {}).get("success")]
        assert verified_dependents, f"At least one dependent must verify successfully: {dependents}"
        log(f"[PHASE1] {len(verified_dependents)}/{len(dependents)} dependents verified")

        log("[PHASE1] Triggering rollback")
        rb_cr = _rollback_cr(self.client, cr["id"])
        assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings"), \
            f"Unexpected rollback status: {rb_cr['status']}"

        rb_result = _get_rollback_result(rb_cr)
        rollback_steps = rb_result.get("rollback_steps", [])
        assert rollback_steps, "rollback_steps must be non-empty"

        # FILO: first rollback step must have the highest index
        max_index = max(d["index"] for d in dependents) if dependents else 0
        assert rollback_steps[0]["index"] == max_index, \
            f"Expected FILO (index {max_index} first), got {rollback_steps[0]['index']}"

        rb_verified = False
        for step in rollback_steps:
            if step.get("rolled_back"):
                rb_verified = True
                break
        assert rb_verified, f"No rollback step succeeded: {rollback_steps}"
        log("[PHASE1] PASS")

    def test_phase2_type_b_k8s_secret(self):
        """Type-B K8s secret rotation and rollback."""
        if not self.k8s_creds:
            pytest.skip("No kubernetes connector — skipping phase 2")

        log("[PHASE2] Starting K8s secret rotation")
        # Pre-create K8s secret with placeholder PEM
        # Use the platform's kubernetes connector to create the secret
        base = self.client.base
        # Create a simple k8s secret via executor action
        r = self.client.client.post(f"{base}/execute-action", json={
            "connector_type": "kubernetes",
            "action_id": "create_secret",
            "params": {
                "namespace": "default",
                "name": "nexplane-smoke-tls",
                "data": {"tls.crt": "PLACEHOLDER", "tls.key": "PLACEHOLDER"},
            },
        })
        log(f"[PHASE2] Pre-create k8s secret: {r.status_code}")

        cr = _run_cert_rotation_cr(
            self.client,
            "[SMOKE] Cert rotation phase 2 — K8s secret",
            SMOKE_SUBJECT,
            scan_scope=["kubernetes"],
            verify_timeout_seconds=3,
        )
        assert cr["status"] == "completed", f"Expected completed: {cr['status']}: {cr}"
        result = _get_execution_result(cr)
        rotation_result = result.get("rotation_result", {})
        new_cert_pem = rotation_result.get("cert_pem", "")

        k8s_dependents = [d for d in result.get("dependents", []) if d["type"] == "k8s_secret"]
        assert k8s_dependents, "Expected at least one K8s secret dependent"
        verified = all((d.get("verify_result") or {}).get("success") for d in k8s_dependents)
        assert verified, f"K8s secret verify failed: {k8s_dependents}"
        log("[PHASE2] K8s secret verified — triggering rollback")

        rb_cr = _rollback_cr(self.client, cr["id"])
        assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings")
        log("[PHASE2] PASS")

    def test_phase3_compromise_trigger(self):
        """Compromise trigger: rollback_strategy must be 'reissue'."""
        log("[PHASE3] Starting compromise trigger rotation")

        cr = _run_cert_rotation_cr(
            self.client,
            "[SMOKE] Cert rotation phase 3 — compromise",
            SMOKE_SUBJECT,
            scan_scope=["nexplane_agent"],
            trigger_reason="compromise",
            verify_timeout_seconds=5,
        )
        assert cr["status"] == "completed", f"Expected completed: {cr['status']}: {cr}"
        log("[PHASE3] CR completed — triggering rollback")

        rb_cr = _rollback_cr(self.client, cr["id"])
        assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings")

        rb_result = _get_rollback_result(rb_cr)
        rollback_strategy = rb_result.get("rollback_strategy")
        assert rollback_strategy == "reissue", \
            f"Expected rollback_strategy='reissue' for compromise, got '{rollback_strategy}'"
        log("[PHASE3] rollback_strategy=reissue confirmed")
        log("[PHASE3] PASS")

    def test_phase4_verify_failure_paused_rollback(self):
        """Verify failure: CR pauses; rollback restores type-A host."""
        log("[PHASE4] Registering synthetic unreachable host")
        # Register a fake asset with name=SMOKE_SUBJECT but port=9999 (no TLS listener).
        # The scan matches on asset.name == subject, so this gets picked up.
        # The verify step will try to TLS-probe nexplane-smoke-cert.internal:9999 and fail.
        fake_asset_id = _register_asset(
            self.client, SMOKE_SUBJECT, 9999,
        )
        self.smoke_asset_ids.append(fake_asset_id)

        try:
            log("[PHASE4] Running rotation with mixed real + synthetic dependents")
            cr = _run_cert_rotation_cr(
                self.client,
                "[SMOKE] Cert rotation phase 4 — verify failure",
                SMOKE_SUBJECT,
                scan_scope=["nexplane_agent"],
                verify_timeout_seconds=2,
            )
            assert cr["status"] == "paused", f"Expected paused (synthetic host should fail verify), got {cr['status']}: {cr}"
            log("[PHASE4] CR paused as expected")

            result = _get_execution_result(cr)
            assert result.get("phase") == "verify", f"Expected phase=verify, got {result.get('phase')}"

            dependents = result.get("dependents", [])
            failed = [d for d in dependents if not (d.get("verify_result") or {}).get("success")]
            succeeded = [d for d in dependents if (d.get("verify_result") or {}).get("success")]
            assert failed, f"Expected at least one failed dependent: {dependents}"
            log(f"[PHASE4] {len(failed)} failed, {len(succeeded)} succeeded")

            log("[PHASE4] Triggering rollback")
            rb_cr = _rollback_cr(self.client, cr["id"])
            assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings")

            rb_result = _get_rollback_result(rb_cr)
            rb_steps = rb_result.get("rollback_steps", [])
            assert rb_steps, "rollback_steps must be non-empty"
            log("[PHASE4] PASS")
        finally:
            _delete_asset(self.client, fake_asset_id)
            self.smoke_asset_ids.remove(fake_asset_id)

    @pytest.mark.xfail(
        reason=(
            "step-ca CLI v0.27 has no 'step ca certificate list' command. "
            "The credential_expiry_worker._check_step_ca_certs() uses 'step ca admin list' "
            "which lists admins, not issued certs. The expiry worker cannot enumerate "
            "issued certificates without a database backend or ACME introspection API. "
            "This phase requires a step-ca REST endpoint or separate cert DB integration."
        ),
        strict=False,
    )
    def test_phase5_auto_trigger_expiry_worker(self):
        """Auto-trigger: expiry worker creates draft CR for near-expiry cert."""
        log("[PHASE5] Issuing near-expiry cert (not_after=2h)")

        # Issue a short-lived cert via platform CR
        base = self.client.base
        r = self.client.client.post(f"{base}/change-requests", json={
            "title": "[SMOKE-SETUP] Near-expiry cert for auto-trigger",
            "change_type": "certificate_rotation",
            "desired_outcome": {
                "subject": EXPIRING_SUBJECT,
                "san": [EXPIRING_SUBJECT],
                "not_after": "2h",
                "trigger_reason": "scheduled",
                "scan_scope": ["nexplane_agent"],
                "verify_timeout_seconds": 1,
            },
        })
        assert r.status_code in (200, 201), f"Setup CR failed: {r.text}"
        setup_cr_id = r.json()["id"]
        for path in ["plan", "submit-for-approval"]:
            self.client.client.post(f"{base}/change-requests/{setup_cr_id}/{path}")
        self.client.client.post(f"{base}/change-requests/{setup_cr_id}/approve",
                                json={"decision": "approved", "comment": "smoke-setup"})
        self.client.client.post(f"{base}/change-requests/{setup_cr_id}/execute")
        deadline = time.time() + 60
        while time.time() < deadline:
            cr = self.client.client.get(f"{base}/change-requests/{setup_cr_id}").json()
            if cr.get("status") in ("completed", "failed", "paused"):
                break
            time.sleep(3)
        log(f"[PHASE5] Near-expiry cert issued: {cr.get('status')}")

        log("[PHASE5] Calling _check_step_ca_certs with 1-day threshold")
        # Invoke the worker function directly — it creates a draft CR if cert expires within threshold
        trigger_result = self.client.client.post(
            f"{base}/internal/trigger-expiry-check",
            json={"subject": EXPIRING_SUBJECT, "threshold_days": 1},
        )
        if trigger_result.status_code == 404:
            # Internal endpoint may not exist — call worker directly in-process.
            # subprocess.run(["docker", ...]) would fail because docker is not
            # available inside the backend container where pytest runs.
            log("[PHASE5] No internal trigger endpoint — calling worker directly in-process")
            import asyncio as _asyncio
            import concurrent.futures as _cf
            from app.workers.credential_expiry_worker import _check_step_ca_certs
            from app.database import AsyncSessionLocal as _AsyncSessionLocal

            async def _run_worker():
                async with _AsyncSessionLocal() as _db:
                    await _check_step_ca_certs(_db)

            # Run in a fresh thread to avoid "event loop already running" errors
            # when pytest-anyio/asyncio is active.
            with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
                _future = _pool.submit(_asyncio.run, _run_worker())
                _future.result(timeout=60)
            log("[PHASE5] Worker ran in-process")

        log("[PHASE5] Checking for auto-created draft CR")
        deadline = time.time() + 30
        auto_cr = None
        while time.time() < deadline:
            r = self.client.client.get(f"{base}/change-requests", params={
                "change_type": "certificate_rotation",
                "status": "awaiting_approval",
            })
            crs = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
            matches = [
                c for c in crs
                if (c.get("desired_outcome") or {}).get("subject") == EXPIRING_SUBJECT
                and c.get("title", "").startswith("[Auto]")
            ]
            if matches:
                auto_cr = matches[0]
                break
            time.sleep(3)

        assert auto_cr, f"No auto-triggered certificate_rotation CR found for {EXPIRING_SUBJECT}"
        log(f"[PHASE5] Auto-triggered CR found: {auto_cr['id']}")

        # Execute it to completion
        auto_cr_id = auto_cr["id"]
        r = self.client.client.post(f"{base}/change-requests/{auto_cr_id}/approve",
                                    json={"decision": "approved", "comment": "smoke"})
        assert r.status_code in (200, 201, 202, 204)
        r = self.client.client.post(f"{base}/change-requests/{auto_cr_id}/execute")
        assert r.status_code in (200, 201, 202, 204)

        deadline = time.time() + EXEC_TIMEOUT
        while time.time() < deadline:
            final_cr = self.client.client.get(f"{base}/change-requests/{auto_cr_id}").json()
            if final_cr.get("status") in ("completed", "paused", "failed"):
                break
            time.sleep(5)

        assert final_cr.get("status") == "completed", \
            f"Auto-trigger CR did not complete: {final_cr.get('status')}"
        result = _get_execution_result(final_cr)
        assert result.get("rotation_result", {}).get("fingerprint"), "New cert fingerprint must be set"
        log("[PHASE5] PASS")
