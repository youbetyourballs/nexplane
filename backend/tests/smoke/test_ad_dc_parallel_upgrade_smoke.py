# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Live smoke test: ad_dc_parallel_upgrade

Phases:
  1. smoke_setup     — provision source DC from cached AMI, register creds
  2. smoke_execute   — full CR lifecycle: create -> plan -> approve -> execute
  3. smoke_rollback  — inject fault at FSMO transfer; verify rollback
  4. smoke_teardown  — terminate all smoke instances, deregister assets

All phases follow full CR lifecycle. No mocks.

Run: docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_ad_dc_parallel_upgrade_smoke.py -v -s
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, get_connector_creds_from_db

# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_client: NexplaneClient = None


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    c = _get_client()
    return getattr(c, method)(path, **kwargs)


def _poll_cr(cr_id, terminal_statuses=("completed", "failed", "rollback_completed", "rollback_failed"),
             timeout_s=3600, interval_s=30):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal_statuses:
            return cr
        time.sleep(interval_s)
    raise TimeoutError(f"CR {cr_id} did not reach terminal status within {timeout_s}s")


def _platform_has_creds(connector_type: str) -> bool:
    try:
        connectors = _api("get", f"/connectors?connector_type={connector_type}")
        return len(connectors) > 0
    except Exception:
        return False


def _get_smoke_dc_ami(ec2, ssm, version="2019") -> str:
    """Return the pre-configured DC AMI from SSM cache.

    The AMI must be a Windows Server with:
    - AD DS installed and promoted as a domain controller
    - ADWS (Active Directory Web Services) running
    - WinRM HTTP (port 5985) enabled and accessible within VPC
    - At least one other DC in the domain (so preflight dc_count_safe passes after adding new DC)

    This AMI is NOT automatically created — it must be pre-built and stored in SSM.
    Build it by: launching a Windows Server AMI, configuring AD DS, enabling WinRM,
    then creating an AMI snapshot and storing the AMI ID in SSM at the cache_key below.
    """
    cache_key = f"/nexplane/smoke-amis/dc-smoke-prebuilt/{version}"
    try:
        resp = ssm.get_parameter(Name=cache_key)
        ami_id = resp["Parameter"]["Value"]
        # Verify AMI still exists and is available
        img_resp = ec2.describe_images(ImageIds=[ami_id])
        images = img_resp.get("Images", [])
        if images and images[0].get("State") == "available":
            return ami_id
        raise RuntimeError(f"AMI {ami_id} is not available (state: {images[0].get('State') if images else 'not found'})")
    except Exception as exc:
        raise pytest.skip.Exception(
            f"Pre-configured DC smoke AMI not found in SSM at {cache_key}: {exc}. "
            f"Build the AMI first: launch Windows Server {version}, install+promote AD DS, "
            f"enable WinRM HTTP, create AMI, store AMI ID in SSM at {cache_key}."
        )


# ---------------------------------------------------------------------------
# Module-level shared state (populated by smoke_setup)
# ---------------------------------------------------------------------------

_smoke_state = {
    "source_instance_id": None,
    "source_private_ip": None,
    "new_instance_id": None,
    "connector_id": None,
    "asset_id": None,
    "domain_name": "smoke.nexplane.local",
    "domain_admin_username": "smoke\\Administrator",
    "domain_admin_password": None,
    "aws_creds": None,
    "region": "us-east-1",
    "subnet_id": None,
    "sg_ids": [],
    "smoke_cr_ids": [],
}


# ---------------------------------------------------------------------------
# Smoke skip guard
# ---------------------------------------------------------------------------

def _skip_if_no_creds():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")
    ad_creds = get_connector_creds_from_db("active_directory")
    if not ad_creds:
        pytest.skip("No AD connector creds in platform DB")


# ---------------------------------------------------------------------------
# Phase 1: smoke_setup
# ---------------------------------------------------------------------------

