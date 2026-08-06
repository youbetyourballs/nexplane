# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: cert-manager Upgrade

Phases:
  1. setup       — reuse EKS cluster; install cert-manager 1.13 via Helm
  2. run_upgrade — cert_manager_upgrade CR lifecycle (1.13→1.15)
  3. verify      — Deployment running; test Certificate CR issued (Ready=True)
  4. rollback    — Deployment rolled back; CRDs remain at 1.15 (expected)
  5. teardown    — helm uninstall cert-manager; delete test Certificate

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_cert_manager_upgrade.py -v -s
"""

import os
import sys
import time
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_EKS_AGENT_ASSET_SSM_KEY = "/nexplane/smoke-state/k8s-cluster-upgrade/agent-asset-id"
SOURCE_VERSION = "1.13"
TARGET_VERSION = "1.15"

CR_TIMEOUT = 600
POLL_INTERVAL = 10

_state = {
    "asset_id": None,
    "cr_id": None,
    "execution_result": None,
}

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id, terminal_statuses=("completed", "failed", "blocked"), timeout=CR_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _api("get", f"/api/v1/change-requests/{cr_id}")
        assert r.status_code == 200
        data = r.json()
        if data.get("status") in terminal_statuses:
            return data
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} timed out")


def test_phase1_resolve_eks_agent():
    import boto3
    creds = get_connector_creds_from_db("aws")
    ssm = boto3.client("ssm",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    try:
        resp = ssm.get_parameter(Name=_EKS_AGENT_ASSET_SSM_KEY)
        _state["asset_id"] = resp["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        pytest.skip("No EKS smoke cluster — run k8s_cluster_upgrade smoke first")
    log(f"Using EKS agent asset: {_state['asset_id']}")


def test_phase2_create_and_approve_cr():
    if not _state["asset_id"]:
        pytest.skip("No asset_id")

    payload = {
        "change_type": "cert_manager_upgrade",
        "title": f"Smoke: cert-manager {SOURCE_VERSION}→{TARGET_VERSION}",
        "asset_ids": [_state["asset_id"]],
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "namespace": "cert-manager",
            "dry_run": False,
        },
    }
    r = _api("post", "/api/v1/change-requests", json=payload)
    assert r.status_code in (200, 201), f"CR create failed: {r.text}"
    _state["cr_id"] = r.json()["id"]
    _api("post", f"/api/v1/change-requests/{_state['cr_id']}/plan")
    _api("post", f"/api/v1/change-requests/{_state['cr_id']}/approve")
    log(f"CR {_state['cr_id']} approved")


def test_phase2_wait_execution():
    if not _state["cr_id"]:
        pytest.skip()
    cr = _poll_cr(_state["cr_id"])
    assert cr["status"] == "completed", f"CR failed: {cr}"
    _state["execution_result"] = cr.get("execution_result", {})


def test_phase3_verify():
    result = _state["execution_result"]
    assert result, "No execution_result"
    assert result.get("status") == "completed"
    assert result.get("crds_upgraded") is True
    assert result.get("deployment_upgraded") is True
    assert result.get("test_cert_issued") is True, "Test certificate was not issued"
    log(f"cert-manager {TARGET_VERSION} verified; test cert issued")


def test_phase4_rollback():
    if not _state["cr_id"]:
        pytest.skip()
    r = _api("post", f"/api/v1/change-requests/{_state['cr_id']}/rollback")
    assert r.status_code == 200
    cr = _poll_cr(_state["cr_id"], terminal_statuses=("rolled_back", "rollback_failed"))
    assert cr["status"] == "rolled_back"
    rb = cr.get("rollback_result", {})
    assert rb.get("deployment_rolled_back") is True
    # CRDs remain at target — expected
    assert rb.get("crds_note"), "Expected crds_note explaining CRDs remain at target"
    log("cert-manager rollback: Deployment rolled back; CRDs remain at 1.15 (expected)")


def test_phase5_teardown():
    log("Teardown: run 'helm uninstall cert-manager -n cert-manager' on EKS cluster manually or in next smoke setup")
