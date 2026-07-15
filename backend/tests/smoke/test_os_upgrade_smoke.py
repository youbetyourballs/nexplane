# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import os
import time
import sys
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

PHASE = "OS_UPGRADE"
TIMEOUT = 600  # snapshot + volume swap can take several minutes


def _run_cr(client, label, action_id, params, asset_ids=None, timeout=TIMEOUT):
    base = client.base
    body = {
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {
            "connector_type": "nexplane_agent",
            "action_id": action_id,
            "params": params,
        },
    }
    if asset_ids:
        body["asset_ids"] = asset_ids
    resp = client.client.post(f"{base}/change-requests", json=body)
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
        time.sleep(15)
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
        time.sleep(15)
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


def _ssm_run(instance_id, command, creds, timeout=60):
    """Run a shell command on the instance via SSM and return stdout."""
    import boto3
    ssm = boto3.client(
        "ssm",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command]},
    )
    cmd_id = resp["Command"]["CommandId"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        result = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        if result["Status"] in ("Success", "Failed", "Cancelled"):
            return result.get("StandardOutputContent", "").strip()
    raise TimeoutError(f"SSM command timed out after {timeout}s")


class TestOsUpgradeSmoke:

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.creds = get_connector_creds_from_db("aws")
        if self.creds is None:
            pytest.skip("No AWS credentials in platform DB")
        self.instance_id = self.creds.get("smoke_instance_id")
        self.asset_id = self.creds.get("smoke_asset_id")

    def test_os_upgrade_dry_run(self):
        """Phase OS_UPGRADE_DRY — preflight only, no snapshot, no state change."""
        if not self.asset_id:
            pytest.skip("No smoke_asset_id in AWS creds")
        cr = _run_cr(
            self.client,
            "[smoke] os_upgrade dry_run",
            "execute_os_upgrade",
            {"dry_run": True, "target_version": ""},
            asset_ids=[self.asset_id],
        )
        result = _step_result(cr)
        assert result.get("status") == "dry_run", f"Expected dry_run status, got: {result}"
        assert "current_os" in result or "target_os" in result, (
            f"Expected OS info in dry_run result, got: {result}"
        )
        log(f"{PHASE}: DRY_RUN passed — current_os={result.get('current_os')}")

    def test_os_upgrade_snapshot_and_rollback(self):
        """Phase OS_UPGRADE_ROLLBACK — snapshot_only CR, write sentinel, rollback, verify sentinel gone."""
        if not self.instance_id or not self.asset_id:
            pytest.skip("No smoke_instance_id or smoke_asset_id in AWS creds — cannot run rollback smoke")

        # Step 1: Execute with snapshot_only=True — takes snapshot, returns immediately
        cr = _run_cr(
            self.client,
            "[smoke] os_upgrade snapshot_only",
            "execute_os_upgrade",
            {"snapshot_only": True},
            asset_ids=[self.asset_id],
        )
        cr_id = cr["id"]
        result = _step_result(cr)
        assert result.get("status") == "snapshot_only", f"Expected snapshot_only status, got: {result}"
        assert result.get("snapshot_id"), f"Expected snapshot_id in result, got: {result}"
        assert result.get("snapshot_meta", {}).get("instance_id"), (
            f"Expected snapshot_meta.instance_id in result, got: {result}"
        )
        log(f"{PHASE}: snapshot taken — snap={result['snapshot_id']}")

        # Step 2: Write sentinel file via SSM (post-snapshot — should disappear after rollback)
        sentinel_value = f"nexplane-smoke-sentinel-{cr_id[:8]}"
        _ssm_run(
            self.instance_id,
            f"echo {sentinel_value} > /tmp/nexplane-smoke-sentinel",
            self.creds,
        )
        verify = _ssm_run(self.instance_id, "cat /tmp/nexplane-smoke-sentinel", self.creds)
        assert sentinel_value in verify, f"Sentinel write failed: {verify}"
        log(f"{PHASE}: sentinel written post-snapshot")

        # Step 3: Rollback — triggers full EBS volume swap, instance restarts
        cr = _rollback_cr(self.client, cr_id, "rollback os_upgrade snapshot", timeout=TIMEOUT)
        rb_result = _step_result(cr, rollback=True)
        assert rb_result.get("rolled_back") is True, f"Expected rolled_back=True, got: {rb_result}"
        assert rb_result.get("agent_recovered") is True, (
            f"Expected agent_recovered=True after EBS restore, got: {rb_result}"
        )
        log(f"{PHASE}: rollback completed — new_vol={rb_result.get('new_volume_id')}")

        # Step 4: Verify sentinel gone — volume was restored from pre-sentinel snapshot
        sentinel_after = _ssm_run(
            self.instance_id,
            "cat /tmp/nexplane-smoke-sentinel 2>/dev/null || echo MISSING",
            self.creds,
        )
        assert sentinel_value not in sentinel_after, (
            f"Sentinel still present after rollback — EBS restore did not work: {sentinel_after!r}"
        )
        log(f"{PHASE}: SNAPSHOT_AND_ROLLBACK passed — sentinel gone after EBS restore")
