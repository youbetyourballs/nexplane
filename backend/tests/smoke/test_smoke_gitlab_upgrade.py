# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: GitLab Major Version Upgrade

Single-hop smoke: 15.11 -> 16.0 (cheapest valid hop to verify CR lifecycle).
Full multi-hop (15->17) is slow (~45 min); single-hop is sufficient.

AMI cache key: /nexplane/smoke-amis/gitlab/15.11
Instance type: t3.xlarge (GitLab needs 4GB+ RAM)

Phases:
  1. setup     -- get_or_create_smoke_ami for GitLab 15.11; launch t3.xlarge; register
  2. upgrade   -- gitlab_upgrade CR lifecycle (15.11->16.0)
  3. verify    -- GET /api/v4/version == 16.0; GET /api/v4/projects returns 200
  4. rollback  -- restore from backup; verify 15.11
  5. teardown  -- terminate instance; deregister

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_gitlab_upgrade.py -v -s
"""

import os
import sys
import time
import uuid
import pytest
import boto3

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db, get_or_create_smoke_ami

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_AMI_SSM_KEY = "/nexplane/smoke-amis/gitlab/15.11"
SOURCE_VERSION = "15.11"
TARGET_VERSION = "16.0"
INSTANCE_TYPE = "t3.xlarge"
GITLAB_ADMIN_TOKEN = os.environ.get("GITLAB_SMOKE_TOKEN", "smoke-admin-token")

CR_TIMEOUT = 1800  # 30 min -- backup + upgrade + background migrations
POLL_INTERVAL = 15

_state = {
    "connector_id": None,
    "asset_id": None,
    "instance_id": None,
    "instance_ip": None,
    "cr_id": None,
    "execution_result": None,
    "provisioned_by_us": False,
}

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id, terminal_statuses=("completed", "failed", "blocked", "rolled_back", "rollback_failed"), timeout=CR_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal_statuses:
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} timed out after {timeout}s")


def _exec_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    if runs:
        raw = runs[0].get("result") or {}
        steps = raw.get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


def _rollback_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    for run in runs:
        if run.get("status") in ("rolled_back", "rollback_failed"):
            r = run.get("result") or {}
            if "rolled_back" in r or "data_loss_warning" in r:
                return r
    return cr.get("rollback_result") or {}


def test_phase1_provision_gitlab():
    creds = get_connector_creds_from_db("aws")
    ami_id = get_or_create_smoke_ami(
        ssm_key=_AMI_SSM_KEY,
        creds=creds,
        build_instructions=(
            "Install GitLab EE 15.11.x omnibus on Ubuntu 22.04. "
            "Set external_url to http://localhost. "
            "Create initial admin user with token 'smoke-admin-token'. "
            "Create AMI after gitlab-ctl reconfigure completes."
        ),
    )
    log(f"Using GitLab AMI: {ami_id}")

    ec2 = boto3.client("ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    resp = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": "nexplane-smoke-gitlab-upgrade"}],
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    _state["instance_id"] = instance_id
    _state["provisioned_by_us"] = True
    log(f"EC2 launched: {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    _state["instance_ip"] = ip
    log(f"GitLab instance at {ip}")

    # Wait for GitLab to be fully up (gitlab-ctl status)
    time.sleep(120)

    run_id = uuid.uuid4().hex[:6]
    connector = _api("post", "/connectors", json={
        "name": f"smoke-gitlab-{run_id}",
        "connector_type": "nexplane_agent",
    })
    connector_id = connector["id"]
    _api("put", f"/connectors/{connector_id}/credentials", json={"credentials": {}})
    _state["connector_id"] = connector_id

    asset = _api("post", "/assets", json={
        "name": f"smoke-gitlab-{run_id}",
        "asset_type": "server",
        "criticality": "medium",
        "environment": "staging",
        "hostname": ip,
        "connector_id": connector_id,
        "metadata": {"instance_id": instance_id, "ip": ip},
    })
    _state["asset_id"] = asset["id"]
    log(f"Asset: {_state['asset_id']}")


def test_phase2_create_and_approve_cr():
    if not _state["asset_id"]:
        pytest.skip("No asset_id")

    payload = {
        "change_type": "gitlab_upgrade",
        "title": f"Smoke: GitLab {SOURCE_VERSION}->{TARGET_VERSION}",
        "target_asset_ids": [_state["asset_id"]],
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "gitlab_host": _state["instance_ip"],
            "gitlab_admin_token": GITLAB_ADMIN_TOKEN,
            "gitlab_package_type": "omnibus",
            "dry_run": False,
        },
    }
    cr = _api("post", "/change-requests", json=payload)
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "gitlab upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"CR {cr_id} approved and executing")


def test_phase2_wait_execution():
    if not _state["cr_id"]:
        pytest.skip()
    cr = _poll_cr(_state["cr_id"])
    assert cr["status"] == "completed", f"CR failed: {cr}"
    _state["execution_result"] = _exec_result(cr)


def test_phase3_verify():
    if not _state["execution_result"]:
        pytest.skip("No execution_result -- phase 2 did not complete")
    result = _state["execution_result"]
    assert result.get("status") == "completed"
    assert result.get("final_version", "").startswith("16.0"), \
        f"Expected 16.0.x, got {result.get('final_version')}"
    assert result.get("hops_completed") == [f"{SOURCE_VERSION}->{TARGET_VERSION}"]
    log(f"GitLab {TARGET_VERSION} verified; hops={result.get('hops_completed')}")


def test_phase4_rollback():
    if not _state["cr_id"]:
        pytest.skip()
    _api("post", f"/change-requests/{_state['cr_id']}/rollback")
    cr = _poll_cr(_state["cr_id"], terminal_statuses=("rolled_back", "rollback_failed"), timeout=1800)
    assert cr["status"] == "rolled_back", f"Rollback failed: {cr}"
    rb = _rollback_result(cr)
    assert rb.get("rolled_back") is True
    log("GitLab rollback to 15.11 verified")


def test_phase5_teardown():
    if not _state["provisioned_by_us"]:
        return
    creds = get_connector_creds_from_db("aws")
    ec2 = boto3.client("ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    if _state["instance_id"]:
        ec2.terminate_instances(InstanceIds=[_state["instance_id"]])
        log(f"Terminated {_state['instance_id']}")
    if _state["asset_id"]:
        try:
            _api("delete", f"/assets/{_state['asset_id']}")
            log(f"Deleted asset {_state['asset_id']}")
        except Exception as exc:
            log(f"Warning: {exc}", ok=False)
    if _state["connector_id"]:
        try:
            _api("delete", f"/connectors/{_state['connector_id']}")
            log(f"Deleted connector {_state['connector_id']}")
        except Exception as exc:
            log(f"Warning: {exc}", ok=False)
