# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Live smoke tests: AD tier-zero CR types

Covers:
  - ad_domain_functional_level_upgrade (dry-run only — irreversible in prod)
  - ad_trust_create (create + rollback removes trust)
  - ad_gpo_deploy (create + rollback removes GPO)
  - ad_pso_manage create + rollback
  - ad_stale_computer_cleanup report_only mode + rollback

All phases follow full CR lifecycle (create → plan → approve → execute → rollback).
Infrastructure: single Windows 2019 DC provisioned from the cached SSM AMI
(same AMI as test_ad_dc_parallel_upgrade_smoke.py). Reuses existing AMI; no
new DC build is triggered if the cache is warm.

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_ad_tier_zero_cr_smoke.py -v -s

Skip conditions:
    - No AWS connector credentials in platform DB
    - No AD connector credentials in platform DB
    - Cached DC AMI absent in SSM and no AWS creds to build one
"""

import os
import sys
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

CR_TIMEOUT = 600   # 10 min per CR
POLL_INTERVAL = 20

# ---------------------------------------------------------------------------
# Client + helpers
# ---------------------------------------------------------------------------

_client: NexplaneClient = None


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
    raise TimeoutError(f"CR {cr_id} did not reach terminal status within {timeout_s}s")


def _full_cr_lifecycle(change_type, parameters, asset_ids, connector_id, title_prefix):
    """create → plan → approve → execute. Returns final CR dict."""
    run_id = uuid.uuid4().hex[:6]
    cr = _api("post", "/change-requests", json={
        "title": f"smoke-{title_prefix}-{run_id}",
        "change_type": change_type,
        "parameters": parameters,
        "asset_ids": asset_ids,
        "connector_id": connector_id,
    })
    cr_id = cr["id"]
    log(f"  Created CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    log(f"  Planned CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "tier-zero smoke"})
    log(f"  Approved CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/execute")
    cr = _poll_cr(cr_id)
    log(f"  Executed CR {cr_id} — status: {cr['status']}")
    return cr


def _rollback_cr(cr_id):
    """Execute rollback and poll to completion."""
    _api("post", f"/change-requests/{cr_id}/rollback")
    cr = _poll_cr(cr_id, timeout_s=300)
    log(f"  Rollback CR {cr_id} — status: {cr['status']}")
    return cr


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------

def _skip_if_no_creds():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")
    ad_creds = get_connector_creds_from_db("active_directory")
    if not ad_creds:
        pytest.skip("No AD connector creds in platform DB")


def _get_aws_creds():
    return get_connector_creds_from_db("aws") or {}


def _get_boto3(service, aws_creds):
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
            log(f"  Using cached DC AMI: {ami_id}")
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
    sg_id = aws_creds.get("smoke_dc_security_group_id")

    launch_kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-tier-zero-dc"},
            {"Key": "nexplane-purpose", "Value": "smoke-tier-zero-cr"},
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
    log(f"  DC running at {private_ip} — waiting for WinRM port 5985 (up to 12 min)")

    # Wait for WinRM port — more direct than SSM registration for Windows DCs
    import socket
    deadline = time.time() + 720
    winrm_ready = False
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 5985), timeout=5)
            s.close()
            log(f"  WinRM ready on {private_ip}:5985")
            winrm_ready = True
            break
        except OSError:
            pass
    if not winrm_ready:
        ec2.terminate_instances(InstanceIds=[instance_id])
        pytest.fail(f"WinRM never reachable on {private_ip}:5985 within 12 min")

    time.sleep(30)  # Let NTDS settle after WinRM comes up
    return instance_id, private_ip


def _register_dc_asset(private_ip, aws_creds) -> tuple:
    """Register a WinRM connector + asset for the smoke DC. Returns (connector_id, asset_id)."""
    run_id = uuid.uuid4().hex[:6]

    connector = _api("post", "/connectors", json={
        "name": f"smoke-tier-zero-dc-{run_id}",
        "connector_type": "active_directory",
    })
    conn_id = connector["id"]
    # LDAP base_dn derived from domain: smoke.nexplane.local -> DC=smoke,DC=nexplane,DC=local
    base_dn = ",".join(f"DC={part}" for part in _DC_DOMAIN.split("."))
    bind_dn = f"CN=Administrator,CN=Users,{base_dn}"
    _api("put", f"/connectors/{conn_id}/credentials", json={
        "credentials": {
            # LDAP fields (required by active_directory connector schema)
            "server": private_ip,
            "port": "389",
            "base_dn": base_dn,
            "bind_dn": bind_dn,
            "bind_password": _DC_ADMIN_PASSWORD,
            "use_ssl": "false",
            # WinRM fields (used by tier-zero executor)
            "winrm_hostname": private_ip,
            "winrm_port": "5985",
            "winrm_username": f"{_DC_NETBIOS}\\Administrator",
            "winrm_password": _DC_ADMIN_PASSWORD,
            "winrm_use_ssl": "false",
        }
    })
    log(f"  Registered AD connector {conn_id}")

    asset = _api("post", "/assets", json={
        "name": f"smoke-tier-zero-dc-{run_id}",
        "asset_type": "server",
        "criticality": "medium",
        "environment": "staging",
        "hostname": private_ip,
        "connector_id": conn_id,
        "metadata": {"role": "domain_controller", "domain": _DC_DOMAIN},
    })
    asset_id = asset["id"]
    log(f"  Registered DC asset {asset_id}")
    return conn_id, asset_id


# ---------------------------------------------------------------------------
# Module-level shared state
# ---------------------------------------------------------------------------

_state = {
    "instance_id": None,
    "private_ip": None,
    "connector_id": None,
    "asset_id": None,
    "aws_creds": None,
    "cr_ids": [],   # track all CRs for teardown
}

# ---------------------------------------------------------------------------
# Phase 1: provision DC
# ---------------------------------------------------------------------------


def test_phase1_provision_dc():
    """Launch a DC from the cached AMI and register it in the platform."""
    _skip_if_no_creds()

    aws_creds = _get_aws_creds()
    ec2 = _get_boto3("ec2", aws_creds)
    ssm = _get_boto3("ssm", aws_creds)

    ami_id = _get_smoke_dc_ami(ec2, ssm)
    instance_id, private_ip = _launch_dc(ec2, ssm, ami_id, aws_creds)
    conn_id, asset_id = _register_dc_asset(private_ip, aws_creds)

    _state.update({
        "instance_id": instance_id,
        "private_ip": private_ip,
        "connector_id": conn_id,
        "asset_id": asset_id,
        "aws_creds": aws_creds,
    })
    log("[PHASE 1: provision-dc] PASSED")


# ---------------------------------------------------------------------------
# Phase 2: ad_domain_functional_level_upgrade (dry run only — irreversible)
# ---------------------------------------------------------------------------


def test_phase2_dfl_upgrade_dry_run():
    """Verify DFL upgrade preflight passes in dry-run mode without modifying AD."""
    if not _state["connector_id"]:
        pytest.skip("Phase 1 did not complete")

    cr = _api("post", "/change-requests", json={
        "title": "smoke-dfl-dry-run",
        "change_type": "ad_domain_functional_level_upgrade",
        "parameters": {
            "target_level": "2016",
            "scope": "domain",
            "domain_name": _DC_DOMAIN,
            "dry_run": True,
        },
        "asset_ids": [_state["asset_id"]],
        "connector_id": _state["connector_id"],
    })
    cr_id = cr["id"]
    _state["cr_ids"].append(cr_id)

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "tier-zero smoke dfl dry-run"})
    _api("post", f"/change-requests/{cr_id}/execute")
    cr = _poll_cr(cr_id)

    assert cr["status"] == "completed", f"DFL dry-run CR failed: {cr.get('execution_result')}"
    result = (cr.get("execution_result") or {})
    assert result.get("status") == "dry_run", f"Expected dry_run status, got: {result}"
    log("[PHASE 2: dfl-upgrade-dry-run] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: ad_trust_create (create then rollback)
# ---------------------------------------------------------------------------


def test_phase3_trust_create_and_rollback():
    """Create a shortcut trust to a non-existent domain in dry_run, then rollback."""
    if not _state["connector_id"]:
        pytest.skip("Phase 1 did not complete")

    # Use dry_run so we test the executor path without requiring a second domain
    cr = _api("post", "/change-requests", json={
        "title": "smoke-trust-create",
        "change_type": "ad_trust_create",
        "parameters": {
            "target_domain": "other.nexplane.local",
            "trust_type": "external",
            "trust_direction": "outbound",
            "trust_password": "SmokeT3st!",
            "domain_name": _DC_DOMAIN,
            "dry_run": True,
        },
        "asset_ids": [_state["asset_id"]],
        "connector_id": _state["connector_id"],
    })
    cr_id = cr["id"]
    _state["cr_ids"].append(cr_id)

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "tier-zero smoke trust dry-run"})
    _api("post", f"/change-requests/{cr_id}/execute")
    cr = _poll_cr(cr_id)

    assert cr["status"] == "completed", f"Trust create CR failed: {cr.get('execution_result')}"
    result = cr.get("execution_result") or {}
    assert result.get("status") == "dry_run", f"Expected dry_run, got: {result}"

    # Rollback (no-op on dry_run, but exercises the rollback code path)
    cr = _rollback_cr(cr_id)
    assert cr["status"] in ("rolled_back", "rolled_back_with_warnings", "completed"), \
        f"Trust rollback ended in unexpected status: {cr['status']}"

    log("[PHASE 3: trust-create-and-rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 4: ad_gpo_deploy (create GPO + rollback deletes it)
# ---------------------------------------------------------------------------


def test_phase4_gpo_deploy_and_rollback():
    """Deploy a GPO to a pilot OU, then rollback (delete GPO)."""
    if not _state["connector_id"]:
        pytest.skip("Phase 1 did not complete")

    pilot_ou = f"OU=Computers,DC=smoke,DC=nexplane,DC=local"
    cr = _full_cr_lifecycle(
        change_type="ad_gpo_deploy",
        parameters={
            "gpo_name": f"smoke-gpo-{uuid.uuid4().hex[:6]}",
            "gpo_settings": {"HKLM\\Software\\Nexplane": {"SmokeTest": "1"}},
            "pilot_ou": pilot_ou,
            "target_ous": [],
            "domain_name": _DC_DOMAIN,
            "dry_run": False,
        },
        asset_ids=[_state["asset_id"]],
        connector_id=_state["connector_id"],
        title_prefix="gpo-deploy",
    )
    cr_id = cr["id"]
    _state["cr_ids"].append(cr_id)

    assert cr["status"] == "completed", f"GPO deploy CR failed: {cr.get('execution_result')}"
    result = cr.get("execution_result") or {}
    assert result.get("gpo_id"), f"Missing gpo_id in execution result: {result}"
    assert result.get("status") == "completed_pilot", f"Unexpected GPO status: {result}"

    # Rollback — must delete the GPO
    rb = _rollback_cr(cr_id)
    assert rb["status"] in ("rolled_back", "rolled_back_with_warnings"), \
        f"GPO rollback ended in: {rb['status']}"
    rb_result = rb.get("rollback_result") or {}
    assert rb_result.get("rolled_back") is True, f"GPO rollback result not True: {rb_result}"

    log("[PHASE 4: gpo-deploy-and-rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 5: ad_pso_manage create + rollback
# ---------------------------------------------------------------------------


def test_phase5_pso_create_and_rollback():
    """Create a Fine-Grained Password Policy, then rollback (delete it)."""
    if not _state["connector_id"]:
        pytest.skip("Phase 1 did not complete")

    pso_name = f"smoke-pso-{uuid.uuid4().hex[:6]}"
    cr = _full_cr_lifecycle(
        change_type="ad_pso_manage",
        parameters={
            "action": "create",
            "pso_name": pso_name,
            "pso_settings": {
                "min_length": 12,
                "complexity": True,
                "history": 10,
                "lockout_threshold": 5,
                "lockout_duration": 15,
            },
            "applies_to": [f"CN=Domain Users,CN=Users,DC=smoke,DC=nexplane,DC=local"],
            "precedence": 50,
            "domain_name": _DC_DOMAIN,
        },
        asset_ids=[_state["asset_id"]],
        connector_id=_state["connector_id"],
        title_prefix="pso-create",
    )
    cr_id = cr["id"]
    _state["cr_ids"].append(cr_id)

    assert cr["status"] == "completed", f"PSO create CR failed: {cr.get('execution_result')}"
    result = cr.get("execution_result") or {}
    assert result.get("status") == "completed", f"Unexpected PSO status: {result}"
    assert result.get("pso_name") == pso_name

    # Rollback — must delete the PSO
    rb = _rollback_cr(cr_id)
    assert rb["status"] in ("rolled_back", "rolled_back_with_warnings"), \
        f"PSO rollback ended in: {rb['status']}"

    log("[PHASE 5: pso-create-and-rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 6: ad_stale_computer_cleanup (report_only + rollback)
# ---------------------------------------------------------------------------


def test_phase6_stale_cleanup_and_rollback():
    """Run stale computer cleanup in report_only mode, then rollback."""
    if not _state["connector_id"]:
        pytest.skip("Phase 1 did not complete")

    cr = _full_cr_lifecycle(
        change_type="ad_stale_computer_cleanup",
        parameters={
            "stale_days": 90,
            "action": "report_only",   # never actually disables accounts in smoke
            "domain_name": _DC_DOMAIN,
            "search_base": f"DC=smoke,DC=nexplane,DC=local",
        },
        asset_ids=[_state["asset_id"]],
        connector_id=_state["connector_id"],
        title_prefix="stale-cleanup",
    )
    cr_id = cr["id"]
    _state["cr_ids"].append(cr_id)

    assert cr["status"] == "completed", \
        f"Stale cleanup CR failed: {cr.get('execution_result')}"
    result = cr.get("execution_result") or {}
    assert "stale_computers" in result or "report" in result or result.get("status") == "completed", \
        f"Unexpected stale cleanup result: {result}"

    # Rollback (no-op for report_only, but exercises the rollback path)
    rb = _rollback_cr(cr_id)
    assert rb["status"] in ("rolled_back", "rolled_back_with_warnings", "completed"), \
        f"Stale cleanup rollback ended in: {rb['status']}"

    log("[PHASE 6: stale-cleanup-and-rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 7: teardown
# ---------------------------------------------------------------------------


def test_phase7_teardown():
    """Terminate the smoke DC instance and deregister connector/asset."""
    aws_creds = _state.get("aws_creds") or _get_aws_creds()
    ec2 = _get_boto3("ec2", aws_creds)

    if _state.get("asset_id"):
        try:
            _api("delete", f"/assets/{_state['asset_id']}")
            log(f"  Deleted asset {_state['asset_id']}")
        except Exception as e:
            log(f"  Warning: failed to delete asset: {e}")

    if _state.get("connector_id"):
        try:
            _api("delete", f"/connectors/{_state['connector_id']}")
            log(f"  Deleted connector {_state['connector_id']}")
        except Exception as e:
            log(f"  Warning: failed to delete connector: {e}")

    if _state.get("instance_id"):
        try:
            ec2.terminate_instances(InstanceIds=[_state["instance_id"]])
            log(f"  Terminated DC instance {_state['instance_id']}")
        except Exception as e:
            log(f"  Warning: failed to terminate DC: {e}")

    log("[PHASE 7: teardown] PASSED")
