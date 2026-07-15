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

PHASE = "CONTAINERIZE"
TIMEOUT = 300


def _run_cr(client, label, action_id, params, connector_type="nexplane_agent", timeout=TIMEOUT):
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {
            "connector_type": connector_type,
            "action_id": action_id,
            "params": params,
        },
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
        json={"decision": "approved", "comment": "smoke"},
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
        if rollback:
            steps = result.get("rollback_steps", [])
            if steps:
                return steps[0].get("result") or {}
            if "rolled_back" in result:
                return result
        else:
            steps = result.get("execution", {}).get("steps", [])
            if steps:
                return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


class TestContainerizeSmoke:

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.agent_creds = get_connector_creds_from_db("nexplane_agent")
        if self.agent_creds is None:
            pytest.skip("No nexplane_agent connector in platform DB")

    def test_containerize_build_dry_run(self):
        """CONTAINERIZE_BUILD_DRY — generate Dockerfile + manifest, no docker build."""
        asset_id = self.agent_creds.get("smoke_asset_id")
        if not asset_id:
            pytest.skip("No smoke_asset_id in nexplane_agent creds")
        app_name = self.agent_creds.get("smoke_app_name", "nexplane-smoke-app")
        cr = _run_cr(
            self.client,
            "[smoke] containerize_build dry_run",
            "agent_containerize_build",
            {"app_name": app_name, "registry": "nexplane-local", "dry_run": True},
        )
        result = _step_result(cr)
        assert result.get("dockerfile") or result.get("dry_run") is True or result.get("manifests"), (
            f"Expected dry_run artifacts in result, got: {result}"
        )
        log(f"{PHASE}: BUILD_DRY passed")

    def test_containerize_build_rollback(self):
        """CONTAINERIZE_BUILD_ROLLBACK — real build then rollback deletes image."""
        registry = self.agent_creds.get("registry")
        asset_id = self.agent_creds.get("smoke_asset_id")
        app_name = self.agent_creds.get("smoke_app_name", "nexplane-smoke-app")
        if not registry or not asset_id:
            pytest.skip("No registry or smoke_asset_id in nexplane_agent creds")
        cr = _run_cr(
            self.client,
            "[smoke] containerize_build live",
            "agent_containerize_build",
            {"app_name": app_name, "registry": registry, "dry_run": False},
        )
        cr_id = cr["id"]
        result = _step_result(cr)
        assert result.get("image_name"), f"Expected image_name in result, got: {result}"
        log(f"{PHASE}: BUILD executed image={result.get('image_name')}")
        cr = _rollback_cr(self.client, cr_id, "rollback build")
        rb_result = _step_result(cr, rollback=True)
        assert rb_result.get("rolled_back") is True, f"Expected rolled_back=True, got: {rb_result}"
        log(f"{PHASE}: BUILD_ROLLBACK passed")

    def test_containerize_retire_rollback(self):
        """CONTAINERIZE_RETIRE — stop test service, rollback restarts it."""
        systemd_unit = self.agent_creds.get("smoke_test_service")
        asset_id = self.agent_creds.get("smoke_asset_id")
        if not systemd_unit or not asset_id:
            pytest.skip("No smoke_test_service or smoke_asset_id in nexplane_agent creds")
        cr = _run_cr(
            self.client,
            "[smoke] containerize_retire test service",
            "agent_containerize_retire",
            {"systemd_unit": systemd_unit},
        )
        cr_id = cr["id"]
        result = _step_result(cr)
        assert result.get("retired") is True or result.get("stopped") is True, (
            f"Expected retired/stopped=True, got: {result}"
        )
        log(f"{PHASE}: RETIRE executed unit={systemd_unit}")
        cr = _rollback_cr(self.client, cr_id, "rollback retire")
        rb_result = _step_result(cr, rollback=True)
        assert rb_result.get("rolled_back") is True, f"Expected rolled_back=True, got: {rb_result}"
        log(f"{PHASE}: RETIRE_ROLLBACK passed")

    def test_containerize_auto_dry_run(self):
        """CONTAINERIZE_AUTO_DRY — full 7-stage pipeline with dry_run=True."""
        asset_id = self.agent_creds.get("smoke_asset_id")
        if not asset_id:
            pytest.skip("No smoke_asset_id in nexplane_agent creds")
        registry = self.agent_creds.get("registry", "nexplane-local")
        cr = _run_cr(
            self.client,
            "[smoke] containerize_auto dry_run",
            "agent_containerize_auto",
            {"registry": registry, "dry_run": True},
            timeout=600,
        )
        result = _step_result(cr)
        assert (
            result.get("stages_completed")
            or result.get("dry_run") is True
            or result.get("containerized")
        ), f"Expected auto dry_run result, got: {result}"
        log(f"{PHASE}: AUTO_DRY passed")

    def test_containerize_ssh_inplace(self):
        """CONTAINERIZE_SSH_INPLACE — adaptive SSH executor, verify rollback restores service."""
        ssh_creds = get_connector_creds_from_db("ssh")
        if ssh_creds is None:
            pytest.skip("No SSH connector in platform DB")
        test_service = ssh_creds.get("smoke_test_service")
        if not test_service:
            pytest.skip("No smoke_test_service in SSH creds")
        test_registry = ssh_creds.get("registry", "nexplane-local")
        cr = _run_cr(
            self.client,
            "[smoke] ssh containerize_workload",
            "ssh_containerize_workload",
            {"service_name": test_service, "registry": test_registry},
            connector_type="ssh",
        )
        cr_id = cr["id"]
        result = _step_result(cr)
        assert result.get("containerized") is True, f"Expected containerized=True, got: {result}"
        log(f"{PHASE}: SSH path={result.get('path')}")
        cr = _rollback_cr(self.client, cr_id, "rollback ssh containerize")
        rb_result = _step_result(cr, rollback=True)
        assert rb_result.get("rolled_back") is True, f"Expected rolled_back=True, got: {rb_result}"
        log(f"{PHASE}: SSH_INPLACE_ROLLBACK passed")
