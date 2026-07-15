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

PHASE = "CR_CONTRACT"


def _create_cr(client, action_id, params):
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": f"[smoke] contract test {action_id}",
        "change_type": "catalog_action",
        "desired_outcome": {
            "connector_type": "gcp",
            "action_id": action_id,
            "params": params,
        },
    })
    return resp


def _plan_cr(client, cr_id):
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/plan")
    return r


def _get_cr(client, cr_id):
    base = client.base
    return client.client.get(f"{base}/change-requests/{cr_id}").json()


class TestCrContractSmoke:
    """
    Verifies observable API contract for rollback metadata in plan output.
    Tests:
      1. Irreversible action -> plan succeeds, rollback_available=False, rollback_warning set
      2. Reversible action -> plan succeeds, rollback_available=True
      3. Non-existent action -> 400 with error message
    """

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)

    def test_irreversible_action_plan_has_rollback_warning(self):
        """Plan for gcp_delete_gke_cluster must expose rollback_available=False and rollback_warning."""
        # gcp_delete_gke_cluster declares ROLLBACK_CAPABILITY = "irreversible"
        # We only plan -- we do NOT execute (deleting a real cluster would be destructive)
        creds = get_connector_creds_from_db("gcp")
        if creds is None:
            pytest.skip("No GCP credentials in platform DB")

        project_id = creds.get("project_id", "smoke-project")
        params = {
            "project_id": project_id,
            "cluster_name": "nexplane-smoke-contract-check",
            "region": creds.get("region", "us-central1"),
        }

        resp = _create_cr(self.client, "gcp_delete_gke_cluster", params)
        assert resp.status_code in (200, 201), f"CR create failed {resp.status_code}: {resp.text}"
        cr_id = resp.json()["id"]

        plan_resp = _plan_cr(self.client, cr_id)
        assert plan_resp.status_code in (200, 201, 202, 204), (
            f"plan failed {plan_resp.status_code}: {plan_resp.text}"
        )

        plan = plan_resp.json()
        blast_radius = plan.get("blast_radius") or {}
        steps = plan.get("generated_steps") or []

        assert blast_radius.get("rollback_available") is False, (
            f"Expected rollback_available=False for irreversible action, got: {blast_radius}"
        )

        # At least one step should carry a rollback_warning
        warnings = [s.get("rollback_warning") for s in steps if s.get("rollback_warning")]
        assert len(warnings) > 0, (
            f"Expected at least one step with rollback_warning, steps: {steps}"
        )

        log(f"{PHASE}: irreversible action plan contract verified (cr={cr_id})")

    def test_reversible_action_plan_has_rollback_available(self):
        """Plan for gcp_create_gke_cluster must expose rollback_available=True."""
        creds = get_connector_creds_from_db("gcp")
        if creds is None:
            pytest.skip("No GCP credentials in platform DB")

        project_id = creds.get("project_id", "smoke-project")
        params = {
            "project_id": project_id,
            "cluster_name": "nexplane-smoke-contract-check-create",
            "region": creds.get("region", "us-central1"),
            "node_count": 1,
            "machine_type": "e2-medium",
        }

        resp = _create_cr(self.client, "gcp_create_gke_cluster", params)
        assert resp.status_code in (200, 201), f"CR create failed {resp.status_code}: {resp.text}"
        cr_id = resp.json()["id"]

        plan_resp = _plan_cr(self.client, cr_id)
        assert plan_resp.status_code in (200, 201, 202, 204), (
            f"plan failed {plan_resp.status_code}: {plan_resp.text}"
        )

        plan = plan_resp.json()
        blast_radius = plan.get("blast_radius") or {}

        assert blast_radius.get("rollback_available") is True, (
            f"Expected rollback_available=True for reversible action, got: {blast_radius}"
        )

        log(f"{PHASE}: reversible action plan contract verified (cr={cr_id})")

    def test_nonexistent_action_returns_400(self):
        """Creating a CR with a non-existent action_id must return 400."""
        resp = _create_cr(self.client, "gcp_totally_nonexistent_action_xyz", {})
        # Either create or plan may return 400 depending on where validation occurs
        _BLOCKED_CODES = (400, 422)
        if resp.status_code in (200, 201):
            cr_id = resp.json()["id"]
            plan_resp = _plan_cr(self.client, cr_id)
            assert plan_resp.status_code in _BLOCKED_CODES, (
                f"Expected {_BLOCKED_CODES} for unknown action, got {plan_resp.status_code}: {plan_resp.text}"
            )
            body = plan_resp.json() if plan_resp.headers.get("content-type", "").startswith("application/json") else {}
            detail = body.get("detail") or body.get("error") or ""
            error_text = (detail if isinstance(detail, str) else str(detail)).lower()
            assert "unknown" in error_text or "not found" in error_text or "invalid" in error_text, (
                f"Expected error message about unknown action, got: {plan_resp.text}"
            )
        else:
            assert resp.status_code in _BLOCKED_CODES, (
                f"Expected {_BLOCKED_CODES} for unknown action on create, got {resp.status_code}: {resp.text}"
            )
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            detail = body.get("detail") or body.get("error") or ""
            error_text = (detail if isinstance(detail, str) else str(detail)).lower()
            assert "unknown" in error_text or "not found" in error_text or "invalid" in error_text, (
                f"Expected error message about unknown action, got: {resp.text}"
            )

        log(f"{PHASE}: non-existent action returns 400 verified")
