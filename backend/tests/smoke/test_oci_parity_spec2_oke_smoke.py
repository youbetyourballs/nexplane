# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OCI Parity Spec 2 — OKE Lifecycle Smoke Test

Phases (single test, shared cluster state):
  1. CREATE cluster + initial node pool (bundled CR, ~20 min)
  2. GET kubeconfig
  3. SCALE node pool up by 1 → rollback
  4. UPDATE node pool name → rollback
  5. ADD second node pool → wait ACTIVE
  6. DELETE second node pool → verify drain_recommended key present
  7. DELETE cluster (handles remaining node pool internally)

Timeout: 2400s (40 min) per pytest-timeout.
Run from EC2:
  docker exec nexplane-backend-1 python -m pytest \
    /app/tests/smoke/test_oci_parity_spec2_oke_smoke.py -v -s --timeout=2400
"""

import os
import time
import uuid

import pytest

from smoke_helpers import (
    NexplaneClient,
    OCI_CONNECTOR_ID,
    log,
    _get_oci_creds,
    _get_oci_network_client,
    _get_oci_container_engine_client,
)

BASE_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

CREATE_TIMEOUT = 1800   # 30 min: cluster + node pool provisioning
DAY2_TIMEOUT = 900      # 15 min: scale / update / add pool
DELETE_TIMEOUT = 1800   # 30 min: delete cluster (drains pools first)


class OciInfraConstraint(Exception):
    """Raised when OCI infrastructure prerequisites are missing (VCN, subnet, k8s version)."""
    pass


def _get_client():
    return NexplaneClient(BASE_URL, EMAIL, PASSWORD)


def _run_cr(client, label, action_id, params, timeout):
    """Create -> plan -> submit-for-approval -> approve -> execute -> poll until terminal."""
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

    r = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "oke-smoke"},
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
            raise AssertionError(f"[{label}] CR {cr_id} status={status!r}\n{str(cr.get('execution_runs', ''))[:400]}")
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


def _get_vcn_and_subnet():
    """Return (vcn_id, subnet_id, compartment_id) from the first available subnet."""
    creds = _get_oci_creds()
    compartment_id = creds.get("compartment_id", creds.get("tenancy", ""))
    net = _get_oci_network_client()
    if not net:
        pytest.skip("Cannot get network client")
    subnets = net.list_subnets(compartment_id=compartment_id).data
    if not subnets:
        pytest.skip("No subnets found in compartment — cannot create OKE cluster")
    subnet = subnets[0]
    return subnet.vcn_id, subnet.id, compartment_id


def _get_kubernetes_version():
    """Return the latest available OKE Kubernetes version."""
    ce = _get_oci_container_engine_client()
    if not ce:
        pytest.skip("Cannot get container engine client")
    options = ce.get_cluster_options(cluster_option_id="all").data
    versions = options.kubernetes_versions
    if not versions:
        pytest.skip("No Kubernetes versions available in this tenancy")
    return versions[-1]


class TestOkeLifecycle:
    """Full OKE lifecycle: create -> day-2 ops -> delete."""

    def test_oke_lifecycle(self):
        client = _get_client()
        creds = _get_oci_creds()
        compartment_id = creds.get("compartment_id", creds.get("tenancy", ""))
        vcn_id, subnet_id, _ = _get_vcn_and_subnet()
        k8s_version = _get_kubernetes_version()
        cluster_name = f"nexplane-smoke-{uuid.uuid4().hex[:8]}"
        log(f"[OKE] Using VCN {vcn_id[:40]}... subnet {subnet_id[:40]}... k8s {k8s_version}")

        # ---------------------------------------------------------------
        # Step 1: Create cluster + initial node pool (bundled)
        # ---------------------------------------------------------------
        log(f"[OKE] Creating cluster: {cluster_name}")
        create_cr = _run_cr(
            client, "[OKE] Create cluster + node pool", "oci_create_oke_cluster",
            {
                "compartment_id": compartment_id,
                "name": cluster_name,
                "vcn_id": vcn_id,
                "kubernetes_version": k8s_version,
                "subnet_ids": [subnet_id],
                "node_shape": "VM.Standard.E3.Flex",
                "node_count": 1,
            },
            timeout=CREATE_TIMEOUT,
        )
        create_result = _step_result(create_cr)
        cluster_id = create_result.get("cluster_id", "")
        node_pool_id = create_result.get("node_pool_id", "")
        assert cluster_id, f"cluster_id missing from create result: {create_result}"
        assert node_pool_id, f"node_pool_id missing from create result: {create_result}"
        create_cr_id = create_cr["id"]
        log(f"[OKE] Cluster: {cluster_id[:40]}... Pool: {node_pool_id[:40]}...")

        try:
            # -----------------------------------------------------------
            # Step 2: Get kubeconfig
            # -----------------------------------------------------------
            log("[OKE] Getting kubeconfig")
            kc_cr = _run_cr(
                client, "[OKE] Get kubeconfig", "oci_get_oke_kubeconfig",
                {"cluster_id": cluster_id},
                timeout=60,
            )
            kc_result = _step_result(kc_cr)
            kubeconfig = kc_result.get("kubeconfig", "")
            assert kubeconfig, f"kubeconfig missing from result: {kc_result}"
            assert "apiVersion" in kubeconfig, f"kubeconfig is not YAML: {kubeconfig[:100]}"
            log("[OKE] Kubeconfig retrieved OK")

            # -----------------------------------------------------------
            # Step 3: Scale node pool up by 1, then rollback
            # -----------------------------------------------------------
            log("[OKE] Scaling node pool to 2")
            scale_cr = _run_cr(
                client, "[OKE] Scale node pool", "oci_scale_oke_node_pool",
                {"node_pool_id": node_pool_id, "node_count": 2},
                timeout=DAY2_TIMEOUT,
            )
            scale_result = _step_result(scale_cr)
            assert scale_result.get("scaled") is True, f"Scale result: {scale_result}"
            assert scale_result.get("new_count") == 2
            log("[OKE] Scale OK — rolling back to 1")
            _rollback(client, scale_cr["id"], "[OKE] rollback scale", timeout=DAY2_TIMEOUT)
            log("[OKE] Scale rollback OK")

            # -----------------------------------------------------------
            # Step 4: Update node pool name, then rollback
            # -----------------------------------------------------------
            new_pool_name = f"{cluster_name}-pool-renamed"
            log(f"[OKE] Updating node pool name to: {new_pool_name}")
            update_cr = _run_cr(
                client, "[OKE] Update node pool name", "oci_update_oke_node_pool",
                {"node_pool_id": node_pool_id, "name": new_pool_name},
                timeout=DAY2_TIMEOUT,
            )
            update_result = _step_result(update_cr)
            assert update_result.get("updated") is True, f"Update result: {update_result}"
            log("[OKE] Update OK — rolling back")
            _rollback(client, update_cr["id"], "[OKE] rollback update", timeout=DAY2_TIMEOUT)
            log("[OKE] Update rollback OK")

            # -----------------------------------------------------------
            # Step 5: Add second node pool
            # -----------------------------------------------------------
            pool2_name = f"{cluster_name}-pool-2"
            log(f"[OKE] Adding second node pool: {pool2_name}")
            add_cr = _run_cr(
                client, "[OKE] Add second node pool", "oci_add_oke_node_pool",
                {
                    "cluster_id": cluster_id,
                    "compartment_id": compartment_id,
                    "name": pool2_name,
                    "kubernetes_version": k8s_version,
                    "node_shape": "VM.Standard.E3.Flex",
                    "node_count": 1,
                    "subnet_id": subnet_id,
                },
                timeout=DAY2_TIMEOUT,
            )
            add_result = _step_result(add_cr)
            pool2_id = add_result.get("node_pool_id", "")
            assert pool2_id, f"node_pool_id missing from add result: {add_result}"
            log(f"[OKE] Second pool: {pool2_id[:40]}...")

            # -----------------------------------------------------------
            # Step 6: Delete second node pool
            # -----------------------------------------------------------
            log("[OKE] Deleting second node pool")
            del_pool_cr = _run_cr(
                client, "[OKE] Delete second node pool", "oci_delete_oke_node_pool",
                {"node_pool_id": pool2_id},
                timeout=DAY2_TIMEOUT,
            )
            del_pool_result = _step_result(del_pool_cr)
            assert del_pool_result.get("deleted") is True, f"Delete pool result: {del_pool_result}"
            assert "drain_recommended" in del_pool_result, f"drain_recommended key missing: {del_pool_result}"
            log(f"[OKE] Second pool deleted. drain_recommended={del_pool_result.get('drain_recommended')}")

        finally:
            # -----------------------------------------------------------
            # Step 7: Delete cluster (handles remaining node pool)
            # -----------------------------------------------------------
            log("[OKE] Deleting cluster (cleanup)")
            try:
                _run_cr(
                    client, "[OKE] Delete cluster", "oci_delete_oke_cluster",
                    {"cluster_id": cluster_id, "compartment_id": compartment_id},
                    timeout=DELETE_TIMEOUT,
                )
                log("[OKE] Cluster deleted")
            except Exception as e:
                log(f"[OKE] Cluster delete warning (non-fatal in finally): {e}", ok=False)

        log("[OKE] PASS — full lifecycle: create, kubeconfig, scale, update, add pool, delete pool, delete cluster")
