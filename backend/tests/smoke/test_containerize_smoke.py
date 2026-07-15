# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import os
import time
import urllib.request
import pytest
import sys

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

PHASE = "CONTAINERIZE"
TIMEOUT = 300
DUMMY_SERVICE = "nexplane-smoke-dummy"


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


def _ssm_run(instance_id, command, aws_creds, timeout=60):
    """Run a shell command on an EC2 instance via SSM and return stdout."""
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


class TestContainerizeSmoke:
    """
    Self-provisioning smoke suite for the containerize CR family.

    setup_class spins up a fresh t3.small EC2 via ec2_launch CR, deploys the
    Nexplane agent via deploy_nexplane_agent CR, installs a dummy systemd service
    (nexplane-smoke-dummy) so the retire/SSH-inplace phases have something to
    containerize, then stores the agent asset ID for all tests to use.

    teardown_class terminates the EC2 by rolling back the ec2_launch CR.
    """

    # Class-level state populated by setup_class
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

        # AWS creds for SSM access during setup
        cls.aws_creds = get_connector_creds_from_db("aws")
        if not cls.aws_creds:
            pytest.skip("No AWS connector in platform DB — cannot provision EC2")

        # Cloud account asset
        try:
            cloud_account_id = cls.client.get_cloud_account_asset_id()
        except Exception as e:
            pytest.skip(f"No cloud_account asset found: {e}")

        # Agent secret (regenerates; valid for the session)
        try:
            agent_secret = cls.client.get_agent_secret()
        except Exception as e:
            pytest.skip(f"Cannot generate agent secret: {e}")

        # Launch EC2 instance via ec2_launch CR
        log("CONTAINERIZE setup: launching EC2")
        launch_cr = _run_cr(
            cls.client,
            "containerize-smoke — provision EC2",
            "ec2_launch",
            {
                "mode": "quick",
                "name": "nexplane-smoke-containerize",
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
            "/assets", params={"q": "nexplane-smoke-containerize", "asset_type": "server"}
        )
        assert assets, "Server asset not found after ec2_launch"
        ec2_asset = assets[0]
        ec2_asset_id = ec2_asset["id"]
        meta = ec2_asset.get("asset_metadata") or {}
        cls.instance_id = meta.get("instance_id")
        private_ip = meta.get("private_ip", "")
        ec2_hostname = f"ip-{private_ip.replace('.', '-')}.ec2.internal" if private_ip else ""
        assert cls.instance_id, f"instance_id missing from asset metadata: {ec2_asset}"
        log(f"CONTAINERIZE setup: EC2 {cls.instance_id} launched, waiting 180s for SSM")
        time.sleep(180)

        # Deploy Nexplane agent via CR
        _run_cr(
            cls.client,
            "containerize-smoke — deploy agent",
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
        log("CONTAINERIZE setup: waiting for agent registration")
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
        log(f"CONTAINERIZE setup: agent registered as {agent_asset_id}")

        # Install dummy systemd service for retire/SSH-inplace phases
        _ssm_run(
            cls.instance_id,
            (
                "printf '[Unit]\\nDescription=Nexplane Smoke Dummy\\n"
                "[Service]\\nExecStart=/bin/sleep infinity\\n"
                "[Install]\\nWantedBy=multi-user.target\\n' "
                f"| sudo tee /etc/systemd/system/{DUMMY_SERVICE}.service > /dev/null && "
                "sudo systemctl daemon-reload && "
                f"sudo systemctl enable --now {DUMMY_SERVICE}.service"
            ),
            cls.aws_creds,
            timeout=60,
        )
        log(f"CONTAINERIZE setup: dummy service {DUMMY_SERVICE} installed")

    @classmethod
    def teardown_class(cls):
        if cls.launch_cr_id and cls.client:
            try:
                _rollback_cr(cls.client, cls.launch_cr_id, "teardown EC2 terminate", timeout=300)
                log("CONTAINERIZE teardown: EC2 terminated")
            except Exception as e:
                log(f"CONTAINERIZE teardown: rollback failed (manual cleanup needed): {e}")

    def test_containerize_build_dry_run(self):
        """CONTAINERIZE_BUILD_DRY — generate Dockerfile + manifest, no docker build."""
        cr = _run_cr(
            self.client,
            "[smoke] containerize_build dry_run",
            "agent_containerize_build",
            {"app_name": "nexplane-smoke-app", "registry": "nexplane-local", "dry_run": True},
            asset_ids=[self.agent_asset_id],
        )
        result = _step_result(cr)
        assert result.get("dockerfile") or result.get("dry_run") is True or result.get("manifests"), (
            f"Expected dry_run artifacts in result, got: {result}"
        )
        log(f"{PHASE}: BUILD_DRY passed")

    def test_containerize_build_rollback(self):
        """CONTAINERIZE_BUILD_ROLLBACK — real build then rollback deletes image."""
        aws_creds = self.aws_creds or {}
        registry = aws_creds.get("ecr_registry") or aws_creds.get("registry")
        if not registry:
            pytest.skip("No ECR registry in AWS creds (ecr_registry field) — skipping live build")
        cr = _run_cr(
            self.client,
            "[smoke] containerize_build live",
            "agent_containerize_build",
            {"app_name": "nexplane-smoke-app", "registry": registry, "dry_run": False},
            asset_ids=[self.agent_asset_id],
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
        """CONTAINERIZE_RETIRE — stop dummy service, rollback restarts it."""
        cr = _run_cr(
            self.client,
            "[smoke] containerize_retire dummy service",
            "agent_containerize_retire",
            {"systemd_unit": DUMMY_SERVICE},
            asset_ids=[self.agent_asset_id],
        )
        cr_id = cr["id"]
        result = _step_result(cr)
        assert result.get("retired") is True or result.get("stopped") is True, (
            f"Expected retired/stopped=True, got: {result}"
        )
        log(f"{PHASE}: RETIRE executed unit={DUMMY_SERVICE}")
        cr = _rollback_cr(self.client, cr_id, "rollback retire")
        rb_result = _step_result(cr, rollback=True)
        assert rb_result.get("rolled_back") is True, f"Expected rolled_back=True, got: {rb_result}"
        log(f"{PHASE}: RETIRE_ROLLBACK passed")

    def test_containerize_auto_dry_run(self):
        """CONTAINERIZE_AUTO_DRY — full 7-stage pipeline with dry_run=True."""
        cr = _run_cr(
            self.client,
            "[smoke] containerize_auto dry_run",
            "agent_containerize_auto",
            {"registry": "nexplane-local", "dry_run": True},
            asset_ids=[self.agent_asset_id],
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
        """CONTAINERIZE_SSH_INPLACE — adaptive SSH executor on provisioned host."""
        cr = _run_cr(
            self.client,
            "[smoke] ssh containerize_workload inplace",
            "ssh_containerize_workload",
            {"service_name": DUMMY_SERVICE, "registry": "nexplane-local"},
            connector_type="ssh",
            asset_ids=[self.agent_asset_id],
        )
        cr_id = cr["id"]
        result = _step_result(cr)
        assert result.get("containerized") is True, f"Expected containerized=True, got: {result}"
        log(f"{PHASE}: SSH path={result.get('path')}")
        cr = _rollback_cr(self.client, cr_id, "rollback ssh containerize")
        rb_result = _step_result(cr, rollback=True)
        assert rb_result.get("rolled_back") is True, f"Expected rolled_back=True, got: {rb_result}"
        log(f"{PHASE}: SSH_INPLACE_ROLLBACK passed")
