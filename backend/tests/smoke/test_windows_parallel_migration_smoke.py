# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Live smoke test: windows_parallel_migration

Two parametrized runs:
  - 2016→2019: source=Windows Server 2016, dest=Windows Server 2019
  - 2019→2022: source=Windows Server 2019, dest=Windows Server 2022

Each run:
  1. Provision source + dest from cached AMIs (or bootstrap with agent install)
  2. Set up test IIS site, scheduled task, config file with source hostname on source (via SSM)
  3. Register both assets in platform, patch with instance_id
  4. Create CR with cutover_method=eip, run full lifecycle
  5. Assert: EIP on dest, source stopped, config file hostname replaced on dest, IIS site present
  6. Rollback: assert EIP back on source, source running
  7. Teardown

AMI SSM paths:
  /nexplane/smoke-amis/windows-2016-agent/latest
  /nexplane/smoke-amis/windows-2019-agent/latest
  /nexplane/smoke-amis/windows-2022-agent/latest

Run (from EC2):
  docker exec nexplane-backend-1 python -m pytest \\
    /app/tests/smoke/test_windows_parallel_migration_smoke.py -v -s --timeout=7200
"""

import os
import sys
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, get_connector_creds_from_db, log

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SMOKE_SG = os.environ.get("NEXPLANE_SMOKE_SG", "")
SMOKE_SUBNET = os.environ.get("NEXPLANE_SMOKE_SUBNET", "")

_PLATFORM_PRIVATE_IP = os.environ.get("NEXPLANE_BACKEND_IP", "172.31.1.233")

_SSM_AMI = {
    "2016": "/nexplane/smoke-amis/windows-2016-agent/latest",
    "2019": "/nexplane/smoke-amis/windows-2019-agent/latest",
    "2022": "/nexplane/smoke-amis/windows-2022-agent/latest",
}

PROVISION_TIMEOUT = 900
CR_TIMEOUT = 3600
ROLLBACK_TIMEOUT = 900

_client: NexplaneClient = None


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _ec2():
    return boto3.client("ec2", region_name=AWS_REGION)


def _ssm_boto():
    return boto3.client("ssm", region_name=AWS_REGION)


def _get_ami(ssm_path: str) -> str | None:
    try:
        resp = _ssm_boto().get_parameter(Name=ssm_path)
        return resp["Parameter"]["Value"]
    except Exception:
        return None


def _ssm_run_ps(instance_id: str, ps_command: str, timeout: int = 120) -> str:
    """Run a PowerShell command via SSM on a Windows instance."""
    ssm = _ssm_boto()
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


def _wait_for_ssm_ready(instance_id: str, timeout: int = 600):
    ssm = _ssm_boto()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            info = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            if info.get("InstanceInformationList"):
                log(f"WPM smoke: SSM ready on {instance_id}")
                return
        except Exception:
            pass
        time.sleep(20)
    raise TimeoutError(f"SSM not ready on {instance_id} after {timeout}s")


def _get_agent_secret() -> str:
    import requests as _req
    s = _req.Session()
    r = s.post(f"{BASE_URL}/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.ok, f"login failed: {r.text}"
    token = r.json()["access_token"]
    s.headers.update({"Authorization": f"Bearer {token}"})
    r2 = s.get(f"{BASE_URL}/settings/agent-secret")
    assert r2.ok, f"agent-secret endpoint failed: {r2.text}"
    secret = r2.json().get("agent_secret_plaintext") or r2.json().get("agent_secret") or r2.json().get("secret")
    assert secret, f"Could not retrieve agent secret: {r2.json()}"
    return secret


def _install_nexplane_agent(instance_id: str):
    """Install Nexplane agent via SSM on a fresh Windows instance."""
    from test_windows_os_upgrade_smoke import _install_nexplane_agent_via_ssm, _get_aws_clients
    creds = get_connector_creds_from_db("aws")
    ec2, ssm, region = _get_aws_clients(creds)
    agent_secret = _get_agent_secret()
    _install_nexplane_agent_via_ssm(ssm, ec2, instance_id, creds, agent_secret)


def _launch_windows_instance(ami_id: str, name: str) -> str:
    if not SMOKE_SG:
        pytest.skip("NEXPLANE_SMOKE_SG not set")
    if not SMOKE_SUBNET:
        pytest.skip("NEXPLANE_SMOKE_SUBNET not set")
    ec2 = _ec2()
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType="t3.medium",
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=[SMOKE_SG],
        SubnetId=SMOKE_SUBNET,
        IamInstanceProfile={"Name": "nexplane-smoke-ssm"},
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "Name", "Value": name},
                {"Key": "nexplane-smoke", "Value": "true"},
            ],
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"WPM smoke: launched {instance_id} ({name}), waiting for running state")
    ec2.get_waiter("instance_running").wait(
        InstanceIds=[instance_id],
        WaiterConfig={"Delay": 15, "MaxAttempts": 60},
    )
    log(f"WPM smoke: {instance_id} running")
    return instance_id


def _get_instance_state(instance_id: str) -> str:
    resp = _ec2().describe_instances(InstanceIds=[instance_id])
    return resp["Reservations"][0]["Instances"][0]["State"]["Name"]


def _get_eip_instance(allocation_id: str) -> "str | None":
    addrs = _ec2().describe_addresses(AllocationIds=[allocation_id])["Addresses"]
    return addrs[0].get("InstanceId") if addrs else None


def _wait_for_platform_asset(instance_id: str, timeout: int = PROVISION_TIMEOUT) -> str:
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
                    log(f"WPM smoke: platform asset {asset['id']} registered ({instance_id} / {private_ip})")
                    return asset["id"]
        except Exception:
            pass
        time.sleep(20)
    raise TimeoutError(f"Platform asset for {instance_id} not registered within {timeout}s")


def _run_cr(change_type: str, desired_outcome: dict, title: str = "", asset_ids=None, timeout: int = CR_TIMEOUT) -> dict:
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
    log(f"WPM smoke: CR {cr_id} created ({change_type})")

    for step in ("plan", "submit-for-approval"):
        c.post(f"/change-requests/{cr_id}/{step}")

    c.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke"})
    c.post(f"/change-requests/{cr_id}/execute")
    log(f"WPM smoke: CR {cr_id} executing…")

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = c.get(f"/change-requests/{cr_id}")
        if cr["status"] == "completed":
            log(f"WPM smoke: CR {cr_id} completed")
            return cr
        if cr["status"] in ("failed", "rejected", "cancelled"):
            raise AssertionError(
                f"CR {cr_id} ended with status={cr['status']!r}: "
                f"{str(cr.get('execution_result', ''))[:500]}"
            )
        time.sleep(20)
    raise TimeoutError(f"CR {cr_id} did not complete within {timeout}s")


def _rollback_cr(cr_id: str, timeout: int = ROLLBACK_TIMEOUT) -> dict:
    c = _get_client()
    c.post(f"/change-requests/{cr_id}/rollback")
    log(f"WPM smoke: rollback triggered for CR {cr_id}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = c.get(f"/change-requests/{cr_id}")
        if cr["status"] == "rolled_back":
            log(f"WPM smoke: CR {cr_id} rolled back")
            return cr
        if cr["status"] == "rollback_failed":
            raise AssertionError(f"CR {cr_id} rollback_failed: {cr.get('execution_result', '')}")
        time.sleep(20)
    raise TimeoutError(f"CR {cr_id} rollback did not complete within {timeout}s")


def _execution_result(cr: dict) -> dict:
    er = cr.get("execution_result") or {}
    if isinstance(er, str):
        import json as _json
        try:
            er = _json.loads(er)
        except Exception:
            er = {}
    return er


# ---------------------------------------------------------------------------
# Parametrized fixture
# ---------------------------------------------------------------------------

@pytest.fixture(
    params=[
        {"source_version": "2016", "dest_version": "2019"},
        {"source_version": "2019", "dest_version": "2022"},
    ],
    ids=["2016_to_2019", "2019_to_2022"],
    scope="function",
)
def smoke_resources(request):
    src_ver = request.param["source_version"]
    dst_ver = request.param["dest_version"]
    run_id = str(uuid.uuid4())[:8]

    source_ami = _get_ami(_SSM_AMI[src_ver])
    dest_ami = _get_ami(_SSM_AMI[dst_ver])

    if not source_ami:
        # Fall back to base Windows AMI — will need agent install (slow)
        # Use public Windows Server AMIs (us-east-1 base AMIs)
        _BASE_AMIS = {
            "2016": "ami-0c2b0d3fb02824d92",  # Windows_Server-2016-English-Full-Base
            "2019": "ami-07cc1bbe145f35b58",  # Windows_Server-2019-English-Full-Base
            "2022": "ami-0f496107db66676ff",  # Windows_Server-2022-English-Full-Base
        }
        source_ami = _BASE_AMIS[src_ver]
        log(f"WPM smoke: no cached AMI for 2016 source, using base AMI {source_ami}")

    if not dest_ami:
        _BASE_AMIS = {
            "2019": "ami-07cc1bbe145f35b58",
            "2022": "ami-0f496107db66676ff",
        }
        dest_ami = _BASE_AMIS[dst_ver]
        log(f"WPM smoke: no cached AMI for {dst_ver} dest, using base AMI {dest_ami}")

    ec2 = _ec2()

    source_instance_id = _launch_windows_instance(source_ami, f"smoke-wpm-src-{src_ver}-{run_id}")
    dest_instance_id = _launch_windows_instance(dest_ami, f"smoke-wpm-dst-{dst_ver}-{run_id}")

    # Wait for SSM agent readiness (Windows takes longer to boot)
    _wait_for_ssm_ready(source_instance_id, timeout=600)
    _wait_for_ssm_ready(dest_instance_id, timeout=600)

    # Install Nexplane agent if not pre-baked
    source_has_agent = bool(_get_ami(_SSM_AMI[src_ver]))
    dest_has_agent = bool(_get_ami(_SSM_AMI[dst_ver]))

    if not source_has_agent:
        log(f"WPM smoke: installing Nexplane agent on source {source_instance_id}")
        _install_nexplane_agent(source_instance_id)
    if not dest_has_agent:
        log(f"WPM smoke: installing Nexplane agent on dest {dest_instance_id}")
        _install_nexplane_agent(dest_instance_id)

    # Allocate and associate EIP to source
    eip = ec2.allocate_address(Domain="vpc")
    eip_id = eip["AllocationId"]
    log(f"WPM smoke: EIP {eip_id} allocated")
    ec2.associate_address(AllocationId=eip_id, InstanceId=source_instance_id)
    log(f"WPM smoke: EIP associated to source {source_instance_id}")

    # Get source hostname
    source_hostname = _ssm_run_ps(source_instance_id, "hostname").strip()
    log(f"WPM smoke: source hostname={source_hostname!r}")

    # Set up test data on source:
    # 1. IIS site (requires IIS installed)
    _ssm_run_ps(
        source_instance_id,
        "Install-WindowsFeature -Name Web-Server -IncludeManagementTools -ErrorAction SilentlyContinue | Out-Null",
        timeout=300,
    )
    _ssm_run_ps(
        source_instance_id,
        f"New-WebSite -Name 'TestApp' -Port 8080 -PhysicalPath 'C:\\testapp' -Force -ErrorAction SilentlyContinue | Out-Null; "
        f"New-Item -Path 'C:\\testapp' -ItemType Directory -Force | Out-Null",
        timeout=60,
    )
    log(f"WPM smoke: IIS TestApp site created on source")

    # 2. Scheduled task
    _ssm_run_ps(
        source_instance_id,
        "Register-ScheduledTask -TaskName 'NexplaneSmokeTask' -Action (New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c echo smoke') "
        "-Trigger (New-ScheduledTaskTrigger -Daily -At '3am') -RunLevel Highest -Force | Out-Null",
        timeout=30,
    )
    log(f"WPM smoke: scheduled task registered on source")

    # 3. Config file with source hostname hardcoded
    _ssm_run_ps(
        source_instance_id,
        f"New-Item -Path 'C:\\testapp' -ItemType Directory -Force | Out-Null; "
        f"Set-Content -Path 'C:\\testapp\\app.config' -Value 'server={source_hostname} port=8080'",
        timeout=30,
    )
    log(f"WPM smoke: config file with hostname {source_hostname!r} written on source")

    # Wait for platform asset registration
    source_asset_id = _wait_for_platform_asset(source_instance_id)
    dest_asset_id = _wait_for_platform_asset(dest_instance_id)

    # Patch assets with instance_id metadata
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
        "source_hostname": source_hostname,
        "src_ver": src_ver,
        "dst_ver": dst_ver,
    }

    yield resources

    # Teardown
    log("WPM smoke: teardown — releasing EIP and terminating instances")
    try:
        addrs = ec2.describe_addresses(AllocationIds=[eip_id])["Addresses"]
        assoc_id = addrs[0].get("AssociationId") if addrs else None
        if assoc_id:
            ec2.disassociate_address(AssociationId=assoc_id)
        ec2.release_address(AllocationId=eip_id)
    except Exception as exc:
        log(f"WPM smoke: EIP teardown warning: {exc}", ok=False)
    try:
        ec2.terminate_instances(InstanceIds=[source_instance_id, dest_instance_id])
        log(f"WPM smoke: terminated {source_instance_id}, {dest_instance_id}")
    except Exception as exc:
        log(f"WPM smoke: instance termination warning: {exc}", ok=False)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_windows_parallel_migration_full_flow(smoke_resources):
    """End-to-end smoke: inventory → sync → EIP cutover → assert → rollback → assert."""
    r = smoke_resources
    log(f"WPM smoke: testing {r['src_ver']}→{r['dst_ver']}")

    # New hostname for dest (used in replacement)
    dest_hostname = _ssm_run_ps(r["dest_instance_id"], "hostname").strip()
    log(f"WPM smoke: dest hostname={dest_hostname!r}")

    # -------------------------------------------------------------------------
    # Execute CR
    # -------------------------------------------------------------------------
    cr = _run_cr(
        "windows_parallel_migration",
        {
            "source_asset_id": r["source_asset_id"],
            "dest_asset_id": r["dest_asset_id"],
            "cutover_method": "eip",
            "cutover_config": {"eip_allocation_id": r["eip_id"]},
            "hostname_replacements": [
                {
                    "location": "file",
                    "path": "C:\\testapp\\app.config",
                    "old": r["source_hostname"],
                    "new": dest_hostname,
                }
            ],
            "decommission_after_hours": 0,
            "dry_run": False,
        },
        title=f"[smoke] windows_parallel_migration {r['src_ver']}→{r['dst_ver']} EIP",
        asset_ids=[r["source_asset_id"], r["dest_asset_id"]],
    )
    cr_id = cr["id"]
    exec_result = _execution_result(cr)

    assert cr["status"] == "completed", (
        f"CR did not complete: status={cr['status']!r} result={exec_result}"
    )
    assert exec_result.get("cutover_completed") is True, (
        f"cutover_completed not True: {exec_result}"
    )
    assert exec_result.get("source_stopped") is True, (
        f"source_stopped not True: {exec_result}"
    )

    # Inventory must have captured hostname ref in config file
    hostname_refs = exec_result.get("hostname_refs", [])
    assert any(ref.get("location") == "file" for ref in hostname_refs), (
        f"Expected file hostname_ref in execution_result, got: {hostname_refs}"
    )
    log(f"WPM smoke: hostname_refs captured: {hostname_refs}")

    # EIP must now be on dest
    eip_instance = _get_eip_instance(r["eip_id"])
    assert eip_instance == r["dest_instance_id"], (
        f"EIP should be on dest {r['dest_instance_id']}, got {eip_instance!r}"
    )
    log(f"WPM smoke: EIP on dest confirmed ({r['dest_instance_id']})")

    # Source must be stopped
    ec2 = _ec2()
    ec2.get_waiter("instance_stopped").wait(
        InstanceIds=[r["source_instance_id"]],
        WaiterConfig={"Delay": 10, "MaxAttempts": 18},
    )
    source_state = _get_instance_state(r["source_instance_id"])
    assert source_state == "stopped", (
        f"Source {r['source_instance_id']} expected stopped, got {source_state!r}"
    )
    log(f"WPM smoke: source stopped confirmed")

    # Config file on dest must have new hostname (replacement applied)
    dest_config = _ssm_run_ps(
        r["dest_instance_id"],
        "Get-Content 'C:\\testapp\\app.config' -ErrorAction SilentlyContinue",
        timeout=30,
    )
    assert dest_hostname in dest_config, (
        f"Expected dest hostname {dest_hostname!r} in app.config on dest, got: {dest_config!r}"
    )
    assert r["source_hostname"] not in dest_config, (
        f"Source hostname {r['source_hostname']!r} should have been replaced in app.config, got: {dest_config!r}"
    )
    log(f"WPM smoke: hostname replacement confirmed on dest")

    # IIS TestApp site must be present on dest
    iis_check = _ssm_run_ps(
        r["dest_instance_id"],
        "Import-Module WebAdministration -ErrorAction SilentlyContinue; (Get-Website -Name 'TestApp' -ErrorAction SilentlyContinue).Name",
        timeout=30,
    )
    assert "TestApp" in iis_check, (
        f"IIS TestApp site not found on dest after migration: {iis_check!r}"
    )
    log(f"WPM smoke: IIS TestApp site confirmed on dest")

    # -------------------------------------------------------------------------
    # Rollback
    # -------------------------------------------------------------------------
    cr = _rollback_cr(cr_id)

    # EIP must be back on source
    eip_instance = _get_eip_instance(r["eip_id"])
    assert eip_instance == r["source_instance_id"], (
        f"After rollback EIP should be on source {r['source_instance_id']}, got {eip_instance!r}"
    )
    log(f"WPM smoke: EIP back on source confirmed")

    # Source must be running
    ec2.get_waiter("instance_running").wait(
        InstanceIds=[r["source_instance_id"]],
        WaiterConfig={"Delay": 15, "MaxAttempts": 20},
    )
    source_state = _get_instance_state(r["source_instance_id"])
    assert source_state == "running", (
        f"Source {r['source_instance_id']} expected running after rollback, got {source_state!r}"
    )
    log(f"WPM smoke: source running after rollback confirmed — {r['src_ver']}→{r['dst_ver']} PASSED")
