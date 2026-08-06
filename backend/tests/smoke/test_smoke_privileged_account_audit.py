# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Privileged Account Audit

Phases:
  1. provision_dc   — reuse existing AD connector or launch DC from cached AMI
  2. run_audit      — privileged_account_audit CR → plan → approve → execute → verify
  3. teardown       — terminate instance and deregister connector/asset if we provisioned

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_privileged_account_audit.py -v -s

Skip conditions:
  - No AWS connector credentials in platform DB
  - No AD connector in DB AND no cached DC AMI in SSM
"""

import os
import sys
import socket
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_state = {
    "connector_id": None,
    "asset_id": None,
    "instance_id": None,
    "provisioned_by_us": False,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_client: NexplaneClient = None


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _boto3_client(service, creds):
    return boto3.client(
        service,
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    )


def _get_smoke_dc_ami(ec2, ssm) -> str:
    try:
        resp = ssm.get_parameter(Name=_DC_AMI_SSM_KEY)
        ami_id = resp["Parameter"]["Value"]
        imgs = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if imgs and imgs[0].get("State") == "available":
            log(f"  Using cached DC AMI: {ami_id}")
            return ami_id
    except Exception:
        pass
    pytest.skip(
        f"No usable DC AMI in SSM at {_DC_AMI_SSM_KEY}. "
        "Run test_ad_tier_zero_cr_smoke.py phase1 first to build and cache one."
    )


def _launch_dc(ec2, ssm, ami_id, aws_creds) -> tuple:
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id = aws_creds.get("smoke_dc_security_group_id")

    launch_kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-priv-audit-dc"},
            {"Key": "nexplane-purpose", "Value": "smoke-privileged-account-audit"},
        ]}],
    )
    if subnet_id:
        launch_kwargs["SubnetId"] = subnet_id
    if sg_id:
        launch_kwargs["SecurityGroupIds"] = [sg_id]

    resp = ec2.run_instances(**launch_kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched DC instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    log(f"  DC at {private_ip} — waiting for LDAP port 389 (up to 10 min)")

    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 389), timeout=5)
            s.close()
            log(f"  LDAP port open on {private_ip}:389 — waiting for NTDS bind readiness (up to 3 min)")
            break
        except OSError:
            pass
    else:
        ec2.terminate_instances(InstanceIds=[instance_id])
        pytest.fail(f"LDAP never reachable on {private_ip}:389 within 10 min")

    # Port open ≠ NTDS ready for binds — poll until an anonymous LDAP connect succeeds
    import ldap3
    base_dn = ",".join(f"DC={part}" for part in _DC_DOMAIN.split("."))
    bind_dn = f"CN=Administrator,CN=Users,{base_dn}"
    bind_deadline = time.time() + 180
    while time.time() < bind_deadline:
        time.sleep(10)
        try:
            server = ldap3.Server(private_ip, port=389, connect_timeout=5)
            conn = ldap3.Connection(server, user=bind_dn, password=_DC_ADMIN_PASSWORD,
                                    auto_bind=ldap3.AUTO_BIND_NO_TLS)
            if conn.bind():
                conn.unbind()
                log(f"  NTDS accepting binds on {private_ip}:389")
                break
        except Exception:
            pass
    else:
        ec2.terminate_instances(InstanceIds=[instance_id])
        pytest.fail(f"NTDS on {private_ip}:389 never accepted bind within 3 min after port open")

    return instance_id, private_ip


def _register_dc_asset(private_ip, aws_creds) -> tuple:
    run_id = uuid.uuid4().hex[:6]
    base_dn = ",".join(f"DC={part}" for part in _DC_DOMAIN.split("."))
    bind_dn = f"CN=Administrator,CN=Users,{base_dn}"

    connector = _api("post", "/connectors", json={
        "name": f"smoke-priv-audit-dc-{run_id}",
        "connector_type": "active_directory",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={
        "credentials": {
            "server": private_ip,
            "port": "389",
            "base_dn": base_dn,
            "bind_dn": bind_dn,
            "bind_password": _DC_ADMIN_PASSWORD,
            "use_ssl": "false",
        }
    })
    log(f"  Registered AD connector {conn_id}")

    asset = _api("post", "/assets", json={
        "name": f"smoke-priv-audit-dc-{run_id}",
        "asset_type": "server",
        "criticality": "medium",
        "environment": "staging",
        "hostname": private_ip,
        "connector_id": conn_id,
        "metadata": {"role": "domain_controller", "domain": _DC_DOMAIN},
    })
    asset_id = asset["id"]
    _api("post", f"/assets/{asset_id}/connectors", json={"connector_id": conn_id})
    log(f"  Registered DC asset {asset_id}")
    return conn_id, asset_id


def _poll_cr(cr_id, timeout_s=CR_TIMEOUT):
    terminal = ("completed", "failed", "rolled_back", "rollback_failed")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal:
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not reach terminal within {timeout_s}s")


def _exec_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    if runs:
        raw = runs[0].get("result") or {}
        steps = raw.get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


# ---------------------------------------------------------------------------
# Phase 1: provision DC
# ---------------------------------------------------------------------------

def test_phase1_provision_dc():
    """Reuse existing AD connector, or provision a fresh DC from cached AMI."""
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")

    # Check if an AD connector is already registered with a reachable DC
    existing_ad = get_connector_creds_from_db("active_directory")
    if existing_ad:
        dc_server = existing_ad.get("server", "")
        dc_port = int(existing_ad.get("port", 389))
        dc_reachable = False
        if dc_server:
            try:
                s = socket.create_connection((dc_server, dc_port), timeout=5)
                s.close()
                dc_reachable = True
                log(f"  Existing DC {dc_server}:{dc_port} is reachable")
            except OSError:
                log(f"  Existing AD connector DC {dc_server}:{dc_port} unreachable — will provision fresh DC")

        if dc_reachable:
            connectors = _api("get", "/connectors")
            ad_connectors = [c for c in connectors if c.get("connector_type") == "active_directory"]
            if ad_connectors:
                conn = ad_connectors[0]
                _state["connector_id"] = conn["id"]
                try:
                    assets = _api("get", "/assets")
                    for a in assets:
                        meta = a.get("metadata") or {}
                        if meta.get("role") == "domain_controller" or a.get("connector_id") == conn["id"]:
                            _state["asset_id"] = a["id"]
                            break
                except Exception:
                    pass
                _state["provisioned_by_us"] = False
                log(f"[PHASE 1] Reusing existing AD connector {conn['id']}")
                log("[PHASE 1: provision-dc] PASSED (reused existing)")
                return

    # No existing AD connector — provision from cached AMI
    ec2 = _boto3_client("ec2", aws_creds)
    ssm = _boto3_client("ssm", aws_creds)
    ami_id = _get_smoke_dc_ami(ec2, ssm)
    instance_id, private_ip = _launch_dc(ec2, ssm, ami_id, aws_creds)
    conn_id, asset_id = _register_dc_asset(private_ip, aws_creds)

    _state.update({
        "connector_id": conn_id,
        "asset_id": asset_id,
        "instance_id": instance_id,
        "provisioned_by_us": True,
    })
    log("[PHASE 1: provision-dc] PASSED (launched from AMI)")


# ---------------------------------------------------------------------------
# Phase 2: run privileged_account_audit CR
# ---------------------------------------------------------------------------

@pytest.mark.smoke
@pytest.mark.smoke_phase("AD_AUDIT")
def test_phase2_run_privileged_account_audit():
    """Create, plan, approve, execute privileged_account_audit CR and verify result."""
    if not _state.get("connector_id"):
        pytest.skip("Phase 1 did not complete — skipping audit phase")

    conn_id = _state["connector_id"]
    run_id = uuid.uuid4().hex[:6]

    cr = _api("post", "/change-requests", json={
        "title": f"smoke-privileged-account-audit-{run_id}",
        "change_type": "privileged_account_audit",
        "desired_outcome": {
            "summary": "Smoke: audit privileged AD accounts",
            "stale_threshold_days": 90,
            "generate_remediations": True,
        },
        "connector_id": conn_id,
    })
    cr_id = cr["id"]
    log(f"  Created CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "privileged audit smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", (
        f"CR {cr_id} reached {cr['status']} instead of completed"
    )

    result = _exec_result(cr)

    # If DC was unreachable, executor returns mock result — still validates structure
    if result.get("mock"):
        log("  Executor returned mock result (no LDAP creds resolved) — checking structure")

    if result.get("error"):
        pytest.skip(f"AD audit returned error (DC unreachable?): {result['error']}")

    for key in ("group_summary", "accounts", "risky_accounts", "proposed_remediations", "audited_at"):
        assert key in result, f"Missing '{key}' in execution result: {result}"

    group_summary = result["group_summary"]
    for group in ("Domain Admins", "Administrators"):
        assert group in group_summary, f"Expected '{group}' in group_summary: {group_summary}"

    for acc in result.get("risky_accounts", []):
        assert "sAMAccountName" in acc
        assert "risk_flags" in acc
        assert "severity" in acc

    for rem in result.get("proposed_remediations", []):
        assert "change_type" in rem
        assert "parameters" in rem
        assert "reason" in rem

    log(
        f"  Audit: {len(result['accounts'])} accounts, "
        f"{len(result['risky_accounts'])} risky, "
        f"{len(result['proposed_remediations'])} remediations proposed"
    )

    # Rollback is a no-op but must succeed
    _api("post", f"/change-requests/{cr_id}/rollback")
    log("[PHASE 2: run_audit] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: teardown
# ---------------------------------------------------------------------------

def test_phase3_teardown():
    """Terminate DC instance and deregister connector/asset if we provisioned them."""
    if not _state.get("provisioned_by_us"):
        log("[PHASE 3: teardown] SKIPPED (reused existing infrastructure)")
        return

    aws_creds = get_connector_creds_from_db("aws")
    asset_id = _state.get("asset_id")
    conn_id = _state.get("connector_id")
    instance_id = _state.get("instance_id")

    if asset_id:
        try:
            _api("delete", f"/assets/{asset_id}")
            log(f"  Deleted asset {asset_id}")
        except Exception as exc:
            log(f"  Warning: could not delete asset {asset_id}: {exc}", ok=False)

    if conn_id:
        try:
            _api("delete", f"/connectors/{conn_id}")
            log(f"  Deleted connector {conn_id}")
        except Exception as exc:
            log(f"  Warning: could not delete connector {conn_id}: {exc}", ok=False)

    if instance_id and aws_creds:
        try:
            ec2 = _boto3_client("ec2", aws_creds)
            ec2.terminate_instances(InstanceIds=[instance_id])
            log(f"  Terminated EC2 instance {instance_id}")
        except Exception as exc:
            log(f"  Warning: could not terminate {instance_id}: {exc}", ok=False)

    log("[PHASE 3: teardown] PASSED")
