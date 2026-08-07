# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Redis Cluster Migration

Phases:
  1. provision   -- launch Redis 6.2 EC2 from cached AMI (builds+caches if missing)
  2. upgrade     -- CR lifecycle: redis_cluster_migration 6.2->7.2, assert completed
  3. rollback    -- trigger rollback, verify rolled_back=True
  4. teardown    -- terminate instance, deregister connector/asset

AMI cache key: /nexplane/smoke-amis/redis-cluster/6.2
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_redis_cluster_migration.py -v -s
"""

import os
import sys
import socket
import time
import uuid
import base64

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import (
    NexplaneClient,
    log,
    get_connector_creds_from_db,
    install_nexplane_agent_on_instance,
    get_or_create_smoke_ami,
)

BASE_URL  = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL     = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD  = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_REDIS_AMI_CACHE_KEY  = "redis-cluster"
_REDIS_AMI_SETUP_HASH = "6.2"
_SSM_PROFILE          = "nexplane-smoke-ssm"
_SOURCE_VERSION       = "6.2"
_TARGET_VERSION       = "7.2"
_REDIS_PORT           = 6379

CR_TIMEOUT    = 1800
POLL_INTERVAL = 20

_state = {
    "connector_id":      None,
    "asset_id":          None,
    "instance_id":       None,
    "private_ip":        None,
    "provisioned_by_us": False,
    "cr_id":             None,
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


def _launch_redis(aws_creds) -> tuple:
    """Provision an EC2 instance with Redis 6.2 installed and running.

    Used as launch_fn for get_or_create_smoke_ami. Returns (instance_id, ec2_client, None).
    """
    ec2       = _boto3_client("ec2", aws_creds)
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    user_data_script = (
        "#!/bin/bash\n"
        "amazon-linux-extras install redis6 -y\n"
        "systemctl enable redis\n"
        "systemctl start redis\n"
        "for i in $(seq 1 30); do\n"
        "    if redis-cli ping 2>/dev/null | grep -q PONG; then\n"
        "        break\n"
        "    fi\n"
        "    sleep 2\n"
        "done\n"
    )
    user_data = base64.b64encode(user_data_script.encode()).decode()

    kwargs = dict(
        ImageId="ami-0c02fb55956c7d316",
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-redis-build"},
            {"Key": "nexplane-purpose", "Value": "smoke-redis-ami-build"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Redis build instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for Redis port {_REDIS_PORT} on {private_ip} (up to 10 min)")
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = socket.create_connection((private_ip, _REDIS_PORT), timeout=5)
            s.close()
            log(f"  Redis port {_REDIS_PORT} open on {private_ip}")
            return instance_id, ec2, None
        except OSError:
            pass

    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"Redis never reachable on {private_ip}:{_REDIS_PORT} within 10 min")


def _launch_from_ami(ami_id, aws_creds) -> tuple:
    """Launch a smoke run instance from the cached AMI; wait for Redis to be reachable."""
    ec2       = _boto3_client("ec2", aws_creds)
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    user_data = base64.b64encode(b"#!/bin/bash\nsystemctl start redis || true\n").decode()

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-redis-migration"},
            {"Key": "nexplane-purpose", "Value": "smoke-redis-cluster-migration"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Redis smoke instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for Redis port {_REDIS_PORT} on {private_ip} (up to 10 min)")
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = socket.create_connection((private_ip, _REDIS_PORT), timeout=5)
            s.close()
            log(f"  Redis port {_REDIS_PORT} open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass

    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"Redis never reachable on {private_ip}:{_REDIS_PORT} within 10 min")


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-redis-migration-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-redis-migration-{run_id}",
        "asset_type":   "server",
        "criticality":  "high",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "redis", "ip": private_ip},
    })
    asset_id = asset["id"]
    _api("post", f"/assets/{asset_id}/connectors", json={"connector_id": conn_id})
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
        cache_key=_REDIS_AMI_CACHE_KEY,
        setup_hash=_REDIS_AMI_SETUP_HASH,
        launch_fn=_launch_redis,
        snapshot_name="nexplane-smoke-redis-6.2",
    )
    log(f"  Using Redis AMI: {ami_id}")

    instance_id, private_ip = _launch_from_ami(ami_id, aws_creds)
    run_id                   = uuid.uuid4().hex[:6]
    conn_id, asset_id        = _register_asset(private_ip, run_id)

    _state.update({
        "connector_id":      conn_id,
        "asset_id":          asset_id,
        "instance_id":       instance_id,
        "private_ip":        private_ip,
        "provisioned_by_us": True,
    })

    log("  Installing nexplane agent on smoke instance")
    install_nexplane_agent_on_instance(instance_id, asset_id, aws_creds, private_ip=private_ip, timeout_s=300)

    log("[PHASE 1: provision] PASSED")


# ---------------------------------------------------------------------------
# Phase 2: migration CR lifecycle
# ---------------------------------------------------------------------------

def test_phase2_upgrade():
    if not _state.get("connector_id"):
        pytest.skip("Phase 1 did not complete")

    conn_id    = _state["connector_id"]
    asset_id   = _state["asset_id"]
    private_ip = _state["private_ip"]
    run_id     = uuid.uuid4().hex[:6]

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-redis-migration-{run_id}",
        "change_type": "redis_cluster_migration",
        "desired_outcome": {
            "source_version": _SOURCE_VERSION,
            "target_version": _TARGET_VERSION,
            "nodes": [{"host": private_ip, "port": _REDIS_PORT}],
            "dry_run": False,
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
         json={"decision": "approved", "comment": "redis migration smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} -- expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"
    assert result.get("target_version") == _TARGET_VERSION, (
        f"target_version mismatch: {result}"
    )

    log(f"  Redis migrated to {_TARGET_VERSION}")
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

    cr = _poll_cr(cr_id, timeout_s=CR_TIMEOUT)
    assert cr["status"] in ("rolled_back", "completed"), (
        f"Rollback reached unexpected status: {cr['status']}"
    )

    result = _rollback_result(cr)
    assert result.get("rolled_back") is True, f"Rollback result: {result}"

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