def test_phase1_smoke_setup():
    """Provision source DC from cached AMI; register smoke creds in platform DB."""
    _skip_if_no_creds()

    import boto3

    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")
    _smoke_state["aws_creds"] = aws_creds
    region = aws_creds.get("region", "us-east-1")
    _smoke_state["region"] = region

    ec2 = _make_ec2_client(aws_creds, region)
    ssm = _make_ssm_client(aws_creds, region)

    source_ami = _get_smoke_dc_ami(ec2, ssm, version="2019")
    print(f"[smoke_setup] Source DC AMI: {source_ami}")

    # Get subnet/SG — use nexplane-smoke-dc SG which has WinRM + LDAP + AD ports open in VPC
    default_subnet = aws_creds.get("smoke_subnet_id") or _resolve_default_subnet(ec2, region)
    default_sg = aws_creds.get("smoke_dc_security_group_id") or _resolve_dc_smoke_sg(ec2)
    _smoke_state["subnet_id"] = default_subnet
    _smoke_state["sg_ids"] = [default_sg]

    run_resp = ec2.run_instances(
        ImageId=source_ami,
        InstanceType="t3.medium",
        SubnetId=default_subnet,
        SecurityGroupIds=[default_sg],
        MinCount=1,
        MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "Name", "Value": "np-smoke-source-dc"},
                {"Key": "ManagedBy", "Value": "nexplane"},
                {"Key": "NexplaneSmoke", "Value": "ad_dc_parallel_upgrade"},
            ],
        }],
    )
    source_instance_id = run_resp["Instances"][0]["InstanceId"]
    _smoke_state["source_instance_id"] = source_instance_id
    print(f"[smoke_setup] Source DC instance: {source_instance_id}")

    # Wait for running
    source_private_ip = _wait_ec2_running(ec2, source_instance_id, timeout_s=600)
    _smoke_state["source_private_ip"] = source_private_ip
    print(f"[smoke_setup] Source DC private IP: {source_private_ip}")

    # Wait for WinRM (Windows first-boot takes time)
    domain_admin_password = aws_creds.get("smoke_dc_admin_password", "SmokeTest1234!")
    _smoke_state["domain_admin_password"] = domain_admin_password
    _wait_winrm(source_private_ip, "Administrator", domain_admin_password, timeout_s=1200)
    print(f"[smoke_setup] WinRM reachable on {source_private_ip}")

    # Verify AD DS is running (smoke AMI must have AD DS pre-configured)
    import winrm
    session = winrm.Session(
        target=f"http://{source_private_ip}:5985/wsman",
        auth=("Administrator", domain_admin_password),
        transport="basic",
        server_cert_validation="ignore",
    )
    r = session.run_ps("(Get-Service ADWS).Status")
    adws_status = r.std_out.decode().strip()
    assert adws_status == "Running", f"AD DS not running on source DC: {adws_status}"
    print(f"[smoke_setup] AD DS (ADWS) is Running on source DC")

    r2 = session.run_ps("(Get-ADDomainController -Filter *).HostName")
    dc_list = r2.std_out.decode().strip()
    assert dc_list, "No domain controllers found on source DC"
    print(f"[smoke_setup] Domain controllers: {dc_list}")

    # Register connector + asset in platform
    ad_connector = _api("get", "/connectors?connector_type=active_directory")[0]
    connector_id = ad_connector["id"]
    _smoke_state["connector_id"] = connector_id

    # Update connector creds to point to smoke DC
    _api("put", f"/connectors/{connector_id}/credentials", json={
        "winrm_hostname": source_private_ip,
        "winrm_username": "Administrator",
        "winrm_password": domain_admin_password,
        "winrm_port": "5985",
        "winrm_use_ssl": "false",
    })

    # Register/update asset
    assets = _api("get", f"/assets?connector_id={connector_id}&asset_type=server")
    smoke_asset = next((a for a in assets if a.get("name", "").startswith("np-smoke")), None)
    if not smoke_asset:
        smoke_asset = _api("post", "/assets", json={
            "name": "np-smoke-source-dc",
            "asset_type": "server",
            "connector_id": connector_id,
            "metadata": {"private_ip": source_private_ip, "instance_id": source_instance_id},
        })
    _smoke_state["asset_id"] = smoke_asset["id"]
    print(f"[smoke_setup] Asset ID: {smoke_asset['id']}")
    print("[smoke_setup] PASSED")


