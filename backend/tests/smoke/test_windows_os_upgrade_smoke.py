# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: Windows OS In-Place Upgrade
Phase 1: dry_run preflight (no infra change, ~5-20 min depending on AMI cache)
Phase 2: full upgrade 2019->2022 + EBS rollback (~60-75 min)

Run from EC2:
  docker exec -e NEXPLANE_BACKEND_IP=172.31.1.233 nexplane-backend-1 python -m pytest \
    /app/tests/smoke/test_windows_os_upgrade_smoke.py -v -s --timeout=5400
"""

import os
import sys
import time
import pytest
import boto3

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

# AMI cache SSM key — Windows Server 2019 with nexplane agent pre-installed
_AMI_CACHE_KEY = "/nexplane/smoke-amis/windows-2019-with-agent/v1"
_INSTANCE_TYPE = "t3.medium"
_NEXPLANE_AGENT_SERVICE = "NexplaneAgent"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_aws_clients(creds: dict):
    region = creds.get("region", "us-east-1")
    kwargs = dict(
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=region,
    )
    ec2 = boto3.client("ec2", **kwargs)
    ssm = boto3.client("ssm", **kwargs)
    return ec2, ssm, region


def _ssm_run_ps(ssm, instance_id: str, ps_command: str, timeout: int = 120) -> str:
    """Run a PowerShell command via SSM on a Windows instance. Returns stdout."""
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunPowerShellScript",
        Parameters={"commands": [ps_command]},
    )
    cmd_id = resp["Command"]["CommandId"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(5)
        try:
            result = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if result["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            return result.get("StandardOutputContent", "").strip()
    raise TimeoutError(f"SSM PowerShell timed out after {timeout}s")


def get_or_create_windows_smoke_ami(ec2, ssm, creds: dict) -> str:
    """
    Return cached Windows 2019 + nexplane agent AMI from SSM, or provision and cache a new one.
    Cache key: /nexplane/smoke-amis/windows-2019-with-agent/v1
    """
    # Check cache
    try:
        param = ssm.get_parameter(Name=_AMI_CACHE_KEY)
        ami_id = param["Parameter"]["Value"]
        ec2.describe_images(ImageIds=[ami_id])
        log(f"[WINDOWS_SMOKE] Using cached AMI: {ami_id}")
        return ami_id
    except Exception:
        pass

    # Check for agent_install_url before attempting to provision
    agent_install_url = creds.get("agent_install_url", "")
    if not agent_install_url:
        pytest.skip(
            "agent_install_url not set in AWS creds and no cached AMI found — "
            "cannot build Windows smoke AMI; set agent_install_url in AWS connector creds or "
            f"pre-cache AMI at SSM key {_AMI_CACHE_KEY}"
        )

    log("[WINDOWS_SMOKE] No cached AMI — provisioning fresh Windows 2019 instance")

    # Find latest Windows Server 2019 Base AMI from AWS
    images = ec2.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": ["Windows_Server-2019-English-Full-Base-*"]},
            {"Name": "state", "Values": ["available"]},
        ],
    )["Images"]
    if not images:
        pytest.skip("No Windows Server 2019 Base AMI found in AWS — cannot provision smoke infra")

    base_ami = sorted(images, key=lambda x: x["CreationDate"], reverse=True)[0]["ImageId"]
    log(f"[WINDOWS_SMOKE] Base AMI: {base_ami}")

    # Launch instance
    subnet_id = creds.get("smoke_subnet_id") or creds.get("subnet_id")
    sg_id = creds.get("smoke_sg_id") or creds.get("security_group_id")
    key_name = creds.get("smoke_key_name") or creds.get("key_name")

    launch_kwargs = dict(
        ImageId=base_ami,
        InstanceType=_INSTANCE_TYPE,
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-windows-2019"},
            {"Key": "nexplane-purpose", "Value": "smoke-ami-build"},
        ]}],
    )
    if subnet_id:
        launch_kwargs["SubnetId"] = subnet_id
    if sg_id:
        launch_kwargs["SecurityGroupIds"] = [sg_id]
    if key_name:
        launch_kwargs["KeyName"] = key_name

    resp = ec2.run_instances(**launch_kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"[WINDOWS_SMOKE] Launched {instance_id} — waiting for running + SSM ready (~5 min)")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

    # Wait for SSM agent to be ready (Windows takes a few minutes)
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(30)
        resp = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )
        if resp.get("InstanceInformationList"):
            break
    else:
        pytest.fail(f"SSM agent never came online for {instance_id}")

    # Install nexplane agent via SSM
    log(f"[WINDOWS_SMOKE] Installing nexplane agent on {instance_id}")
    _ssm_run_ps(
        ssm,
        instance_id,
        f"Invoke-WebRequest -Uri '{agent_install_url}' -OutFile C:\\nexplane-agent-install.exe; "
        f"Start-Process -FilePath 'C:\\nexplane-agent-install.exe' -ArgumentList '/S' -Wait",
        timeout=300,
    )

    # Confirm agent service is running
    svc_status = _ssm_run_ps(
        ssm, instance_id,
        f"(Get-Service -Name {_NEXPLANE_AGENT_SERVICE} -ErrorAction SilentlyContinue).Status",
    )
    if "Running" not in svc_status:
        pytest.fail(f"NexplaneAgent service not running after install: {svc_status!r}")

    # Create AMI
    log(f"[WINDOWS_SMOKE] Creating AMI from {instance_id}")
    ami_resp = ec2.create_image(
        InstanceId=instance_id,
        Name=f"nexplane-smoke-windows-2019-agent-{int(time.time())}",
        Description="Windows Server 2019 + nexplane agent — smoke test base AMI",
        NoReboot=False,
    )
    new_ami_id = ami_resp["ImageId"]

    ec2.get_waiter("image_available").wait(
        ImageIds=[new_ami_id],
        WaiterConfig={"Delay": 30, "MaxAttempts": 40},
    )
    log(f"[WINDOWS_SMOKE] AMI ready: {new_ami_id}")

    # Cache in SSM
    ssm.put_parameter(Name=_AMI_CACHE_KEY, Value=new_ami_id, Type="String", Overwrite=True)

    # Terminate the build instance (we have the AMI)
    ec2.terminate_instances(InstanceIds=[instance_id])
    log(f"[WINDOWS_SMOKE] Build instance {instance_id} terminated")

    return new_ami_id


def _run_cr_full_lifecycle(client: NexplaneClient, label: str, action_id: str,
                            params: dict, asset_ids: list, timeout: int = 600) -> dict:
    """Create -> plan -> submit-for-approval -> approve -> execute. Returns CR dict."""
    base = client.base
    body = {
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {
            "connector_type": "nexplane_agent",
            "action_id": action_id,
            "params": params,
        },
        "asset_ids": asset_ids,
    }
    resp = client.client.post(f"{base}/change-requests", json=body)
    assert resp.status_code in (200, 201), f"[{label}] CR create failed {resp.status_code}: {resp.text}"
    cr_id = resp.json()["id"]
    log(f"[{label}] CR created: {cr_id}")

    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        assert r.status_code in (200, 201, 202, 204), f"[{label}] /{path} failed {r.status_code}: {r.text}"

    r = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "windows_os_upgrade smoke"},
    )
    assert r.status_code in (200, 201, 202, 204), f"[{label}] /approve failed {r.status_code}: {r.text}"

    r = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r.status_code in (200, 201, 202, 204), f"[{label}] /execute failed {r.status_code}: {r.text}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "completed":
            log(f"[{label}] CR {cr_id} completed")
            return cr
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(
                f"[{label}] CR {cr_id} status={status!r}: "
                f"{str(cr.get('execution_runs', ''))[:600]}"
            )
        time.sleep(30)
    raise TimeoutError(f"[{label}] CR {cr_id} timeout after {timeout}s")


def _rollback_cr(client: NexplaneClient, cr_id: str, label: str, timeout: int = 2400) -> dict:
    """POST /rollback and poll until rolled_back. Returns CR dict."""
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    assert r.status_code in (200, 201, 202, 204), f"[{label}] /rollback failed {r.status_code}: {r.text}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "rolled_back":
            log(f"[{label}] rollback complete")
            return cr
        if status in ("rollback_failed", "failed"):
            raise AssertionError(f"[{label}] rollback status={status!r}")
        time.sleep(30)
    raise TimeoutError(f"[{label}] rollback timeout after {timeout}s")


def _step_result(cr: dict, rollback: bool = False) -> dict:
    """Extract the executor result dict from the CR's execution runs."""
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


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestWindowsOsUpgradeSmoke:

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.creds = get_connector_creds_from_db("aws")
        if self.creds is None:
            pytest.skip("No AWS credentials in platform DB — skipping Windows OS upgrade smoke")
        if not self.creds.get("access_key_id"):
            pytest.skip("AWS creds missing access_key_id")

        ec2, ssm, region = _get_aws_clients(self.creds)
        self.ec2 = ec2
        self.ssm = ssm
        self.region = region

    # ------------------------------------------------------------------
    # Phase 1: Dry run — pre-flight only, no state change
    # ------------------------------------------------------------------

    def test_phase1_dry_run(self):
        """
        WINDOWS_OS_UPGRADE Phase 1: dry_run=True — pre-flight checks only.
        Provisions (or reuses cached) Windows 2019 instance, registers as asset,
        runs CR with dry_run=True, asserts preflight data returned without snapshot.
        ~10-20 minutes (first run) / ~5 minutes (cached AMI).
        """
        # Get or provision base AMI
        ami_id = get_or_create_windows_smoke_ami(self.ec2, self.ssm, self.creds)

        # Launch a fresh instance from the AMI
        subnet_id = self.creds.get("smoke_subnet_id") or self.creds.get("subnet_id")
        sg_id = self.creds.get("smoke_sg_id") or self.creds.get("security_group_id")

        launch_kwargs = dict(
            ImageId=ami_id,
            InstanceType=_INSTANCE_TYPE,
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[{"ResourceType": "instance", "Tags": [
                {"Key": "Name", "Value": "nexplane-smoke-windows-os-upgrade-dry-run"},
                {"Key": "nexplane-purpose", "Value": "smoke-test"},
            ]}],
        )
        if subnet_id:
            launch_kwargs["SubnetId"] = subnet_id
        if sg_id:
            launch_kwargs["SecurityGroupIds"] = [sg_id]

        resp = self.ec2.run_instances(**launch_kwargs)
        instance_id = resp["Instances"][0]["InstanceId"]
        log(f"[WINDOWS_SMOKE Phase1] Launched {instance_id}")

        try:
            self.ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

            # Wait for SSM
            deadline = time.time() + 300
            while time.time() < deadline:
                time.sleep(20)
                info = self.ssm.describe_instance_information(
                    Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
                )
                if info.get("InstanceInformationList"):
                    break
            else:
                pytest.fail(f"SSM never ready for {instance_id}")

            # Register as asset in platform
            base = self.client.base
            asset_resp = self.client.client.post(f"{base}/assets", json={
                "name": f"smoke-windows-dry-run-{instance_id}",
                "asset_type": "server",
                "asset_metadata": {
                    "instance_id": instance_id,
                    "environment": "dev",
                    "os": "windows",
                },
            })
            assert asset_resp.status_code in (200, 201), f"Asset create failed: {asset_resp.text}"
            asset_id = asset_resp.json()["id"]
            log(f"[WINDOWS_SMOKE Phase1] Asset registered: {asset_id}")

            # Run CR with dry_run=True
            cr = _run_cr_full_lifecycle(
                self.client,
                "[smoke] windows_os_upgrade dry_run",
                "windows_os_upgrade",
                {"target_version": "2022", "dry_run": True},
                asset_ids=[asset_id],
                timeout=600,
            )

            result = _step_result(cr)
            assert result.get("status") == "dry_run", (
                f"Expected status=dry_run, got: {result}"
            )
            assert result.get("snapshot_id") is None, (
                f"dry_run must not take snapshot, got snapshot_id={result.get('snapshot_id')}"
            )
            preflight = result.get("preflight") or {}
            assert preflight.get("disk_ok") is True, (
                f"disk_ok should be True on fresh instance: {preflight}"
            )
            assert preflight.get("dism_compat_passed") is True, (
                f"DISM compat should pass on fresh 2019 instance: {preflight}"
            )
            assert "2019" in preflight.get("current_os", ""), (
                f"Expected 2019 in current_os, got: {preflight.get('current_os')}"
            )
            log(f"[WINDOWS_SMOKE Phase1] PASS — dry_run preflight OK, current_os={preflight.get('current_os')}")

        finally:
            # Terminate instance after Phase 1 — don't leave Windows running (cost)
            log(f"[WINDOWS_SMOKE Phase1] Terminating {instance_id}")
            self.ec2.terminate_instances(InstanceIds=[instance_id])

    # ------------------------------------------------------------------
    # Phase 2: Full upgrade 2019->2022 + EBS rollback
    # ------------------------------------------------------------------

    def test_phase2_full_upgrade_and_rollback(self):
        """
        WINDOWS_OS_UPGRADE Phase 2: Full 2019->2022 in-place upgrade + EBS rollback.
        ~60-75 minutes total.

        1. Launch Windows 2019 instance from cached AMI
        2. Register asset, create CR with target_version=2022 + health_check_command
        3. Full CR lifecycle — polls for completion up to 90 min
        4. Assert post_upgrade_os contains "2022", agent_reconnected=True, health_check_passed=True
        5. Independently verify OS via SSM
        6. Rollback CR — EBS volume swap
        7. Assert rolled_back=True, agent_recovered=True
        8. Independently verify OS reverted to 2019 via SSM
        9. Terminate instance
        """
        ami_id = get_or_create_windows_smoke_ami(self.ec2, self.ssm, self.creds)

        subnet_id = self.creds.get("smoke_subnet_id") or self.creds.get("subnet_id")
        sg_id = self.creds.get("smoke_sg_id") or self.creds.get("security_group_id")

        launch_kwargs = dict(
            ImageId=ami_id,
            InstanceType=_INSTANCE_TYPE,
            MinCount=1,
            MaxCount=1,
            TagSpecifications=[{"ResourceType": "instance", "Tags": [
                {"Key": "Name", "Value": "nexplane-smoke-windows-os-upgrade-full"},
                {"Key": "nexplane-purpose", "Value": "smoke-test"},
            ]}],
        )
        if subnet_id:
            launch_kwargs["SubnetId"] = subnet_id
        if sg_id:
            launch_kwargs["SecurityGroupIds"] = [sg_id]

        resp = self.ec2.run_instances(**launch_kwargs)
        instance_id = resp["Instances"][0]["InstanceId"]
        log(f"[WINDOWS_SMOKE Phase2] Launched {instance_id}")

        try:
            self.ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

            deadline = time.time() + 300
            while time.time() < deadline:
                time.sleep(20)
                info = self.ssm.describe_instance_information(
                    Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
                )
                if info.get("InstanceInformationList"):
                    break
            else:
                pytest.fail(f"SSM never ready for {instance_id}")

            # Confirm OS is 2019 before upgrade
            current_os = _ssm_run_ps(
                self.ssm, instance_id,
                "(Get-WmiObject Win32_OperatingSystem).Caption",
            )
            assert "2019" in current_os, f"Expected 2019 pre-upgrade, got: {current_os!r}"
            log(f"[WINDOWS_SMOKE Phase2] Pre-upgrade OS confirmed: {current_os}")

            # Register asset
            base = self.client.base
            asset_resp = self.client.client.post(f"{base}/assets", json={
                "name": f"smoke-windows-full-{instance_id}",
                "asset_type": "server",
                "asset_metadata": {
                    "instance_id": instance_id,
                    "environment": "dev",
                    "os": "windows",
                },
            })
            assert asset_resp.status_code in (200, 201), f"Asset create failed: {asset_resp.text}"
            asset_id = asset_resp.json()["id"]
            log(f"[WINDOWS_SMOKE Phase2] Asset registered: {asset_id}")

            # Run full upgrade CR — 90-minute timeout (upgrade + restart + agent poll)
            cr = _run_cr_full_lifecycle(
                self.client,
                "[smoke] windows_os_upgrade 2019->2022",
                "windows_os_upgrade",
                {
                    "target_version": "2022",
                    "health_check_command": "(Get-WmiObject Win32_OperatingSystem).Caption",
                },
                asset_ids=[asset_id],
                timeout=5400,  # 90 minutes
            )
            cr_id = cr["id"]

            result = _step_result(cr)
            assert result.get("status") == "completed", (
                f"Expected status=completed, got: {result}"
            )
            assert result.get("agent_reconnected") is True, (
                f"Expected agent_reconnected=True, got: {result}"
            )
            assert "2022" in result.get("post_upgrade_os", ""), (
                f"Expected 2022 in post_upgrade_os, got: {result.get('post_upgrade_os')}"
            )
            assert result.get("health_check_passed") is True, (
                f"Expected health_check_passed=True, got: {result}"
            )
            assert result.get("snapshot_id"), "Expected snapshot_id in completed result"
            log(
                f"[WINDOWS_SMOKE Phase2] Upgrade PASSED — "
                f"post_upgrade_os={result.get('post_upgrade_os')}, "
                f"agent_reconnect_elapsed={result.get('agent_reconnect_elapsed_seconds')}s, "
                f"snapshot={result.get('snapshot_id')}"
            )

            # Independent OS verification via SSM
            post_os_ssm = _ssm_run_ps(
                self.ssm, instance_id,
                "(Get-WmiObject Win32_OperatingSystem).Caption",
                timeout=60,
            )
            assert "2022" in post_os_ssm, (
                f"SSM independent OS check failed — expected 2022, got: {post_os_ssm!r}"
            )
            log(f"[WINDOWS_SMOKE Phase2] SSM OS verify PASSED: {post_os_ssm}")

            # Rollback — EBS volume swap (up to 40 minutes)
            log(f"[WINDOWS_SMOKE Phase2] Initiating rollback for CR {cr_id}")
            cr = _rollback_cr(self.client, cr_id, "rollback windows_os_upgrade", timeout=2400)

            rb_result = _step_result(cr, rollback=True)
            assert rb_result.get("rolled_back") is True, (
                f"Expected rolled_back=True, got: {rb_result}"
            )
            assert rb_result.get("agent_recovered") is True, (
                f"Expected agent_recovered=True after EBS volume swap, got: {rb_result}"
            )
            log(
                f"[WINDOWS_SMOKE Phase2] Rollback PASSED — "
                f"new_volume={rb_result.get('new_volume_id')}, "
                f"old_volume={rb_result.get('old_volume_id')}"
            )

            # Independent OS verification post-rollback via SSM
            rolled_back_os = _ssm_run_ps(
                self.ssm, instance_id,
                "(Get-WmiObject Win32_OperatingSystem).Caption",
                timeout=60,
            )
            assert "2019" in rolled_back_os, (
                f"SSM OS check post-rollback failed — expected 2019, got: {rolled_back_os!r}"
            )
            log(f"[WINDOWS_SMOKE Phase2] Post-rollback SSM OS verify PASSED: {rolled_back_os}")
            log("[WINDOWS_SMOKE Phase2] FULL PHASE 2 PASSED — upgrade + rollback verified")

        finally:
            # Always terminate — Windows EC2 is expensive
            log(f"[WINDOWS_SMOKE Phase2] Terminating {instance_id}")
            try:
                self.ec2.terminate_instances(InstanceIds=[instance_id])
            except Exception as e:
                log(f"[WINDOWS_SMOKE Phase2] WARNING: terminate failed: {e}")
