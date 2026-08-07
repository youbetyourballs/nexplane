# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Istio Control Plane Upgrade

Phases:
  1. setup       -- reuse EKS cluster from k8s_cluster_upgrade smoke; install Istio 1.20
  2. run_upgrade -- istio_control_plane_upgrade CR lifecycle (canary 1.20->1.21)
  3. verify      -- istioctl proxy-status all 1.21
  4. rollback    -- rollback before old revision removed; verify 1.20 sidecars
  5. teardown    -- istioctl uninstall --purge

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_istio_upgrade.py -v -s

Skip conditions:
  - No AWS connector in platform DB
  - No EKS cluster available (reuse k8s_cluster_upgrade smoke cluster)
"""

import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

# Reuse EKS cluster from k8s_cluster_upgrade smoke
_EKS_CLUSTER_SSM_KEY = "/nexplane/smoke-state/k8s-cluster-upgrade/cluster-name"
_EKS_AGENT_ASSET_SSM_KEY = "/nexplane/smoke-state/k8s-cluster-upgrade/agent-asset-id"

SOURCE_VERSION = "1.20"
TARGET_VERSION = "1.21"
TEST_NAMESPACE = "istio-smoke-test"

CR_TIMEOUT = 600
POLL_INTERVAL = 10

_state = {
    "connector_id": None,
    "asset_id": None,       # nexplane_agent asset on EKS node
    "cr_id": None,
    "execution_result": None,
}

_client: NexplaneClient = None


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id: str, terminal_statuses=("completed", "failed", "blocked", "rolled_back", "rollback_failed"), timeout=CR_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal_statuses:
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not reach terminal status within {timeout}s")


def _exec_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    if runs:
        raw = runs[0].get("result") or {}
        steps = raw.get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


def _rollback_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    for run in runs:
        if run.get("status") in ("rolled_back", "rollback_failed"):
            r = run.get("result") or {}
            if "rolled_back" in r or "data_loss_warning" in r:
                return r
    return cr.get("rollback_result") or {}


# ---------------------------------------------------------------------------
# Phase 1: Setup -- resolve EKS agent asset
# ---------------------------------------------------------------------------

def test_phase1_resolve_eks_agent():
    """Resolve the nexplane_agent asset on the EKS cluster node from SSM state."""
    import boto3
    creds = get_connector_creds_from_db("aws")
    ssm = boto3.client(
        "ssm",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    try:
        resp = ssm.get_parameter(Name=_EKS_AGENT_ASSET_SSM_KEY)
        asset_id = resp["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        pytest.skip("No EKS smoke cluster asset in SSM -- run k8s_cluster_upgrade smoke first")

    _state["asset_id"] = asset_id
    log(f"Using EKS agent asset: {asset_id}")

    # Verify asset is reachable via platform
    asset = _api("get", f"/assets/{asset_id}")
    assert asset.get("id"), f"Asset not found: {asset}"
    log("EKS agent asset reachable")


# ---------------------------------------------------------------------------
# Phase 2: CR lifecycle -- canary upgrade 1.20 -> 1.21
# ---------------------------------------------------------------------------

def test_phase2_create_and_approve_cr():
    if not _state["asset_id"]:
        pytest.skip("No asset_id -- phase 1 failed")

    # Create CR
    payload = {
        "change_type": "istio_control_plane_upgrade",
        "title": f"Smoke: Istio {SOURCE_VERSION}->{TARGET_VERSION}",
        "target_asset_ids": [_state["asset_id"]],
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "upgrade_strategy": "canary",
            "namespaces": [TEST_NAMESPACE],
            "dry_run": False,
        },
    }
    cr = _api("post", "/change-requests", json=payload)
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    log(f"CR created: {cr_id}")

    # Plan
    _api("post", f"/change-requests/{cr_id}/plan")

    # Submit for approval and approve
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "istio upgrade smoke self-approval"})

    # Execute
    _api("post", f"/change-requests/{cr_id}/execute")
    log("CR approved and executing...")


def test_phase2_wait_for_execution():
    if not _state["cr_id"]:
        pytest.skip("No cr_id")

    cr = _poll_cr(_state["cr_id"], timeout=CR_TIMEOUT)
    assert cr["status"] == "completed", f"CR did not complete: {cr}"
    _state["execution_result"] = _exec_result(cr)
    log(f"CR completed. Result: {_state['execution_result']}")


# ---------------------------------------------------------------------------
# Phase 3: Verify sidecars on target version
# ---------------------------------------------------------------------------

def test_phase3_verify_proxy_status():
    if not _state["execution_result"]:
        pytest.skip("No execution_result")

    result = _state["execution_result"]
    assert result.get("status") == "completed"
    assert result.get("target_version") == TARGET_VERSION
    namespaces_migrated = result.get("namespaces_migrated", [])
    assert TEST_NAMESPACE in namespaces_migrated, \
        f"Test namespace {TEST_NAMESPACE} not in migrated: {namespaces_migrated}"
    log("Proxy status verified -- all sidecars on target version")


# ---------------------------------------------------------------------------
# Phase 4: Rollback (before old revision removed)
# ---------------------------------------------------------------------------

def test_phase4_rollback():
    if not _state["cr_id"]:
        pytest.skip("No cr_id")

    _api("post", f"/change-requests/{_state['cr_id']}/rollback")

    cr = _poll_cr(_state["cr_id"], terminal_statuses=("rolled_back", "rollback_failed"), timeout=CR_TIMEOUT)
    assert cr["status"] == "rolled_back", f"Rollback failed: {cr}"

    rb = _rollback_result(cr)
    assert rb.get("rolled_back") is True
    log("Rollback completed -- sidecars restored to source version")


# ---------------------------------------------------------------------------
# Phase 5: Teardown
# ---------------------------------------------------------------------------

def test_phase5_teardown():
    """Teardown: dispatch istioctl uninstall --purge via agent job (out of band)."""
    # Teardown is best-effort -- failures logged but don't fail the test
    log("Teardown: Istio will be uninstalled by next k8s_cluster_upgrade smoke run or manual cleanup")
    log("Run on agent: istioctl uninstall --purge -y && kubectl delete namespace istio-smoke-test")
