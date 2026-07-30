# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Live smoke test: linux_parallel_upgrade

Phases:
  1. smoke_resources fixture — provision source (Ubuntu 20.04) + dest (Ubuntu 22.04)
     from cached AMIs, allocate EIP, associate EIP to source, write test data on
     source, wait for both assets to register in platform.
  2. test_linux_parallel_upgrade_full_flow — full CR lifecycle:
     create -> plan -> submit-for-approval -> approve -> execute -> poll
     Assert: EIP on dest, source stopped, /var/lib/testapp/data.txt synced to dest.
  3. Rollback — POST /rollback, poll for rolled_back status.
     Assert: EIP back on source, source running again.
  4. Fixture teardown — disassociate/release EIP, terminate both instances.

AMI sources (SSM):
  /nexplane/smoke-amis/ubuntu-20-agent/latest  (source — Ubuntu 20.04 + Nexplane agent)
  /nexplane/smoke-amis/ubuntu-22-agent/latest  (dest   — Ubuntu 22.04 + Nexplane agent)

Environment variables:
  NEXPLANE_BASE_URL       default: http://localhost:8000
  NEXPLANE_EMAIL          default: admin@acme.example
  NEXPLANE_PASSWORD       default: admin123
  AWS_REGION              default: us-east-1
  NEXPLANE_SMOKE_SG       required — security group ID
  NEXPLANE_SMOKE_SUBNET   required — subnet ID

Run (from EC2 in-VPC):
  docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_linux_parallel_upgrade_smoke.py -v -s