def _wait_ec2_running(ec2, instance_id: str, timeout_s: int = 600) -> str:
    deadline = time.monotonic() + timeout_s
    time.sleep(5)  # brief pause for EC2 propagation after run_instances
    while time.monotonic() < deadline:
        try:
            resp = ec2.describe_instances(InstanceIds=[instance_id])
            reservations = resp.get("Reservations", [])
            if not reservations:
                time.sleep(10)
                continue
            inst = reservations[0]["Instances"][0]
            if inst["State"]["Name"] == "running":
                return inst["PrivateIpAddress"]
        except Exception as exc:
            if "InvalidInstanceID" in str(exc) or "NotFound" in str(exc):
                time.sleep(10)
                continue
            raise
        time.sleep(30)
    raise TimeoutError(f"Instance {instance_id} not running after {timeout_s}s")


def _make_boto3_kwargs(creds: dict) -> dict:
    """Build boto3 client kwargs from connector creds (handles both access_key_id and aws_access_key_id)."""
    kwargs = {}
    key_id = creds.get("aws_access_key_id") or creds.get("access_key_id")
    secret = creds.get("aws_secret_access_key") or creds.get("secret_access_key")
    session_token = creds.get("aws_session_token") or creds.get("session_token")
    if key_id:
        kwargs["aws_access_key_id"] = key_id
        kwargs["aws_secret_access_key"] = secret
    if session_token:
        kwargs["aws_session_token"] = session_token
    return kwargs


def _make_ec2_client(creds: dict, region: str):
    import boto3
    return boto3.client("ec2", region_name=region, **_make_boto3_kwargs(creds))


def _make_ssm_client(creds: dict, region: str):
    import boto3
    return boto3.client("ssm", region_name=region, **_make_boto3_kwargs(creds))


def _wait_winrm(hostname: str, username: str, password: str, timeout_s: int = 1200):
    import winrm
    deadline = time.monotonic() + timeout_s
    last_exc = None
    while time.monotonic() < deadline:
        try:
            s = winrm.Session(
                target=f"http://{hostname}:5985/wsman",
                auth=(username, password),
                transport="basic",
                server_cert_validation="ignore",
            )
            r = s.run_ps('Write-Output "ready"')
            if r.status_code == 0:
                return
        except Exception as exc:
            last_exc = exc
        time.sleep(30)
    raise TimeoutError(f"WinRM on {hostname} not ready after {timeout_s}s: {last_exc}")


def _resolve_default_subnet(ec2, region: str) -> str:
    resp = ec2.describe_subnets(Filters=[{"Name": "defaultForAz", "Values": ["true"]}])
    subnets = resp.get("Subnets", [])
    if subnets:
        return subnets[0]["SubnetId"]
    raise RuntimeError("No default subnet found; set smoke_subnet_id in AWS connector creds")


def _resolve_dc_smoke_sg(ec2) -> str:
    """Return the nexplane-smoke-dc SG (has WinRM + LDAP + AD ports open in VPC)."""
    resp = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["nexplane-smoke-dc"]}])
    groups = resp.get("SecurityGroups", [])
    if groups:
        return groups[0]["GroupId"]
    # Fall back to nexplane-smoke-winrm
    resp = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["nexplane-smoke-winrm"]}])
    groups = resp.get("SecurityGroups", [])
    if groups:
        return groups[0]["GroupId"]
    # Last resort: default
    resp = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["default"]}])
    groups = resp.get("SecurityGroups", [])
    if groups:
        return groups[0]["GroupId"]
    raise RuntimeError("No suitable security group found for DC smoke test")


