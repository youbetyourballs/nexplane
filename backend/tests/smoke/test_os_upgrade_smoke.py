# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import os
import time
import urllib.request
import sys
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

PHASE = "OS_UPGRADE"
TIMEOUT = 600  # snapshot + volume swap can take several minutes


def _get_backend_private_ip() -> str:
    """Fetch this EC2 instance's private IP from IMDSv2."""
    token_req = urllib.request.Request(
        "http://169.254.169.254/latest/api/token",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        method="PUT",
    )
    with urllib.request.urlopen(token_req, timeout=5) as r:
        token = r.read().decode().strip()
    ip_req = urllib.request.Request(
        "http://169.254.169.254/latest/meta-data/local-ipv4",
        headers={"X-aws-ec2-metadata-token": token},
    )
    with urllib.request.urlopen(ip_req, timeout=5) as r:
        return r.read().decode().strip()


def _run_cr(client, label, action_id, params, connector_type="nexplane_agent",
            asset_ids=None, timeout=TIMEOUT):
    base = client.base
    body = {
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {
            "connector_type": connector_type,
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


def _ssm_run(instance_id, command, aws_creds, timeout=60):
    """Run a shell command on the instance via SSM and return stdout."""
    import boto3
    ssm = boto3.client(
        "ssm",
        aws_access_key_id=aws_creds["access_key_id"],
        aws_secret_access_key=aws_creds["secret_access_key"],
        region_name=aws_creds.get("region", "us-east-1"),
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
    """
    Self-provisioning smoke suite for OS upgrade.

    setup_class spins up a fresh t3.small EC2 via ec2_launch CR and deploys
    the Nexplane agent, then stores agent_asset_id and instance_id for tests.
    The dry_run phase only needs an agent. The snapshot+rollback phase performs
    a full EBS volume swap and uses SSM to verify the sentinel disappears.

    teardown_class terminates the EC2 by rolling back the ec2_launch CR.
    Note: after the EBS rollback test the instance has a NEW root volume; the
    old volume is tagged nexplane-rollback-orphan for manual cleanup.
    """

    client = None
    agent_asset_id = None
    instance_id = None
    launch_cr_id = None
    aws_creds = None

    @classmethod
    def setup_class(cls):
        cls.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)

        # Backend private IP — agent phones home to this URL
        try:
            backend_ip = _get_backend_private_ip()
        except Exception as e:
            pytest.skip(f"Cannot reach IMDS — not running on EC2: {e}")
        nexplane_url = f"http://{backend_ip}:8000"

        # AWS creds for boto3 (SSM sentinel writes)
        cls.aws_creds = get_connector_creds_from_db("aws")
        if not cls.aws_creds:
            pytest.skip("No AWS connector in platform DB — cannot provision EC2")

        # Cloud account asset
        try:
            cloud_account_id = cls.client.get_cloud_account_asset_id()
        except Exception as e:
            pytest.skip(f"No cloud_account asset found: {e}")

        # Agent secret
        try:
            agent_secret = cls.client.get_agent_secret()
        except Exception as e:
            pytest.skip(f"Cannot generate agent secret: {e}")

        # Launch EC2 via ec2_launch CR
        log("OS_UPGRADE setup: launching EC2")
        launch_cr = _run_cr(
            cls.client,
            "os-upgrade-smoke — provision EC2",
            "ec2_launch",
            {
                "mode": "quick",
                "name": "nexplane-smoke-os-upgrade",
                "os": "amazon_linux",
                "instance_type": "t3.small",
                "iam_instance_profile": "NexplaneEC2TestProfile",
                "rollback_strategy": "terminate_instance",
            },
            connector_type="aws",
            timeout=300,
        )
        cls.launch_cr_id = launch_cr["id"]

        # Find the auto-created server asset
        assets = cls.client.get(
            "/assets", params={"q": "nexplane-smoke-os-upgrade", "asset_type": "server"}
        )
        assert assets, "Server asset not found after ec2_launch"
        ec2_asset = assets[0]
        ec2_asset_id = ec2_asset["id"]
        meta = ec2_asset.get("asset_metadata") or {}
        cls.instance_id = meta.get("instance_id")
        private_ip = meta.get("private_ip", "")
        ec2_hostname = f"ip-{private_ip.replace('.', '-')}.ec2.internal" if private_ip else ""
        assert cls.instance_id, f"instance_id missing from asset metadata: {ec2_asset}"
        log(f"OS_UPGRADE setup: EC2 {cls.instance_id} launched, waiting 180s for SSM")
        time.sleep(180)

        # Deploy Nexplane agent via CR
        _run_cr(
            cls.client,
            "os-upgrade-smoke — deploy agent",
            "deploy_nexplane_agent",
            {
                "instance_id": cls.instance_id,
                "nexplane_url": nexplane_url,
                "nexplane_secret": agent_secret,
            },
            connector_type="nexplane_agent",
            asset_ids=[ec2_asset_id],
            timeout=300,
        )

        # Poll for agent asset registration (up to 180s)
        log("OS_UPGRADE setup: waiting for agent registration")
        deadline = time.time() + 180
        agent_asset_id = None
        while time.time() < deadline:
            candidates = [
                a for a in cls.client.get(
                    "/assets", params={"q": ec2_hostname or "nexplane-smoke", "asset_type": "server"}
                )
                if (a.get("asset_metadata") or {}).get("agent_version")
            ]
            if candidates:
                agent_asset_id = candidates[0]["id"]
                break
            time.sleep(15)
        assert agent_asset_id, (
            f"Agent did not register within 180s on {cls.instance_id} (hostname={ec2_hostname})"
        )
        cls.agent_asset_id = agent_asset_id
        log(f"OS_UPGRADE setup: agent registered as {agent_asset_id}")

    @classmethod
    def teardown_class(cls):
        if cls.launch_cr_id and cls.client:
            try:
                _rollback_cr(cls.client, cls.launch_cr_id, "teardown EC2 terminate", timeout=300)
                log("OS_UPGRADE teardown: EC2 terminated")
            except Exception as e:
                log(f"OS_UPGRADE teardown: rollback failed (manual cleanup needed): {e}")

    def test_os_upgrade_dry_run(self):
        """Phase OS_UPGRADE_DRY — preflight only, no snapshot, no state change."""
        cr = _run_cr(
            self.client,
            "[smoke] os_upgrade dry_run",
            "execute_os_upgrade",
            {"dry_run": True, "target_version": ""},
            asset_ids=[self.agent_asset_id],
        )
        result = _step_result(cr)
        assert result.get("status") == "dry_run", f"Expected dry_run status, got: {result}"
        assert "current_os" in result or "target_os" in result, (
            f"Expected OS info in dry_run result, got: {result}"
        )
        log(f"{PHASE}: DRY_RUN passed — current_os={result.get('current_os')}")

    def test_os_upgrade_snapshot_and_rollback(self):
        """Phase OS_UPGRADE_ROLLBACK — snapshot_only CR, write sentinel, rollback, verify sentinel gone."""
        # Step 1: Take snapshot via snapshot_only=True (skips preflight and upgrade)
        cr = _run_cr(
            self.client,
            "[smoke] os_upgrade snapshot_only",
            "execute_os_upgrade",
            {"snapshot_only": True},
            asset_ids=[self.agent_asset_id],
        )
        cr_id = cr["id"]
        result = _step_result(cr)
        assert result.get("status") == "snapshot_only", f"Expected snapshot_only status, got: {result}"
        assert result.get("snapshot_id"), f"Expected snapshot_id in result, got: {result}"
        assert result.get("snapshot_meta", {}).get("instance_id"), (
            f"Expected snapshot_meta.instance_id in result, got: {result}"
        )
        log(f"{PHASE}: snapshot taken — snap={result['snapshot_id']}")

        # Step 2: Write sentinel file post-snapshot (via SSM — will disappear after EBS restore)
        sentinel_value = f"nexplane-smoke-sentinel-{cr_id[:8]}"
        _ssm_run(
            self.instance_id,
            f"echo {sentinel_value} > /tmp/nexplane-smoke-sentinel",
            self.aws_creds,
        )
        verify = _ssm_run(self.instance_id, "cat /tmp/nexplane-smoke-sentinel", self.aws_creds)
        assert sentinel_value in verify, f"Sentinel write failed: {verify}"
        log(f"{PHASE}: sentinel written post-snapshot")

        # Step 3: Rollback — full EBS volume swap (stop → create-from-snap → detach → attach → start)
        cr = _rollback_cr(self.client, cr_id, "rollback os_upgrade snapshot", timeout=TIMEOUT)
        rb_result = _step_result(cr, rollback=True)
        assert rb_result.get("rolled_back") is True, f"Expected rolled_back=True, got: {rb_result}"
        assert rb_result.get("agent_recovered") is True, (
            f"Expected agent_recovered=True after EBS restore, got: {rb_result}"
        )
        log(f"{PHASE}: rollback completed — new_vol={rb_result.get('new_volume_id')}")

        # Step 4: Verify sentinel gone — EBS was restored from pre-sentinel snapshot
        sentinel_after = _ssm_run(
            self.instance_id,
            "cat /tmp/nexplane-smoke-sentinel 2>/dev/null || echo MISSING",
            self.aws_creds,
        )
        assert sentinel_value not in sentinel_after, (
            f"Sentinel still present after rollback — EBS restore did not work: {sentinel_after!r}"
        )
        log(f"{PHASE}: SNAPSHOT_AND_ROLLBACK passed — sentinel gone after EBS restore")
