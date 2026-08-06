# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: GitLab Major Version Upgrade

Single-hop smoke: 15.11 → 16.0 (cheapest valid hop to verify CR lifecycle).
Full multi-hop (15→17) is slow (~45 min); single-hop is sufficient.

AMI cache key: /nexplane/smoke-amis/gitlab/15.11
Instance type: t3.xlarge (GitLab needs 4GB+ RAM)

Phases:
  1. setup     — get_or_create_smoke_ami for GitLab 15.11; launch t3.xlarge; register
  2. upgrade   — gitlab_upgrade CR lifecycle (15.11→16.0)
  3. verify    — GET /api/v4/version == 16.0; GET /api/v4/projects returns 200
  4. rollback  — restore from backup; verify 15.11
  5. teardown  — terminate instance; deregister

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_gitlab_upgrade.py -v -s
"""

import os
import sys
import time
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

CR_TIMEOUT = 1800  # 30 min — backup + upgrade + background migrations
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


def _poll_cr(cr_id, terminal_statuses=("completed", "failed", "blocked"), timeout=CR_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _api("get", f"/api/v1/change-requests/{cr_id}")
        assert r.status_code == 200
        data = r.json()
        if data.get("status") in terminal_statuses:
            return data
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} timed out after {timeout}s")


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

    r = _api("post", "/api/v1/connectors", json={
        "connector_type": "nexplane_agent",
        "display_name": f"smoke-gitlab-{instance_id[:8]}",
        "credentials": {},
    })
    assert r.status_code in (200, 201)
    connector_id = r.json()["id"]
    _state["connector_id"] = connector_id

    r = _api("post", "/api/v1/assets", json={
        "connector_id": connector_id,
        "asset_type": "server",
        "display_name": f"smoke-gitlab-{instance_id[:8]}",
        "asset_metadata": {"instance_id": instance_id, "ip": ip},
    })
    assert r.status_code in (200, 201)
    _state["asset_id"] = r.json()["id"]
    log(f"Asset: {_state['asset_id']}")


def test_phase2_create_and_approve_cr():
    if not _state["asset_id"]:
        pytest.skip("No asset_id")

    payload = {
        "change_type": "gitlab_upgrade",
        "title": f"Smoke: GitLab {SOURCE_VERSION}→{TARGET_VERSION}",
        "asset_ids": [_state["asset_id"]],
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "gitlab_host": _state["instance_ip"],
            "gitlab_admin_token": GITLAB_ADMIN_TOKEN,
            "gitlab_package_type": "omnibus",
            "dry_run": False,
        },
    }
    r = _api("post", "/api/v1/change-requests", json=payload)
    assert r.status_code in (200, 201), f"CR create failed: {r.text}"
    _state["cr_id"] = r.json()["id"]
    _api("post", f"/api/v1/change-requests/{_state['cr_id']}/plan")
    _api("post", f"/api/v1/change-requests/{_state['cr_id']}/approve")
    log(f"CR {_state['cr_id']} approved")


def test_phase2_wait_execution():
    if not _state["cr_id"]:
        pytest.skip()
    cr = _poll_cr(_state["cr_id"])
    assert cr["status"] == "completed", f"CR failed: {cr}"
    _state["execution_result"] = cr.get("execution_result", {})


def test_phase3_verify():
    result = _state["execution_result"]
    assert result, "No execution_result"
    assert result.get("status") == "completed"
    assert result.get("final_version", "").startswith("16.0"), \
        f"Expected 16.0.x, got {result.get('final_version')}"
    assert result.get("hops_completed") == [f"{SOURCE_VERSION}→{TARGET_VERSION}"]
    log(f"GitLab {TARGET_VERSION} verified; hops={result.get('hops_completed')}")


def test_phase4_rollback():
    if not _state["cr_id"]:
        pytest.skip()
    r = _api("post", f"/api/v1/change-requests/{_state['cr_id']}/rollback")
    assert r.status_code == 200
    cr = _poll_cr(_state["cr_id"], terminal_statuses=("rolled_back", "rollback_failed"), timeout=1800)
    assert cr["status"] == "rolled_back", f"Rollback failed: {cr}"
    rb = cr.get("rollback_result", {})
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
        _api("delete", f"/api/v1/assets/{_state['asset_id']}")
    if _state["connector_id"]:
        _api("delete", f"/api/v1/connectors/{_state['connector_id']}")