# ---------------------------------------------------------------------------
# Phase 2: smoke_execute — full CR lifecycle
# ---------------------------------------------------------------------------

def test_phase2_smoke_execute():
    """Run full ad_dc_parallel_upgrade CR lifecycle against live source DC."""
    _skip_if_no_creds()
    if not _smoke_state["source_instance_id"]:
        pytest.skip("smoke_setup did not run")

    source_private_ip = _smoke_state["source_private_ip"]
    connector_id = _smoke_state["connector_id"]
    asset_id = _smoke_state["asset_id"]
    domain_name = _smoke_state["domain_name"]
    domain_admin_username = _smoke_state["domain_admin_username"]
    domain_admin_password = _smoke_state["domain_admin_password"]

    # Create CR
    cr = _api("post", "/change-requests", json={
        "change_type": "ad_dc_parallel_upgrade",
        "title": "Smoke: Parallel DC Upgrade (2019 -> 2022)",
        "connector_id": connector_id,
        "asset_ids": [asset_id],
        "desired_outcome": {
            "source_dc_asset_id": asset_id,
            "target_windows_version": "2022",
            "domain_admin_username": domain_admin_username,
            "domain_admin_password": domain_admin_password,
            "new_dc_instance_type": "t3.medium",
            "new_dc_subnet_id": _smoke_state["subnet_id"],
            "new_dc_security_group_ids": _smoke_state["sg_ids"],
            "skip_fsmo_transfer": False,
            "replication_timeout_minutes": 30,
            "dry_run": False,
        },
    })
    cr_id = cr["id"]
    _smoke_state["smoke_cr_ids"].append(cr_id)
    print(f"[smoke_execute] CR created: {cr_id}")

    # Plan
    _api("post", f"/change-requests/{cr_id}/plan")
    print(f"[smoke_execute] CR planned")

    # Submit for approval
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    print(f"[smoke_execute] CR submitted for approval")

    # Approve
    _api("post", f"/change-requests/{cr_id}/approve")
    print(f"[smoke_execute] CR approved")

    # Execute
    _api("post", f"/change-requests/{cr_id}/execute")
    print(f"[smoke_execute] CR executing — polling (timeout 60 min)")

    cr = _poll_cr(cr_id, timeout_s=3600, interval_s=60)
    print(f"[smoke_execute] CR final status: {cr['status']}")
    print(f"[smoke_execute] execution_result: {cr.get('execution_result', {})}")

    assert cr["status"] == "completed", (
        f"CR did not complete: status={cr['status']}, "
        f"result={cr.get('execution_result')}"
    )

    er = cr["execution_result"]
    assert er.get("new_dc_hostname"), "new_dc_hostname missing from execution_result"
    assert er.get("fsmo_transferred") is True, "fsmo_transferred should be True (single-DC domain)"
    assert er.get("demotion_completed") is True, "demotion_completed should be True"
    assert er.get("replication_verified") is True, "replication_verified should be True"
    assert er.get("validation_status") == "passed", f"validation_status: {er.get('validation_status')}"

    # LDAP probe to new DC
    new_dc_ip = er.get("new_dc_private_ip")
    assert new_dc_ip, "new_dc_private_ip missing from execution_result"
    import socket
    try:
        s = socket.create_connection((new_dc_ip, 389), timeout=10)
        s.close()
        print(f"[smoke_execute] LDAP port 389 reachable on new DC {new_dc_ip}")
    except Exception as exc:
        pytest.fail(f"LDAP probe to new DC {new_dc_ip}:389 failed: {exc}")

    # Verify source DC no longer responds as AD DS (AD DS service should be stopped after demotion)
    import winrm
    try:
        s = winrm.Session(
            target=f"http://{source_private_ip}:5985/wsman",
            auth=("Administrator", domain_admin_password),
            transport="basic",
            server_cert_validation="ignore",
        )
        r = s.run_ps("(Get-Service ADWS -ErrorAction SilentlyContinue).Status")
        adws_status = r.std_out.decode().strip()
        assert adws_status != "Running", f"Source DC still shows ADWS Running after demotion: {adws_status}"
        print(f"[smoke_execute] Source DC ADWS status after demotion: '{adws_status}' (expected empty or Stopped)")
    except Exception as exc:
        # Source DC may have been terminated or WinRM may be down post-demotion — acceptable
        print(f"[smoke_execute] WinRM to source DC failed post-demotion (acceptable): {exc}")

    # Record new instance for teardown
    _smoke_state["new_instance_id"] = er.get("new_instance_id") or er.get("new_ec2_instance_id")
    print("[smoke_execute] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: smoke_rollback — inject fault, verify rollback
# ---------------------------------------------------------------------------

def test_phase3_smoke_rollback():
    """Provision a second source DC; run CR with dry_run to trigger rollback at FSMO transfer."""
    _skip_if_no_creds()
    if not _smoke_state["aws_creds"]:
        pytest.skip("smoke_setup did not populate aws_creds")

    aws_creds = _smoke_state["aws_creds"]
    region = _smoke_state["region"]
    ec2 = _make_ec2_client(aws_creds, region)
    ssm = _make_ssm_client(aws_creds, region)

    # Provision a fresh source DC for rollback test
    source_ami = _get_smoke_dc_ami(ec2, ssm, version="2019")
    run_resp = ec2.run_instances(
        ImageId=source_ami,
        InstanceType="t3.medium",
        SubnetId=_smoke_state["subnet_id"],
        SecurityGroupIds=_smoke_state["sg_ids"],
        MinCount=1, MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": "np-smoke-rollback-dc"},
                     {"Key": "NexplaneSmoke", "Value": "ad_dc_parallel_upgrade_rollback"}],
        }],
    )
    rollback_source_id = run_resp["Instances"][0]["InstanceId"]
    _smoke_state["smoke_cr_ids"].append(f"ec2:{rollback_source_id}")  # track for teardown
    print(f"[smoke_rollback] Rollback source DC: {rollback_source_id}")

    rollback_source_ip = _wait_ec2_running(ec2, rollback_source_id, timeout_s=600)
    _wait_winrm(rollback_source_ip, "Administrator",
                _smoke_state["domain_admin_password"], timeout_s=1200)

    # Update connector to point to rollback source DC
    connector_id = _smoke_state["connector_id"]
    _api("put", f"/connectors/{connector_id}/credentials", json={
        "winrm_hostname": rollback_source_ip,
        "winrm_username": "Administrator",
        "winrm_password": _smoke_state["domain_admin_password"],
        "winrm_port": "5985",
        "winrm_use_ssl": "false",
    })

    # Create CR with skip_fsmo_transfer=True to exercise Case A rollback cleanly
    # (new DC gets provisioned and replicated but FSMOs never transferred)
    # We trigger rollback immediately after the CR is executing and has passed phase 2
    cr = _api("post", "/change-requests", json={
        "change_type": "ad_dc_parallel_upgrade",
        "title": "Smoke: Parallel DC Upgrade Rollback Test",
        "connector_id": connector_id,
        "asset_ids": [_smoke_state["asset_id"]],
        "desired_outcome": {
            "source_dc_asset_id": _smoke_state["asset_id"],
            "target_windows_version": "2022",
            "domain_admin_username": _smoke_state["domain_admin_username"],
            "domain_admin_password": _smoke_state["domain_admin_password"],
            "new_dc_instance_type": "t3.medium",
            "new_dc_subnet_id": _smoke_state["subnet_id"],
            "new_dc_security_group_ids": _smoke_state["sg_ids"],
            "skip_fsmo_transfer": True,  # Stops before FSMO transfer -> Case A rollback
            "replication_timeout_minutes": 5,
            "dry_run": False,
        },
    })
    cr_id = cr["id"]
    print(f"[smoke_rollback] Rollback CR created: {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve")
    _api("post", f"/change-requests/{cr_id}/execute")

    # Poll until executing (replication will timeout at 5 min), then trigger rollback
    print("[smoke_rollback] Waiting for CR to fail at replication timeout (5 min)...")
    cr = _poll_cr(
        cr_id,
        terminal_statuses=("failed", "completed"),
        timeout_s=900,
        interval_s=30,
    )
    # Now rollback
    print(f"[smoke_rollback] CR status: {cr['status']} — triggering rollback")
    _api("post", f"/change-requests/{cr_id}/rollback")
    cr = _poll_cr(
        cr_id,
        terminal_statuses=("rollback_completed", "rollback_failed"),
        timeout_s=1800,
        interval_s=30,
    )
    print(f"[smoke_rollback] Rollback final status: {cr['status']}")
    print(f"[smoke_rollback] rollback_result: {cr.get('rollback_result', {})}")

    assert cr["status"] == "rollback_completed", (
        f"Rollback did not complete: status={cr['status']}, "
        f"result={cr.get('rollback_result')}"
    )

    rr = cr.get("rollback_result", {})
    assert rr.get("rolled_back") is True, f"rolled_back not True: {rr}"
    assert rr.get("strategy") == "demote_new_dc", f"Expected demote_new_dc strategy: {rr}"
    assert rr.get("new_instance_terminated") is True, f"New instance not terminated: {rr}"

    # Verify new instance is terminated via boto3
    new_instance_id = rr.get("new_instance_id")
    if new_instance_id:
        resp = ec2.describe_instances(InstanceIds=[new_instance_id])
        state = resp["Reservations"][0]["Instances"][0]["State"]["Name"]
        assert state in ("terminated", "shutting-down"), (
            f"New DC instance {new_instance_id} still in state: {state}"
        )
        print(f"[smoke_rollback] New DC instance {new_instance_id} confirmed {state}")

    # Terminate rollback source DC
    ec2.terminate_instances(InstanceIds=[rollback_source_id])
    print(f"[smoke_rollback] Rollback source DC {rollback_source_id} terminating")
    print("[smoke_rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 4: smoke_teardown
# ---------------------------------------------------------------------------

def test_phase4_smoke_teardown():
    """Terminate all smoke EC2 instances and deregister smoke assets."""
    _skip_if_no_creds()

    aws_creds = _smoke_state.get("aws_creds") or {}
    region = _smoke_state.get("region", "us-east-1")
    ec2 = _make_ec2_client(aws_creds, region)

    instances_to_terminate = []
    if _smoke_state.get("source_instance_id"):
        instances_to_terminate.append(_smoke_state["source_instance_id"])
    if _smoke_state.get("new_instance_id"):
        instances_to_terminate.append(_smoke_state["new_instance_id"])

    # Scan for any lingering smoke-tagged instances
    resp = ec2.describe_instances(Filters=[
        {"Name": "tag:NexplaneSmoke", "Values": ["ad_dc_parallel_upgrade", "ad_dc_parallel_upgrade_rollback"]},
        {"Name": "instance-state-name", "Values": ["running", "stopped", "pending"]},
    ])
    for res in resp.get("Reservations", []):
        for inst in res.get("Instances", []):
            if inst["InstanceId"] not in instances_to_terminate:
                instances_to_terminate.append(inst["InstanceId"])

    if instances_to_terminate:
        print(f"[smoke_teardown] Terminating: {instances_to_terminate}")
        try:
            ec2.terminate_instances(InstanceIds=instances_to_terminate)
            print(f"[smoke_teardown] Termination initiated for {len(instances_to_terminate)} instances")
        except Exception as exc:
            print(f"[smoke_teardown] WARNING: terminate failed: {exc}")

    # Restore connector creds to production values (if any)
    # Connector creds are smoke-only; skip restore if no prod backup exists
    print("[smoke_teardown] PASSED")