"""

import os
import sys
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, get_connector_creds_from_db, log

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SMOKE_SG = os.environ.get("NEXPLANE_SMOKE_SG", "")
SMOKE_SUBNET = os.environ.get("NEXPLANE_SMOKE_SUBNET", "")

_SSM_SOURCE_AMI = "/nexplane/smoke-amis/ubuntu-20-agent/latest"
_SSM_DEST_AMI = "/nexplane/smoke-amis/ubuntu-22-agent/latest"

PROVISION_TIMEOUT = 600   # seconds to wait for instance running + asset registration
CR_TIMEOUT = 900          # seconds to wait for CR completion
ROLLBACK_TIMEOUT = 600    # seconds to wait for rollback completion
SSM_CMD_TIMEOUT = 90      # seconds for a single SSM RunCommand invocation

# ---------------------------------------------------------------------------
# Module-level client
# ---------------------------------------------------------------------------

_client: NexplaneClient = None


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


# ---------------------------------------------------------------------------
# AWS helpers (instance profile auth — no explicit credentials)
# ---------------------------------------------------------------------------

def _ec2():
    return boto3.client("ec2", region_name=AWS_REGION)


def _ssm_boto():
    return boto3.client("ssm", region_name=AWS_REGION)


def _get_ami(ssm_path: str) -> str:
    resp = _ssm_boto().get_parameter(Name=ssm_path)
    return resp["Parameter"]["Value"]


def _launch_instance(ami_id: str, name: str) -> str:
    """Launch a t3.micro EC2 instance and wait until running. Returns instance_id."""
    if not SMOKE_SG:
        pytest.skip("NEXPLANE_SMOKE_SG not set — cannot provision smoke instances")
    if not SMOKE_SUBNET:
        pytest.skip("NEXPLANE_SMOKE_SUBNET not set — cannot provision smoke instances")
    ec2 = _ec2()
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType="t3.micro",
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=[SMOKE_SG],
        SubnetId=SMOKE_SUBNET,
        IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "Name", "Value": name},
                {"Key": "nexplane-smoke", "Value": "true"},
            ],
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"LPU smoke: launched {instance_id} ({name}), waiting for running state")
    ec2.get_waiter("instance_running").wait(
        InstanceIds=[instance_id],
        WaiterConfig={"Delay": 15, "MaxAttempts": 40},
    )
    log(f"LPU smoke: {instance_id} running")
    return instance_id


def _get_instance_state(instance_id: str) -> str:
    resp = _ec2().describe_instances(InstanceIds=[instance_id])
    return resp["Reservations"][0]["Instances"][0]["State"]["Name"]


def _get_eip_instance(allocation_id: str) -> "str | None":
    """Return the InstanceId currently holding this EIP, or None."""
    addrs = _ec2().describe_addresses(AllocationIds=[allocation_id])["Addresses"]
    return addrs[0].get("InstanceId") if addrs else None


def _ssm_run(instance_id: str, command: str, timeout: int = SSM_CMD_TIMEOUT) -> str:
    """Send an SSM RunShellScript command and return stdout (blocks until done)."""
    ssm = _ssm_boto()
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
        if result["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            return result.get("StandardOutputContent", "").strip()
    raise TimeoutError(f"SSM command {cmd_id!r} on {instance_id} timed out after {timeout}s")


def _wait_for_ssm_ready(instance_id: str, timeout: int = 300):
    """Poll until SSM agent reports the instance as managed."""
    ssm = _ssm_boto()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            info = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            if info.get("InstanceInformationList"):
                log(f"LPU smoke: SSM ready on {instance_id}")
                return
        except Exception:
            pass
        time.sleep(15)
    raise TimeoutError(f"SSM not ready on {instance_id} after {timeout}s")


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def _wait_for_platform_asset(instance_id: str, timeout: int = PROVISION_TIMEOUT) -> str:
    """Poll platform API until an asset with the instance's private IP registers with an agent.
    Returns the platform asset id."""
    ec2 = _ec2()
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = resp["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    c = _get_client()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            assets = c.get("/assets", params={"asset_type": "server", "limit": 200})
            if not isinstance(assets, list):
                assets = assets.get("items", [])
            for asset in assets:
                meta = asset.get("asset_metadata") or {}
                ips = meta.get("ip_addresses") or []
                if private_ip in ips and meta.get("agent_version"):
                    log(f"LPU smoke: platform asset {asset['id']} registered for {instance_id} ({private_ip})")
                    return asset["id"]
        except Exception:
            pass
        time.sleep(15)
    raise TimeoutError(f"Platform asset for {instance_id} ({private_ip}) not registered within {timeout}s")


def _run_cr(
    change_type: str,
    desired_outcome: dict,
    title: str = "",
    asset_ids: "list[str] | None" = None,
    timeout: int = CR_TIMEOUT,
) -> dict:
    """Full CR lifecycle: create -> plan -> submit-for-approval -> approve -> execute.
    Polls until completed or failed. Returns the final CR dict."""
    c = _get_client()
    body = {
        "title": title or change_type,
        "change_type": change_type,
        "desired_outcome": {"_smoke_test": True, **desired_outcome},
    }
    if asset_ids:
        body["target_asset_ids"] = asset_ids

    resp = c.post("/change-requests", json=body)
    cr_id = resp["id"]
    log(f"LPU smoke: CR {cr_id} created ({change_type})")

    for step in ("plan", "submit-for-approval"):
        c.post(f"/change-requests/{cr_id}/{step}")

    c.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke"})
    c.post(f"/change-requests/{cr_id}/execute")
    log(f"LPU smoke: CR {cr_id} executing…")

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = c.get(f"/change-requests/{cr_id}")
        if cr["status"] == "completed":
            log(f"LPU smoke: CR {cr_id} completed")
            return cr
        if cr["status"] in ("failed", "rejected", "cancelled"):
            raise AssertionError(
                f"CR {cr_id} ended with status={cr['status']!r}: "
                f"{str(cr.get('execution_result', ''))[:500]}"
            )
        time.sleep(15)
    raise TimeoutError(f"CR {cr_id} did not complete within {timeout}s")


def _rollback_cr(cr_id: str, timeout: int = ROLLBACK_TIMEOUT) -> dict:
    """POST /rollback and poll until rolled_back. Returns final CR dict."""
    c = _get_client()
    c.post(f"/change-requests/{cr_id}/rollback")
    log(f"LPU smoke: rollback triggered for CR {cr_id}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = c.get(f"/change-requests/{cr_id}")
        if cr["status"] == "rolled_back":
            log(f"LPU smoke: CR {cr_id} rolled back")
            return cr
        if cr["status"] == "rollback_failed":
            raise AssertionError(f"CR {cr_id} rollback_failed: {cr.get('execution_result', '')}")
        time.sleep(15)
    raise TimeoutError(f"Rollback for CR {cr_id} did not complete within {timeout}s")


def _execution_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    if runs:
        steps = (runs[0].get("result") or {}).get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


# ---------------------------------------------------------------------------
# Smoke fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def smoke_resources():
    """Provision source + dest EC2 instances and an EIP.

    Source: Ubuntu 20.04 AMI with Nexplane agent (SSM: /nexplane/smoke-amis/ubuntu-20-agent/latest)
    Dest:   Ubuntu 22.04 AMI with Nexplane agent (SSM: /nexplane/smoke-amis/ubuntu-22-agent/latest)

    Yields a dict with instance IDs, platform asset IDs, and EIP allocation ID.
    Teardown releases EIP and terminates both instances.
    """
    # Resolve AMIs from SSM
    try:
        source_ami = _get_ami(_SSM_SOURCE_AMI)
        dest_ami = _get_ami(_SSM_DEST_AMI)
    except Exception as exc:
        pytest.skip(f"AMI SSM paths not populated — smoke infra not provisioned: {exc}")

    run_id = uuid.uuid4().hex[:8]
    source_name = f"smoke-lpu-source-{run_id}"
    dest_name = f"smoke-lpu-dest-{run_id}"

    source_instance_id = _launch_instance(source_ami, source_name)
    dest_instance_id = _launch_instance(dest_ami, dest_name)

    # Allocate EIP
    ec2 = _ec2()
    eip = ec2.allocate_address(
        Domain="vpc",
        TagSpecifications=[{
            "ResourceType": "elastic-ip",
            "Tags": [{"Key": "nexplane-smoke", "Value": "true"}],
        }],
    )
    eip_id = eip["AllocationId"]
    log(f"LPU smoke: EIP {eip_id} allocated")

    # Associate EIP to source
    ec2.associate_address(AllocationId=eip_id, InstanceId=source_instance_id)
    log(f"LPU smoke: EIP associated to source {source_instance_id}")

    # Wait for SSM readiness before sending commands
    _wait_for_ssm_ready(source_instance_id)
    _wait_for_ssm_ready(dest_instance_id)

    # Write test data on source
    _ssm_run(
        source_instance_id,
        "mkdir -p /var/lib/testapp && echo 'hello-from-source' > /var/lib/testapp/data.txt",
    )
    log(f"LPU smoke: test data written on source {source_instance_id}")

    # Start nc health-check listener on dest (port 8080)
    _ssm_run(
        dest_instance_id,
        "nohup nc -k -l 8080 </dev/null >/dev/null 2>&1 &",
    )
    log(f"LPU smoke: nc listener started on dest {dest_instance_id}")

    # Wait for platform asset registration (agents register by hostname, look up by private IP)
    source_asset_id = _wait_for_platform_asset(source_instance_id)
    dest_asset_id = _wait_for_platform_asset(dest_instance_id)

    # Patch assets with EC2 instance_id so executor can look up instances by asset ID
    c = _get_client()
    for asset_id, instance_id in [(source_asset_id, source_instance_id), (dest_asset_id, dest_instance_id)]:
        existing = c.get(f"/assets/{asset_id}")
        meta = existing.get("asset_metadata") or {}
        meta["instance_id"] = instance_id
        resp = c.client.patch(f"{c.base}/assets/{asset_id}", json={"asset_metadata": meta})
        resp.raise_for_status()

    resources = {
        "source_instance_id": source_instance_id,
        "dest_instance_id": dest_instance_id,
        "source_asset_id": source_asset_id,
        "dest_asset_id": dest_asset_id,
        "eip_id": eip_id,
    }

    yield resources

    # Teardown
    log("LPU smoke: teardown — releasing EIP and terminating instances")
    try:
        addrs = ec2.describe_addresses(AllocationIds=[eip_id])["Addresses"]
        assoc_id = addrs[0].get("AssociationId") if addrs else None
        if assoc_id:
            ec2.disassociate_address(AssociationId=assoc_id)
        ec2.release_address(AllocationId=eip_id)
    except Exception as exc:
        log(f"LPU smoke: EIP teardown warning: {exc}", ok=False)
    try:
        ec2.terminate_instances(InstanceIds=[source_instance_id, dest_instance_id])
        log(f"LPU smoke: termination requested for {source_instance_id}, {dest_instance_id}")
    except Exception as exc:
        log(f"LPU smoke: instance termination warning: {exc}", ok=False)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_linux_parallel_upgrade_full_flow(smoke_resources):
    """End-to-end smoke: provision -> sync -> EIP cutover -> assert -> rollback -> assert."""
    r = smoke_resources

    # -------------------------------------------------------------------------
    # Execute CR
    # -------------------------------------------------------------------------
    cr = _run_cr(
        "linux_parallel_upgrade",
        {
            "source_asset_id": r["source_asset_id"],
            "dest_asset_id": r["dest_asset_id"],
            "sync_paths": ["/var/lib/testapp"],
            "sync_exclude": [],
            "pre_sync_runs": 1,
            "health_check_ports": [8080],
            "cutover_method": "eip",
            "cutover_config": {"eip_allocation_id": r["eip_id"]},
            "decommission_after_hours": 0,
            "dry_run": False,
        },
        title="[smoke] linux_parallel_upgrade Ubuntu 20->22 EIP",
    )
    cr_id = cr["id"]
    exec_result = _execution_result(cr)

    assert cr["status"] == "completed", f"CR did not complete: status={cr['status']!r} result={exec_result}"
    assert exec_result.get("cutover_completed") is True, (
        f"cutover_completed not True in execution_result: {exec_result}"
    )
    assert exec_result.get("source_stopped") is True, (
        f"source_stopped not True in execution_result: {exec_result}"
    )

    # EIP must now be on dest
    eip_instance = _get_eip_instance(r["eip_id"])
    assert eip_instance == r["dest_instance_id"], (
        f"EIP should be on dest {r['dest_instance_id']}, got {eip_instance!r}"
    )
    log(f"LPU smoke: EIP on dest confirmed ({r['dest_instance_id']})")

    # Source must be stopped (nohup shutdown takes a few seconds to reach 'stopped')
    ec2 = _ec2()
    ec2.get_waiter("instance_stopped").wait(
        InstanceIds=[r["source_instance_id"]],
        WaiterConfig={"Delay": 5, "MaxAttempts": 24},
    )
    source_state = _get_instance_state(r["source_instance_id"])
    assert source_state == "stopped", (
        f"Source instance {r['source_instance_id']} expected stopped, got {source_state!r}"
    )
    log(f"LPU smoke: source stopped confirmed")

    # Synced data must exist on dest
    dest_content = _ssm_run(
        r["dest_instance_id"],
        "cat /var/lib/testapp/data.txt 2>/dev/null || echo MISSING",
    )
    assert "hello-from-source" in dest_content, (
        f"Expected synced data on dest, got: {dest_content!r}"
    )
    log(f"LPU smoke: data sync confirmed on dest")

    # -------------------------------------------------------------------------
    # Rollback
    # -------------------------------------------------------------------------
    cr = _rollback_cr(cr_id)

    # EIP must be back on source
    eip_instance = _get_eip_instance(r["eip_id"])
    assert eip_instance == r["source_instance_id"], (
        f"After rollback EIP should be on source {r['source_instance_id']}, got {eip_instance!r}"
    )
    log(f"LPU smoke: EIP back on source confirmed")

    # Source must be running again
    _ec2().get_waiter("instance_running").wait(
        InstanceIds=[r["source_instance_id"]],
        WaiterConfig={"Delay": 15, "MaxAttempts": 20},
    )
    source_state = _get_instance_state(r["source_instance_id"])
    assert source_state == "running", (
        f"Source {r['source_instance_id']} expected running after rollback, got {source_state!r}"
    )
    log(f"LPU smoke: source running after rollback confirmed — FULL FLOW PASSED")
