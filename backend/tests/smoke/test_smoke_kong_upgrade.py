# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Kong API Gateway Upgrade

Phases:
  1. provision   -- launch Kong 3.4 EC2 from cached AMI (auto-provisioned via launch_fn);
                    register connector + asset
  2. upgrade     -- CR lifecycle: kong_upgrade 3.4->3.7, assert completed
  3. rollback    -- trigger rollback, assert rolled_back
  4. teardown    -- terminate instance, deregister connector/asset

AMI cache key: /nexplane/smoke-amis/kong/3.4
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_kong_upgrade.py -v -s
"""

import base64
import os
import socket
import sys
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db, get_or_create_smoke_ami

BASE_URL  = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL     = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD  = os.environ.get("NEXPLANE_PASSWORD", "admin123")

SOURCE_VERSION = "3.4"
TARGET_VERSION = "3.7"
_SSM_PROFILE   = "nexplane-smoke-ssm"

CR_TIMEOUT    = 1800
POLL_INTERVAL = 15

_state = {
    "connector_id":      None,
    "asset_id":          None,
    "instance_id":       None,
    "private_ip":        None,
    "cr_id":             None,
    "provisioned_by_us": False,
}

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


_KONG_USER_DATA = (
    b"#!/bin/bash\n"
    b"amazon-linux-extras install postgresql14 -y\n"
    b"yum install -y postgresql-server\n"
    b"postgresql-setup initdb\n"
    b"systemctl enable postgresql && systemctl start postgresql\n"
    b"sudo -u postgres psql -c \"CREATE USER kong WITH PASSWORD 'kong';\"\n"
    b"sudo -u postgres psql -c \"CREATE DATABASE kong OWNER kong;\"\n"
    b"curl -sfL https://packages.konghq.com/public/gateway-34/rpm/amzn/2/x86_64/kong-3.4.2.1.aws.amd64.rpm"
    b" -o /tmp/kong.rpm\n"
    b"yum install -y /tmp/kong.rpm\n"
    b"cp /etc/kong/kong.conf.default /etc/kong/kong.conf\n"
    b"sed -i 's/#database = off/database = postgres/' /etc/kong/kong.conf\n"
    b"sed -i 's/#pg_host = 127.0.0.1/pg_host = 127.0.0.1/' /etc/kong/kong.conf\n"
    b"sed -i 's/#pg_user = kong/pg_user = kong/' /etc/kong/kong.conf\n"
    b"sed -i 's/#pg_password =/pg_password = kong/' /etc/kong/kong.conf\n"
    b"sed -i 's/#pg_database = kong/pg_database = kong/' /etc/kong/kong.conf\n"
    b"kong migrations bootstrap\n"
    b"systemctl enable kong && systemctl start kong\n"
)


def _launch_kong_ami(aws_creds) -> tuple:
    ec2       = _boto3_client("ec2", aws_creds)
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    user_data = base64.b64encode(_KONG_USER_DATA).decode()

    kwargs = dict(
        ImageId="ami-0c101f26f147fa7fd",
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-kong-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-kong-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Kong instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for Kong admin port 8001 on {private_ip} (up to 600s)")
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 8001), timeout=5)
            s.close()
            log(f"  Kong admin port 8001 open on {private_ip}")
            return instance_id, ec2, None
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"Kong admin API never reachable on {private_ip}:8001 within 600s")


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
        raw   = runs[0].get("result") or {}
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


# ---------------------------------------------------------------------------
# Phase 1: provision
# ---------------------------------------------------------------------------

def test_phase1_provision():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")

    ami_id = get_or_create_smoke_ami(
        cache_key="/nexplane/smoke-amis/kong/3.4",
        setup_hash="34",
        launch_fn=_launch_kong_ami,
        snapshot_name="nexplane-smoke-kong-3.4",
    )
    log(f"  Using Kong AMI: {ami_id}")

    ec2 = _boto3_client("ec2", aws_creds)

    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-kong-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-kong-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    _state["instance_id"]       = instance_id
    _state["provisioned_by_us"] = True
    log(f"  Launched Kong instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    _state["private_ip"] = private_ip
    log(f"  Kong instance at {private_ip}")

    log(f"  Waiting for Kong admin port 8001 on {private_ip}")
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 8001), timeout=5)
            s.close()
            log(f"  Kong admin port 8001 reachable")
            break
        except OSError:
            pass
    else:
        ec2.terminate_instances(InstanceIds=[instance_id])
        pytest.fail(f"Kong admin API never reachable on {private_ip}:8001 within 600s")

    run_id    = uuid.uuid4().hex[:6]
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-kong-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-kong-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"instance_id": instance_id, "ip": private_ip},
    })
    asset_id = asset["id"]
    _api("post", f"/assets/{asset_id}/connectors", json={"connector_id": conn_id})

    _state["connector_id"] = conn_id
    _state["asset_id"]     = asset_id
    log(f"  Registered connector {conn_id}, asset {asset_id}")
    log("[PHASE 1: provision] PASSED")


# ---------------------------------------------------------------------------
# Phase 2: upgrade CR lifecycle
# ---------------------------------------------------------------------------

def test_phase2_upgrade():
    if not _state.get("connector_id"):
        pytest.skip("Phase 1 did not complete")

    conn_id    = _state["connector_id"]
    asset_id   = _state["asset_id"]
    private_ip = _state["private_ip"]
    run_id     = uuid.uuid4().hex[:6]

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-kong-upgrade-{run_id}",
        "change_type": "kong_upgrade",
        "desired_outcome": {
            "source_version": SOURCE_VERSION,
            "target_version": TARGET_VERSION,
            "kong_host":      private_ip,
            "db_mode":        "postgres",
            "db_host":        "localhost",
            "db_port":        5432,
            "db_name":        "kong",
            "db_user":        "kong",
            "db_password":    "kong",
            "dry_run":        False,
        },
        "connector_id":     conn_id,
        "target_asset_ids": [asset_id],
    })
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    log(f"  Created CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "kong upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} -- expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"
    assert result.get("target_version") == TARGET_VERSION, (
        f"target_version mismatch: {result}"
    )
    assert result.get("routes_count", 0) >= 1, "Routes lost during upgrade"
    log(f"  Kong upgraded to {TARGET_VERSION}; routes={result.get('routes_count')}")
    log("[PHASE 2: upgrade] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: rollback
# ---------------------------------------------------------------------------

def test_phase3_rollback():
    cr_id = _state.get("cr_id")
    if not cr_id:
        pytest.skip("Phase 2 did not complete -- no CR to roll back")

    _api("post", f"/change-requests/{cr_id}/rollback")
    log(f"  Rollback triggered for CR {cr_id}")

    cr = _poll_cr(cr_id, timeout_s=1800)
    assert cr["status"] in ("rolled_back", "completed"), (
        f"Rollback CR reached unexpected status: {cr['status']}"
    )

    result = _rollback_result(cr)
    assert result.get("rolled_back") is True or cr["status"] == "rolled_back", (
        f"Rollback result: {result}"
    )
    log("[PHASE 3: rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 4: teardown
# ---------------------------------------------------------------------------

def test_phase4_teardown():
    if not _state.get("provisioned_by_us"):
        log("[PHASE 4: teardown] SKIPPED")
        return

    aws_creds   = get_connector_creds_from_db("aws")
    asset_id    = _state.get("asset_id")
    conn_id     = _state.get("connector_id")
    instance_id = _state.get("instance_id")

    for res_id, path in [(asset_id, f"/assets/{asset_id}"), (conn_id, f"/connectors/{conn_id}")]:
        if res_id:
            try:
                _api("delete", path)
                log(f"  Deleted {path}")
            except Exception as exc:
                log(f"  Warning: {exc}", ok=False)

    if instance_id and aws_creds:
        try:
            ec2 = _boto3_client("ec2", aws_creds)
            ec2.terminate_instances(InstanceIds=[instance_id])
            log(f"  Terminated instance {instance_id}")
        except Exception as exc:
            log(f"  Warning: could not terminate: {exc}", ok=False)

    log("[PHASE 4: teardown] PASSED")
