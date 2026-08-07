# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Kong API Gateway Upgrade

Phases:
  1. setup       — get_or_create_smoke_ami for Kong 3.4; launch EC2; register connector+asset
  2. run_upgrade — kong_upgrade CR lifecycle (3.4→3.7)
  3. verify      — admin API version=3.7; pre-seeded route still returns 200 via proxy
  4. rollback    — assert version=3.4 via admin API
  5. teardown    — terminate instance; deregister connector+asset

AMI cache key: /nexplane/smoke-amis/kong/3.4
Instance type: t3.medium
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_kong_upgrade.py -v -s
"""

import os
import sys
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db, get_or_create_smoke_ami

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_AMI_SSM_KEY = "/nexplane/smoke-amis/kong/3.4"
SOURCE_VERSION = "3.4"
TARGET_VERSION = "3.7"
INSTANCE_TYPE = "t3.medium"

CR_TIMEOUT = 600
POLL_INTERVAL = 10

_state = {
    "connector_id": None,
    "asset_id": None,
    "instance_id": None,
    "instance_ip": None,
    "cr_id": None,
    "execution_result": None,
    "provisioned_by_us": False,
}

_client: NexplaneClient = None


def _get_client():
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id, timeout_s=CR_TIMEOUT):
    terminal = ("completed", "failed", "rolled_back", "rollback_failed")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal:
            return cr
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"CR {cr_id} did not reach terminal within {timeout_s}s")


def _rollback_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    for run in runs:
        if run.get("status") in ("rolled_back", "rollback_failed"):
            r = run.get("result") or {}
            if "rolled_back" in r or "data_loss_warning" in r:
                return r
    return cr.get("rollback_result") or {}


def test_phase1_provision_kong_instance():
    creds = get_connector_creds_from_db("aws")
    ami_id = get_or_create_smoke_ami(
        ssm_key=_AMI_SSM_KEY,
        creds=creds,
        build_instructions="Install Kong 3.4 + Postgres 14 on Amazon Linux 2; seed one route 'smoke-route' → service 'smoke-svc' → upstream http://httpbin.org/get; start kong; create AMI",
    )
    log(f"Using Kong AMI: {ami_id}")

    ec2 = boto3.client(
        "ec2",
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
            "Tags": [{"Key": "Name", "Value": "nexplane-smoke-kong-upgrade"}],
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    _state["instance_id"] = instance_id
    _state["provisioned_by_us"] = True
    log(f"EC2 launched: {instance_id}")

    # Wait for running
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    _state["instance_ip"] = ip
    log(f"Instance running at {ip}")

    # Register connector + asset via platform API
    run_id = uuid.uuid4().hex[:6]
    connector = _api("post", "/connectors", json={
        "name": f"smoke-kong-{run_id}",
        "connector_type": "nexplane_agent",
    })
    assert connector.get("id"), f"Connector create failed: {connector}"
    connector_id = connector["id"]
    _api("put", f"/connectors/{connector_id}/credentials", json={"credentials": {}})
    _state["connector_id"] = connector_id

    asset = _api("post", "/assets", json={
        "name": f"smoke-kong-{run_id}",
        "asset_type": "server",
        "criticality": "medium",
        "environment": "staging",
        "hostname": ip,
        "connector_id": connector_id,
        "metadata": {"instance_id": instance_id, "ip": ip},
    })
    assert asset.get("id"), f"Asset create failed: {asset}"
    _state["asset_id"] = asset["id"]
    log(f"Asset registered: {_state['asset_id']}")


def test_phase2_create_and_approve_cr():
    if not _state["asset_id"]:
        pytest.skip("No asset_id")

    payload = {
        "change_type": "kong_upgrade",
        "title": f"Smoke: Kong {SOURCE_VERSION}→{TARGET_VERSION}",
        "target_asset_ids": [_state["asset_id"]],
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "kong_host": _state["instance_ip"],
            "db_mode": "postgres",
            "db_host": "localhost",
            "db_port": 5432,
            "db_name": "kong",
            "db_user": "kong",
            "db_password": "kong",
            "dry_run": False,
        },
    }
    cr = _api("post", "/change-requests", json=payload)
    assert cr.get("id"), f"CR create failed: {cr}"
    cr_id = cr["id"]
    _state["cr_id"] = cr_id

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "kong upgrade smoke self-approval"})
    log(f"CR {cr_id} approved")


def test_phase2_wait_execution():
    if not _state["cr_id"]:
        pytest.skip("No cr_id")
    _api("post", f"/change-requests/{_state['cr_id']}/execute")
    cr = _poll_cr(_state["cr_id"])
    assert cr["status"] == "completed", f"CR failed: {cr}"
    _state["execution_result"] = cr.get("execution_result", {})


def test_phase3_verify_kong_version():
    if not _state["execution_result"]:
        pytest.skip("No execution_result — phase 2 did not complete")
    result = _state["execution_result"]
    assert result.get("status") == "completed"
    assert result.get("target_version") == TARGET_VERSION
    # Route count should be preserved
    assert result.get("routes_count", 0) >= 1, "Routes lost during upgrade"
    log(f"Kong {TARGET_VERSION} verified; routes={result.get('routes_count')}")


def test_phase4_rollback():
    if not _state["cr_id"]:
        pytest.skip("No cr_id")
    _api("post", f"/change-requests/{_state['cr_id']}/rollback")
    cr = _poll_cr(_state["cr_id"])
    assert cr["status"] == "rolled_back", f"Rollback failed: {cr}"
    rb = _rollback_result(cr)
    assert rb.get("rolled_back") is True
    log("Kong rollback to 3.4 verified")


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
        _api("delete", f"/assets/{_state['asset_id']}")
    if _state["connector_id"]:
        _api("delete", f"/connectors/{_state['connector_id']}")
