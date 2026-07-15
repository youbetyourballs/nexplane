# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import os
import time
import pytest
import sys

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@nexplane.local")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin")

PHASE = "AD_ROLLBACK"
TIMEOUT = 120
UAC_DISABLED_BIT = 0x2


def _run_cr(client, label, action_id, params, timeout=TIMEOUT):
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {"connector_type": "active_directory", "action_id": action_id, "params": params},
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
        if rollback:
            steps = result.get("rollback_steps", [])
            if steps:
                return steps[0].get("result") or {}
        else:
            steps = result.get("execution", {}).get("steps", [])
            if steps:
                return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


def _get_user_account_control(creds, user_dn):
    from ldap3 import Server, Connection, SUBTREE
    server = Server(creds["domain_controller"])
    conn = Connection(server, creds["bind_dn"], creds["bind_password"], auto_bind=True)
    conn.search(
        creds["base_dn"],
        f"(distinguishedName={user_dn})",
        SUBTREE,
        attributes=["userAccountControl"],
    )
    if not conn.entries:
        conn.unbind()
        raise AssertionError(f"User not found in LDAP: {user_dn}")
    uac = int(conn.entries[0].userAccountControl.value)
    conn.unbind()
    return uac


class TestAdRollbackSmoke:
    """
    End-to-end rollback smoke for the Active Directory disable_account executor.
    Disables a smoke test account via CR, verifies UAC disabled bit, rolls back, verifies re-enabled.
    """

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.creds = get_connector_creds_from_db("active_directory")
        if self.creds is None:
            pytest.skip("No Active Directory credentials in platform DB")
        self.username = self.creds.get("smoke_test_username")
        self.user_dn = self.creds.get("smoke_test_user_dn")
        if not self.username or not self.user_dn:
            pytest.skip(
                "No smoke_test_username/smoke_test_user_dn in AD creds — cannot run disable_account rollback smoke"
            )

    def test_disable_account_rollback(self):
        # Step 1: Verify account is enabled before test
        uac = _get_user_account_control(self.creds, self.user_dn)
        is_disabled = bool(uac & UAC_DISABLED_BIT)
        assert not is_disabled, (
            f"Smoke account must be ENABLED before test. UAC={uac:#010x}, disabled bit is set."
        )
        log(f"{PHASE}: account {self.username} confirmed ENABLED (UAC={uac:#010x})")

        # Step 2: Execute disable_account CR
        cr = _run_cr(
            self.client,
            f"[smoke] disable account {self.username}",
            "disable_account",
            {
                "username": self.username,
                "user_dn": self.user_dn,
            },
        )
        cr_id = cr["id"]

        disable_result = _step_result(cr, rollback=False)
        assert disable_result.get("disabled") is True, (
            f"Expected disabled=True, got: {disable_result}"
        )
        log(f"{PHASE}: disable_account executed (cr={cr_id})")

        # Step 3: Verify via LDAP that account is now disabled
        uac = _get_user_account_control(self.creds, self.user_dn)
        is_disabled = bool(uac & UAC_DISABLED_BIT)
        assert is_disabled, (
            f"Expected account disabled after CR, but UAC={uac:#010x} — disabled bit not set"
        )
        log(f"{PHASE}: LDAP confirmed account DISABLED (UAC={uac:#010x})")

        # Step 4: Rollback
        cr = _rollback_cr(self.client, cr_id, f"rollback disable_account {self.username}")

        rollback_result = _step_result(cr, rollback=True)
        assert rollback_result.get("rolled_back") is True, (
            f"Expected rolled_back=True, got: {rollback_result}"
        )
        log(f"{PHASE}: rollback completed (cr={cr_id})")

        # Step 5: Verify via LDAP that account is enabled again
        uac = _get_user_account_control(self.creds, self.user_dn)
        is_disabled = bool(uac & UAC_DISABLED_BIT)
        assert not is_disabled, (
            f"Expected account re-enabled after rollback, but UAC={uac:#010x} — disabled bit still set"
        )
        log(f"{PHASE}: LDAP confirmed account re-ENABLED after rollback (UAC={uac:#010x})")
