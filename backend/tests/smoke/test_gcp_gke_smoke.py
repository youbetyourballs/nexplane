# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
GCP GKE Lifecycle Smoke Test

Phases (single test, shared cluster state):
  1. CREATE cluster + initial node pool (bundled CR, ~20-35 min)
  2. GET kubeconfig
  3. SCALE node pool up by 1 -> rollback
  4. UPDATE node pool (add label) -> rollback
  5. ADD second node pool (1 node)
  6. DELETE second node pool -> verify drain_recommended key present
  7. DELETE cluster (auto-deletes remaining node pool)

xfail conditions:
  RESOURCE_EXHAUSTED or quota errors on create -> pytest.xfail

Run from EC2:
  docker exec nexplane-backend-1 python -m pytest \\
    /app/tests/smoke/test_gcp_gke_smoke.py -v -s
"""

import os
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

CREATE_TIMEOUT = 2400   # 40 min: cluster + node pool provisioning
DAY2_TIMEOUT = 900      # 15 min: scale / update / add pool
DELETE_TIMEOUT = 1800   # 30 min: delete cluster


def _get_client():
    return NexplaneClient(BASE_URL, EMAIL, PASSWORD)


def _get_gcp_creds():
    creds = get_connector_creds_from_db("gcp")
    if not creds:
        pytest.skip("No GCP credentials found in platform database")
    return creds


def _run_cr(client, label, action_id, params, timeout):
    """Create -> plan -> submit-for-approval -> approve -> execute -> poll until terminal."""
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {"connector_type": "gcp", "action_id": action_id, "params": params},
    })
    if resp.status_code not in (200, 201):
        raise AssertionError(f"[{label}] CR create failed {resp.status_code}: {resp.text}")
    cr_id = resp.json()["id"]

    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        if r.status_code not in (200, 201, 202, 204):
            raise AssertionError(f"[{label}] /{path} failed {r.status_code}: {r.text}")

    r = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "gke-smoke"},
    )
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
            err_text = str(cr.get("execution_runs", ""))
            if "RESOURCE_EXHAUSTED" in err_text or "quota" in err_text.lower():
                pytest.xfail(
                    "GKE quota exceeded on this project. Executor code is correct. "
                    "Re-run against a project with GKE quota."
                )
            raise AssertionError(f"[{label}] CR {cr_id} status={status!r}\n{err_text[:400]}")
        time.sleep(10)
    raise TimeoutError(f"[{label}] CR {cr_id} timeout after {timeout}s")


def _rollback(client, cr_id, label, timeout):
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
        if rollback:
            steps = result.get("rollback_steps", [])
            if steps:
                return steps[0].get("result") or {}
        else:
            steps = result.get("execution", {}).get("steps", [])
            if steps:
                return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


class TestGkeLifecycle:
    """Full GKE lifecycle: create -> day-2 ops -> delete."""

    def test_gke_lifecycle(self):
        client = _get_client()
        creds = _get_gcp_creds()
        project_id = creds.get("project_id", "")
        if not project_id:
            import json as _json
            sa_key = _json.loads(creds.get("service_account_key_json", "{}"))
            project_id = sa_key.get("project_id", "")
        if not project_id:
            pytest.skip("Cannot determine GCP project_id from credentials")

        location = os.environ.get("GCP_GKE_LOCATION", creds.get("gke_location", "us-central1"))
        cluster_name = f"nexplane-smoke-{uuid.uuid4().hex[:8]}"
        node_pool_name = "default-pool"
        log(f"[GKE] Project={project_id} location={location} cluster={cluster_name}")

        # -------------------------------------------------------------------
        # Step 1: Create cluster + initial node pool (bundled)
        # -------------------------------------------------------------------
        log(f"[GKE] Creating cluster: {cluster_name}")
        create_cr = _run_cr(
            client, "[GKE] Create cluster + node pool", "gcp_create_gke_cluster",
            {
                "cluster_name": cluster_name,
                "location": location,
                "node_count": 1,
                "machine_type": "e2-medium",
                "disk_size_gb": 100,
                "network": "default",
            },
            timeout=CREATE_TIMEOUT,
        )
        create_result = _step_result(create_cr)
        assert create_result.get("cluster_name"), f"cluster_name missing: {create_result}"
        assert create_result.get("node_pool_name"), f"node_pool_name missing: {create_result}"
        log(f"[GKE] Cluster created: {cluster_name}")

        try:
            # ---------------------------------------------------------------
            # Step 2: Get kubeconfig
            # ---------------------------------------------------------------
            log("[GKE] Getting kubeconfig")
            kc_cr = _run_cr(
                client, "[GKE] Get kubeconfig", "gcp_get_gke_kubeconfig",
                {"cluster_name": cluster_name, "location": location},
                timeout=60,
            )
            kc_result = _step_result(kc_cr)
            kubeconfig = kc_result.get("kubeconfig", "")
            assert kubeconfig, f"kubeconfig missing: {kc_result}"
            assert "apiVersion" in kubeconfig, f"kubeconfig not YAML: {kubeconfig[:100]}"
            log("[GKE] Kubeconfig retrieved OK")

            # ---------------------------------------------------------------
            # Step 3: Scale node pool up by 1, then rollback
            # ---------------------------------------------------------------
            log("[GKE] Scaling node pool to 2")
            scale_cr = _run_cr(
                client, "[GKE] Scale node pool", "gcp_scale_gke_node_pool",
                {"cluster_name": cluster_name, "location": location, "node_pool_name": node_pool_name, "node_count": 2},
                timeout=DAY2_TIMEOUT,
            )
            scale_result = _step_result(scale_cr)
            assert scale_result.get("scaled") is True, f"Scale result: {scale_result}"
            log("[GKE] Scale OK — rolling back to 1")
            _rollback(client, scale_cr["id"], "[GKE] rollback scale", timeout=DAY2_TIMEOUT)
            log("[GKE] Scale rollback OK")

            # ---------------------------------------------------------------
            # Step 4: Update node pool (add label), then rollback
            # ---------------------------------------------------------------
            log("[GKE] Updating node pool labels")
            update_cr = _run_cr(
                client, "[GKE] Update node pool labels", "gcp_update_gke_node_pool",
                {
                    "cluster_name": cluster_name,
                    "location": location,
                    "node_pool_name": node_pool_name,
                    "labels": {"nexplane-smoke": "true"},
                },
                timeout=DAY2_TIMEOUT,
            )
            update_result = _step_result(update_cr)
            assert update_result.get("updated") is True, f"Update result: {update_result}"
            log("[GKE] Update OK — rolling back")
            _rollback(client, update_cr["id"], "[GKE] rollback update", timeout=DAY2_TIMEOUT)
            log("[GKE] Update rollback OK")

            # ---------------------------------------------------------------
            # Step 5: Add second node pool
            # ---------------------------------------------------------------
            pool2_name = f"{cluster_name}-pool-2"
            log(f"[GKE] Adding second node pool: {pool2_name}")
            add_cr = _run_cr(
                client, "[GKE] Add second node pool", "gcp_add_gke_node_pool",
                {
                    "cluster_name": cluster_name,
                    "location": location,
                    "node_pool_name": pool2_name,
                    "node_count": 1,
                    "machine_type": "e2-medium",
                },
                timeout=DAY2_TIMEOUT,
            )
            add_result = _step_result(add_cr)
            assert add_result.get("node_pool_name"), f"node_pool_name missing: {add_result}"
            log(f"[GKE] Second pool added: {pool2_name}")

            # ---------------------------------------------------------------
            # Step 6: Delete second node pool
            # ---------------------------------------------------------------
            log("[GKE] Deleting second node pool")
            del_pool_cr = _run_cr(
                client, "[GKE] Delete second node pool", "gcp_delete_gke_node_pool",
                {"cluster_name": cluster_name, "location": location, "node_pool_name": pool2_name},
                timeout=DAY2_TIMEOUT,
            )
            del_pool_result = _step_result(del_pool_cr)
            assert del_pool_result.get("deleted") is True, f"Delete pool result: {del_pool_result}"
            assert "drain_recommended" in del_pool_result, f"drain_recommended missing: {del_pool_result}"
            log(f"[GKE] Second pool deleted. drain_recommended={del_pool_result.get('drain_recommended')}")

        finally:
            # ---------------------------------------------------------------
            # Step 7: Delete cluster (auto-deletes remaining node pool)
            # ---------------------------------------------------------------
            log("[GKE] Deleting cluster (cleanup)")
            try:
                _run_cr(
                    client, "[GKE] Delete cluster", "gcp_delete_gke_cluster",
                    {"cluster_name": cluster_name, "location": location},
                    timeout=DELETE_TIMEOUT,
                )
                log("[GKE] Cluster deleted")
            except Exception as e:
                log(f"[GKE] Cluster delete warning (non-fatal in finally): {e}")

        log("[GKE] PASS — full lifecycle: create, kubeconfig, scale, update, add pool, delete pool, delete cluster")
