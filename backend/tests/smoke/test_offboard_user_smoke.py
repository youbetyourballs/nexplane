# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test: offboard_user CR — discovery + verification lifecycle.

Boots a Windows 2019 DC from cached AMI, creates a test AD user, runs
offboard_user CR (create→plan→submit→approve→execute), verifies discovery
found the user, verification passed, AD account is actually disabled, then
rolls back and confirms re-enabled.

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_offboard_user_smoke.py -v -s
"""

import os
import sys
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_DC_AMI_SSM_KEY = "/nexplane/smoke-amis/dc-smoke-prebuilt/2019"
_DC_ADMIN_PASSWORD = "SmokeTest1234!"
_DC_DOMAIN = "smoke.nexplane.local"
_DC_NETBIOS = "SMOKE"
_SSM_PROFILE = "nexplane-smoke-ssm"

CR_TIMEOUT = 300
POLL_INTERVAL = 10

_client: NexplaneClient = None
_state: dict = {}


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id, timeout_s=CR_TIMEOUT, interval_s=POLL_INTERVAL):
    terminal = ("completed", "failed", "rolled_back", "rolled_back_with_warnings")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal:
            return cr
        time.sleep(interval_s)
    raise TimeoutError(f"CR {cr_id} did not reach terminal status in {timeout_s}s")


def _step_results(cr: dict) -> list:
    """Return all step results from execution_runs."""
    runs = cr.get("execution_runs") or []
    if runs:
        run_result = runs[0].get("result") or {}
        return run_result.get("execution", {}).get("steps", [])
    return []


def _get_aws_creds():
    return get_connector_creds_from_db("aws") or {}


def _get_boto3_client(service, aws_creds):
    return boto3.client(
        service,
        aws_access_key_id=aws_creds.get("access_key_id") or aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key") or aws_creds.get("aws_secret_access_key"),
        region_name=aws_creds.get("region", "us-east-1"),
    )


def _get_smoke_dc_ami(ec2, ssm) -> str:
    """Return cached DC AMI from SSM, or skip if not present."""
    try:
        resp = ssm.get_parameter(Name=_DC_AMI_SSM_KEY)
        ami_id = resp["Parameter"]["Value"]
        imgs = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if imgs and imgs[0].get("State") == "available":
            log(f"Using cached DC AMI: {ami_id}")
            return ami_id
    except Exception:
        pass
    pytest.skip(
        f"No usable DC AMI in SSM at {_DC_AMI_SSM_KEY}. "
        "Run test_ad_dc_parallel_upgrade_smoke.py phase1 first to build and cache one."
    )


def _launch_dc(ec2, ssm, ami_id, aws_creds) -> tuple:
    """Launch a DC from AMI, wait for WinRM reachability. Returns (instance_id, private_ip)."""
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id = aws_creds.get("smoke_dc_security_group_id", "sg-08891be3823c0e4ce")

    launch_kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-offboard-dc"},
            {"Key": "nexplane-purpose", "Value": "smoke-offboard-user"},
        ]}],
    )
    if subnet_id:
        launch_kwargs["SubnetId"] = subnet_id
    if sg_id:
        launch_kwargs["SecurityGroupIds"] = [sg_id]

    resp = ec2.run_instances(**launch_kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"Launched DC instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    log(f"DC running at {private_ip} — waiting for WinRM port 5985 (up to 12 min)")

    import socket
    deadline = time.time() + 720
    winrm_ready = False
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 5985), timeout=5)
            s.close()
            log(f"WinRM ready on {private_ip}:5985")
            winrm_ready = True
            break
        except OSError:
            pass
    if not winrm_ready:
        ec2.terminate_instances(InstanceIds=[instance_id])
        pytest.fail(f"WinRM never reachable on {private_ip}:5985 within 12 min")

    time.sleep(30)  # Let NTDS settle after WinRM comes up
    return instance_id, private_ip


def _register_ad_connector(private_ip) -> str:
    """Register an AD connector for the smoke DC. Returns connector_id."""
    run_id = uuid.uuid4().hex[:6]
    connector = _api("post", "/connectors", json={
        "name": f"smoke-offboard-ad-{run_id}",
        "connector_type": "active_directory",
    })
    conn_id = connector["id"]
    base_dn = ",".join(f"DC={part}" for part in _DC_DOMAIN.split("."))
    bind_dn = f"CN=Administrator,CN=Users,{base_dn}"
    _api("put", f"/connectors/{conn_id}/credentials", json={
        "credentials": {
            "server": private_ip,
            "port": "389",
            "base_dn": base_dn,
            "bind_dn": bind_dn,
            "bind_password": _DC_ADMIN_PASSWORD,
            "use_ssl": "false",
            "winrm_hostname": private_ip,
            "winrm_port": "5985",
            "winrm_username": f"{_DC_NETBIOS}\\Administrator",
            "winrm_password": _DC_ADMIN_PASSWORD,
            "winrm_use_ssl": "false",
        }
    })
    log(f"Registered AD connector {conn_id}")
    return conn_id, base_dn, bind_dn


def _create_ad_user(private_ip, base_dn, bind_dn, sam, email) -> str:
    """Create a test AD user via LDAP with SSL for password set. Returns user DN.

    AD will not create an enabled account without a password on the wire, so:
      1. Create the account disabled (UAC=514) — no password required.
      2. Set unicodePwd via LDAPS (port 636). If LDAPS is unavailable, the
         account stays disabled but can still be offboarded (disabled→disabled
         is a no-op for the executor, so the test would skip).
      3. Enable the account (UAC=512) so the executor has something to disable.
    """
    from ldap3 import Server, Connection, ALL, MODIFY_REPLACE

    user_dn = f"CN={sam},CN=Users,{base_dn}"
    run_id = sam.split("-")[-1]

    # Step 1: create disabled account (no password needed)
    srv = Server(private_ip, port=389, get_info=ALL)
    conn = Connection(srv, user=bind_dn, password=_DC_ADMIN_PASSWORD, auto_bind=True)
    conn.add(user_dn, ["top", "person", "organizationalPerson", "user"], {
        "sAMAccountName": sam,
        "userPrincipalName": email,
        "mail": email,
        "displayName": f"Smoke Offboard {run_id}",
        "userAccountControl": 514,  # disabled, normal account — password not required
    })
    result = conn.result
    assert result.get("result") == 0, f"Failed to create AD user {sam}: {result}"

    # Step 2: set password and enable account via WinRM/PowerShell (avoids LDAPS dependency)
    try:
        from app.connectors.executors.active_directory._client import run_winrm_ps
        winrm_creds = {
            "winrm_hostname": private_ip,
            "winrm_port": "5985",
            "winrm_username": f"{_DC_NETBIOS}\\Administrator",
            "winrm_password": _DC_ADMIN_PASSWORD,
            "winrm_use_ssl": "false",
        }
        ps_script = (
            f'$pwd = ConvertTo-SecureString "{_DC_ADMIN_PASSWORD}" -AsPlainText -Force; '
            f'Set-ADAccountPassword -Identity "{sam}" -NewPassword $pwd -Reset; '
            f'Enable-ADAccount -Identity "{sam}"; '
            f'Write-Output "done"'
        )
        stdout, stderr, rc = run_winrm_ps(winrm_creds, ps_script)
        if rc != 0:
            log(f"WARNING: WinRM password/enable failed (rc={rc}): {stderr}; account stays disabled")
        else:
            log(f"Account {sam} enabled via WinRM: {stdout}")
    except Exception as exc:
        log(f"WARNING: WinRM unavailable — account {sam} stays disabled ({exc})")

    conn.unbind()
    log(f"Created AD user {sam} ({email})")
    return user_dn


def _query_uac(private_ip, base_dn, bind_dn, sam) -> int:
    """Query userAccountControl for a sAMAccountName. Returns UAC integer."""
    from ldap3 import Server, Connection, ALL

    srv = Server(private_ip, port=389, get_info=ALL)
    conn = Connection(srv, user=bind_dn, password=_DC_ADMIN_PASSWORD, auto_bind=True)
    conn.search(base_dn, f"(sAMAccountName={sam})", attributes=["userAccountControl"])
    assert conn.entries, f"User {sam} not found in AD"
    uac = int(conn.entries[0].userAccountControl.value)
    conn.unbind()
    return uac


# ---------------------------------------------------------------------------
# Phase 1: provision DC
# ---------------------------------------------------------------------------

def test_phase1_provision_dc():
    """Launch a DC from the cached AMI and register AD connector."""
    aws_creds = _get_aws_creds()
    if not aws_creds:
        pytest.skip("No AWS connector credentials — cannot provision DC")

    ec2 = _get_boto3_client("ec2", aws_creds)
    ssm = _get_boto3_client("ssm", aws_creds)

    ami_id = _get_smoke_dc_ami(ec2, ssm)
    instance_id, private_ip = _launch_dc(ec2, ssm, ami_id, aws_creds)
    _state["instance_id"] = instance_id
    _state["private_ip"] = private_ip
    _state["ec2"] = ec2

    conn_id, base_dn, bind_dn = _register_ad_connector(private_ip)
    _state["conn_id"] = conn_id
    _state["base_dn"] = base_dn
    _state["bind_dn"] = bind_dn

    log("Phase 1 PASSED: DC provisioned + AD connector registered")


# ---------------------------------------------------------------------------
# Phase 2: create test AD user
# ---------------------------------------------------------------------------

def test_phase2_create_test_user():
    """Create a test AD user that offboard_user will discover and disable."""
    if not _state.get("private_ip"):
        pytest.skip("DC not provisioned (phase 1 skipped)")

    run_id = uuid.uuid4().hex[:6]
    sam = f"smoke-{run_id}"
    email = f"{sam}@{_DC_DOMAIN}"

    user_dn = _create_ad_user(
        _state["private_ip"],
        _state["base_dn"],
        _state["bind_dn"],
        sam,
        email,
    )
    _state["test_user_sam"] = sam
    _state["test_user_email"] = email
    _state["test_user_dn"] = user_dn

    # Wait for AD to index the new account before discovery runs
    log("Waiting 15s for AD to index the new user...")
    time.sleep(15)

    # Pre-check: confirm discovery will find the user by mail/UPN via LDAP
    from ldap3 import Server as _Srv, Connection as _Conn, ALL as _ALL
    private_ip = _state["private_ip"]
    base_dn = _state["base_dn"]
    bind_dn = _state["bind_dn"]
    check_srv = _Srv(private_ip, port=389, get_info=_ALL)
    check_conn = _Conn(check_srv, user=bind_dn, password=_DC_ADMIN_PASSWORD, auto_bind=True)
    check_conn.search(base_dn, f"(mail={email})", attributes=["sAMAccountName"])
    if not check_conn.entries:
        check_conn.search(base_dn, f"(userPrincipalName={email})", attributes=["sAMAccountName"])
    found_by_email = bool(check_conn.entries)
    check_conn.unbind()
    assert found_by_email, (
        f"Pre-check failed: user {sam} not findable by mail/UPN ({email}) in AD. "
        "Discovery will fail. Check that the account was created with the mail attribute."
    )
    log(f"Pre-check PASSED: user {sam} visible by email in AD")

    log(f"Phase 2 PASSED: test AD user created: {sam}")


# ---------------------------------------------------------------------------
# Phase 3: discovery accuracy (plan-only)
# ---------------------------------------------------------------------------

def test_phase3_discovery_accuracy():
    """Plan-only: discovery manifest must find the test AD user with correct sAMAccountName."""
    if not _state.get("test_user_email"):
        pytest.skip("Test user not created (phase 2 skipped)")

    log("Phase 3: discovery accuracy")

    run_id = uuid.uuid4().hex[:6]
    cr = _api("post", "/change-requests", json={
        "title": f"smoke-offboard-discovery-{run_id}",
        "change_type": "offboard_user",
        "target_asset_ids": [],
        "desired_outcome": {
            "summary": "Smoke: offboard_user discovery phase",
            "target_email": _state["test_user_email"],
            "reason": "smoke_test",
        },
    })
    cr_id = cr["id"]
    _state["phase3_cr_id"] = cr_id

    # Plan only — do NOT execute
    plan = _api("post", f"/change-requests/{cr_id}/plan")

    steps = plan.get("generated_steps", [])

    # At least one AD-related step must be in the plan
    ad_steps = [
        s for s in steps
        if "active_directory" in (s.get("connector_type", "") or s.get("action_id", ""))
    ]
    assert len(ad_steps) > 0, (
        f"No AD steps in plan. Steps: {[s.get('action_id') for s in steps]}"
    )

    # Phase 5 verify steps must include an AD entry with correct account_identifier
    verify_steps = [s for s in steps if s.get("phase") == 5]
    ad_verify = [s for s in verify_steps if "active_directory" in s.get("action_id", "")]
    assert len(ad_verify) == 1, (
        f"Expected 1 AD verify step in phase 5, got: {ad_verify}"
    )
    assert ad_verify[0]["parameters"].get("account_identifier") == _state["test_user_sam"], (
        f"Expected account_identifier={_state['test_user_sam']!r}, "
        f"got {ad_verify[0]['parameters'].get('account_identifier')!r}"
    )

    log(f"Phase 3 PASSED: discovery found {_state['test_user_sam']} in AD")


# ---------------------------------------------------------------------------
# Phase 4: full lifecycle — execute + verify
# ---------------------------------------------------------------------------

def test_phase4_full_lifecycle_execute():
    """Full lifecycle: create→plan→submit→approve→execute. Assert verification passes and AD account is disabled."""
    if not _state.get("test_user_email"):
        pytest.skip("Test user not created (phase 2 skipped)")

    log("Phase 4: full lifecycle execution")

    run_id = uuid.uuid4().hex[:6]
    cr = _api("post", "/change-requests", json={
        "title": f"smoke-offboard-full-{run_id}",
        "change_type": "offboard_user",
        "target_asset_ids": [],
        "desired_outcome": {
            "summary": "Smoke: offboard_user full lifecycle",
            "target_email": _state["test_user_email"],
            "reason": "smoke_test_termination",
        },
    })
    cr_id = cr["id"]
    _state["phase4_cr_id"] = cr_id

    _api("post", f"/change-requests/{cr_id}/plan")
    log(f"  Planned CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    log(f"  Submitted CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "offboard smoke"})
    log(f"  Approved CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/execute")
    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", (
        f"CR {cr_id} failed with status={cr['status']} reason={cr.get('failure_reason')}"
    )

    # Assert Phase 5 verification steps passed
    step_results = _step_results(cr)
    verify_results = [
        s for s in step_results
        if "verify" in (s.get("action_id", "") or "")
    ]
    assert len(verify_results) > 0, (
        "No verification steps found in execution result"
    )
    for vr in verify_results:
        result = vr.get("result", {})
        assert result.get("verified") is True, (
            f"Verification failed for step {vr.get('action_id')}: {result.get('error')}"
        )

    # Assert Phase 6 report was generated
    report_results = [
        s for s in step_results
        if "report" in (s.get("action_id", "") or "")
    ]
    assert len(report_results) == 1, (
        f"Expected 1 report step, got {len(report_results)}"
    )
    report = report_results[0].get("result", {}).get("report", {})
    assert "discovery_manifest" in report, (
        f"Report missing discovery_manifest. Keys: {list(report.keys())}"
    )
    assert _state["test_user_email"] in report.get("target_email", ""), (
        f"Report target_email mismatch: {report.get('target_email')!r}"
    )

    # Independent LDAP confirmation — account must be disabled
    uac = _query_uac(
        _state["private_ip"],
        _state["base_dn"],
        _state["bind_dn"],
        _state["test_user_sam"],
    )
    assert uac & 2, (
        f"Expected AD account disabled (UAC bit 2 set), got UAC={uac}"
    )

    log(f"Phase 4 PASSED: CR completed, verification passed, AD account disabled (UAC={uac})")


# ---------------------------------------------------------------------------
# Phase 5: rollback re-enables AD account
# ---------------------------------------------------------------------------

def test_phase5_rollback_reenables_account():
    """Rollback the offboard CR and confirm the AD account is re-enabled."""
    cr_id = _state.get("phase4_cr_id")
    if not cr_id:
        pytest.skip("Phase 4 CR not available (phase 4 skipped)")

    log("Phase 5: rollback")

    _api("post", f"/change-requests/{cr_id}/rollback")
    cr = _poll_cr(cr_id)
    assert cr["status"] in ("rolled_back", "rolled_back_with_warnings"), (
        f"Rollback did not complete: status={cr['status']}"
    )

    # Independent LDAP confirmation — account must be enabled again
    uac = _query_uac(
        _state["private_ip"],
        _state["base_dn"],
        _state["bind_dn"],
        _state["test_user_sam"],
    )
    assert not (uac & 2), (
        f"Expected AD account re-enabled (UAC bit 2 clear) after rollback, got UAC={uac}"
    )

    log(f"Phase 5 PASSED: AD account re-enabled after rollback (UAC={uac})")


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

def test_phase6_teardown():
    """Terminate the DC instance."""
    ec2 = _state.get("ec2")
    instance_id = _state.get("instance_id")
    if ec2 and instance_id:
        try:
            ec2.terminate_instances(InstanceIds=[instance_id])
            log(f"Terminated DC instance {instance_id}")
        except Exception as exc:
            log(f"WARNING: failed to terminate {instance_id}: {exc}")
    log("Phase 6 (teardown) PASSED")
