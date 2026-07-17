# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Credential Rotation Fanout Smoke Test

Phases:
  1. EXECUTE — pre-seed a K8s ConfigMap with a known value, create a
     credential_rotation_fanout CR to scan for it and replace it, assert
     the new value is present after execution.
  2. ROLLBACK — trigger rollback on the same CR, assert the ConfigMap is
     restored to the original value.

Run from EC2:
  docker exec nexplane-backend-1 python -m pytest \\
    /app/tests/smoke/test_credential_rotation_fanout_smoke.py -v -s
"""

import os
import sys
import time
import uuid

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

EXECUTE_TIMEOUT = 120
ROLLBACK_TIMEOUT = 120
SMOKE_NAMESPACE = "default"
SMOKE_CM_KEY = "secret"
OLD_VALUE = "old-secret-value"
NEW_VALUE = "new-secret-value"

pytestmark = pytest.mark.SMOKE


# ---------------------------------------------------------------------------
# Helpers — K8s direct SDK (verification only, not used for CR execution)
# ---------------------------------------------------------------------------

def _get_k8s_core_client(creds: dict):
    from kubernetes import client as k8s_client, config as k8s_config
    kubeconfig_raw = creds.get("kubeconfig")
    if kubeconfig_raw:
        kubeconfig_dict = yaml.safe_load(kubeconfig_raw) if isinstance(kubeconfig_raw, str) else kubeconfig_raw
        k8s_config.load_kube_config_from_dict(kubeconfig_dict)
    else:
        server = creds.get("server")
        token = creds.get("token")
        if not server or not token:
            pytest.skip("No kubeconfig or server/token in Kubernetes connector creds")
        configuration = k8s_client.Configuration()
        configuration.host = server
        configuration.api_key = {"authorization": f"Bearer {token}"}
        configuration.verify_ssl = False
        k8s_client.Configuration.set_default(configuration)
    return k8s_client.CoreV1Api()


def _create_or_update_configmap(core_v1, name: str, namespace: str, key: str, value: str) -> None:
    from kubernetes import client as k8s_client
    body = k8s_client.V1ConfigMap(
        metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
        data={key: value},
    )
    try:
        core_v1.read_namespaced_config_map(name, namespace)
        core_v1.replace_namespaced_config_map(name, namespace, body)
        log(f"[FANOUT] Updated ConfigMap {name}/{key}={value!r}")
    except Exception:
        core_v1.create_namespaced_config_map(namespace, body)
        log(f"[FANOUT] Created ConfigMap {name}/{key}={value!r}")


def _read_configmap_value(core_v1, name: str, namespace: str, key: str) -> str:
    cm = core_v1.read_namespaced_config_map(name, namespace)
    return (cm.data or {}).get(key, "")


def _delete_configmap(core_v1, name: str, namespace: str) -> None:
    try:
        core_v1.delete_namespaced_config_map(name, namespace)
        log(f"[FANOUT] Deleted ConfigMap {name}")
    except Exception as exc:
        log(f"[FANOUT] Cleanup skipped: {exc}")


# ---------------------------------------------------------------------------
# Helpers — CR lifecycle via Nexplane API (dogfooding)
# ---------------------------------------------------------------------------

def _run_fanout_cr(client: NexplaneClient, title: str, search_terms: list, scan_scope: list,
                   new_value: str, timeout: int) -> dict:
    """Create → plan → approve → execute a credential_rotation_fanout CR; poll until done."""
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": title,
        "change_type": "credential_rotation_fanout",
        "desired_outcome": {
            "search_terms": search_terms,
            "scan_scope": scan_scope,
            "new_value": new_value,
            "_smoke_test": True,
        },
    })
    assert resp.status_code in (200, 201), f"CR create failed {resp.status_code}: {resp.text}"
    cr_id = resp.json()["id"]
    log(f"[FANOUT] CR created: {cr_id}")

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
            raise AssertionError(f"CR {cr_id} ended with status={status!r}: {cr}")
        time.sleep(5)
    raise TimeoutError(f"CR {cr_id} timed out after {timeout}s")


def _rollback_cr(client: NexplaneClient, cr_id: str, timeout: int) -> dict:
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


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestCredentialRotationFanoutSmoke:
    """
    End-to-end smoke for credential_rotation_fanout CR type.

    Phase 1: seeds a ConfigMap with OLD_VALUE, runs the fanout CR to replace
    it with NEW_VALUE, and verifies the ConfigMap was updated.

    Phase 2: triggers rollback on the same CR, verifies the ConfigMap is
    restored to OLD_VALUE, then deletes the test ConfigMap.
    """

    @classmethod
    def setup_class(cls):
        creds = get_connector_creds_from_db("kubernetes")
        if not creds:
            pytest.skip("No Kubernetes connector credentials found in platform database")

        cls.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        cls.creds = creds
        cls.core_v1 = _get_k8s_core_client(creds)
        cls.suffix = uuid.uuid4().hex[:8]
        cls.cm_name = f"nexplane-smoke-fanout-{cls.suffix}"
        cls.cr_id = None

        log(f"[FANOUT] Pre-seeding ConfigMap {cls.cm_name} with old value")
        _create_or_update_configmap(cls.core_v1, cls.cm_name, SMOKE_NAMESPACE, SMOKE_CM_KEY, OLD_VALUE)

    @classmethod
    def teardown_class(cls):
        log("[FANOUT] Teardown: deleting smoke ConfigMap")
        try:
            _delete_configmap(cls.core_v1, cls.cm_name, SMOKE_NAMESPACE)
        except Exception as exc:
            log(f"[FANOUT] Teardown warning: {exc}")

    def test_phase1_execute(self):
        """Fan-out scan finds the seeded ConfigMap and replaces OLD_VALUE with NEW_VALUE."""
        log("[PHASE1] Starting credential_rotation_fanout execute smoke")

        cr = _run_fanout_cr(
            self.client,
            f"[SMOKE] credential_rotation_fanout {self.suffix}",
            search_terms=[OLD_VALUE],
            scan_scope=["kubernetes"],
            new_value=NEW_VALUE,
            timeout=EXECUTE_TIMEOUT,
        )
        assert cr["status"] == "completed", f"Expected completed, got {cr['status']}: {cr}"
        self.__class__.cr_id = cr["id"]
        log(f"[PHASE1] CR {cr['id']} completed")

        # Verify via direct K8s SDK that the ConfigMap was updated
        actual = _read_configmap_value(self.core_v1, self.cm_name, SMOKE_NAMESPACE, SMOKE_CM_KEY)
        assert actual == NEW_VALUE, (
            f"ConfigMap {self.cm_name}[{SMOKE_CM_KEY}] should be {NEW_VALUE!r}, got {actual!r}"
        )
        log(f"[PHASE1] ConfigMap value confirmed: {actual!r}")

        # Verify executor result contains our ConfigMap as an updated consumer
        consumers = []
        for run in cr.get("execution_runs", []):
            result = run.get("result") or {}
            consumers = result.get("consumers", [])
            if consumers:
                break

        updated = [c for c in consumers if c.get("update_result", {}).get("status") == "updated"]
        assert any(self.cm_name in c.get("location", "") for c in updated), (
            f"Expected ConfigMap {self.cm_name} in updated consumers; got: {consumers}"
        )
        log("[PHASE1] PASS")

    def test_phase2_rollback(self):
        """Rollback restores the ConfigMap to OLD_VALUE."""
        if not self.cr_id:
            pytest.skip("Phase 1 did not complete — no CR to roll back")

        log("[PHASE2] Starting rollback smoke")
        rb_cr = _rollback_cr(self.client, self.cr_id, ROLLBACK_TIMEOUT)
        assert rb_cr["status"] in (
            "rolled_back", "rolled_back_with_warnings", "rollback_partial"
        ), f"Unexpected rollback status: {rb_cr['status']}: {rb_cr}"
        log(f"[PHASE2] Rollback status: {rb_cr['status']}")

        # Verify via direct K8s SDK that the ConfigMap was restored
        actual = _read_configmap_value(self.core_v1, self.cm_name, SMOKE_NAMESPACE, SMOKE_CM_KEY)
        assert actual == OLD_VALUE, (
            f"ConfigMap {self.cm_name}[{SMOKE_CM_KEY}] should be restored to {OLD_VALUE!r}, got {actual!r}"
        )
        log(f"[PHASE2] ConfigMap value restored: {actual!r}")
        log("[PHASE2] PASS")
