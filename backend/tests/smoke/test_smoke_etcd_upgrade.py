# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: etcd Upgrade

Phases:
  1. provision   -- launch etcd 3.4 EC2 from cached AMI (or build on miss), install nexplane agent
  2. upgrade     -- CR lifecycle: etcd_upgrade 3.4->3.5, assert completed + version
  3. rollback    -- trigger rollback, verify rolled_back=True
  4. teardown    -- terminate instance, deregister connector/asset

AMI cache key: /nexplane/smoke-amis/etcd/3.4
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_etcd_upgrade.py -v -s
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
from smoke_helpers import (
    NexplaneClient,
    get_connector_creds_from_db,
    get_or_create_smoke_ami,
    install_nexplane_agent_on_instance,
    log,
)

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL    = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_ETCD_AMI_SSM_KEY = "/nexplane/smoke-amis/etcd/3.4"
_SSM_PROFILE      = "nexplane-smoke-ssm"
_SOURCE_VERSION   = "3.4"
_TARGET_VERSION   = "3.5"

CR_TIMEOUT    = 900
POLL_INTERVAL = 15

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


def _build_etcd_user_data() -> str:
    """Build base64-encoded user-data script that installs etcd 3.4 on AL2."""
    script_lines = [
        "#!/bin/bash",
        "set -e",
        "ETCD_VER=v3.4.34",
        "curl -sfL https://github.com/etcd-io/etcd/releases/download/${ETCD_VER}/etcd-${ETCD_VER}-linux-amd64.tar.gz"
        " | tar -xz -C /usr/local/bin --strip-components=1"
        " etcd-${ETCD_VER}-linux-amd64/etcd"
        " etcd-${ETCD_VER}-linux-amd64/etcdctl",
        "mkdir -p /var/lib/etcd /etc/etcd",
        "printf '[Unit]\\nDescription=etcd\\n[Service]\\n"
        "ExecStart=/usr/local/bin/etcd --data-dir /var/lib/etcd"
        " --listen-client-urls http://0.0.0.0:2379"
        " --advertise-client-urls http://localhost:2379\\n"
        "Restart=always\\n[Install]\\nWantedBy=multi-user.target\\n'"
        " > /etc/systemd/system/etcd.service",
        "systemctl daemon-reload && systemctl enable etcd && systemctl start etcd",
    ]
    script = "\n".join(script_lines) + "\n"
    return base64.b64encode(script.encode()).decode()


def _launch_etcd(aws_creds: dict):
    """Provision a t3.small AL2 instance with etcd 3.4 installed and running."""
    ec2       = _boto3_client("ec2", aws_creds)
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    # Resolve latest Amazon Linux 2 AMI
    resp = ec2.describe_images(
        Filters=[
            {"Name": "name",                "Values": ["amzn2-ami-hvm-2.0.*-x86_64-gp2"]},
            {"Name": "owner-alias",         "Values": ["amazon"]},
            {"Name": "state",               "Values": ["available"]},
            {"Name": "architecture",        "Values": ["x86_64"]},
            {"Name": "virtualization-type", "Values": ["hvm"]},
        ]
    )
    images = sorted(resp["Images"], key=lambda i: i["CreationDate"], reverse=True)
    if not images:
        pytest.fail("Could not find Amazon Linux 2 AMI")
    base_ami = images[0]["ImageId"]
    log(f"  Base AMI: {base_ami}")

    kwargs = dict(
        ImageId=base_ami,
        InstanceType="t3.small",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=_build_etcd_user_data(),
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-etcd-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-etcd-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched etcd instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    # Wait for etcd client port 2379
    log(f"  Waiting for etcd port 2379 on {private_ip} (up to 15 min)")
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 2379), timeout=5)
            s.close()
            log(f"  etcd port 2379 open on {private_ip}")
            return instance_id, ec2, private_ip
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"etcd never reachable on {private_ip}:2379 within 15 min")


def _register_asset(private_ip: str, run_id: str) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-etcd-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-etcd-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "etcd_node", "ip": private_ip},
    })
    asset_id = asset["id"]
    _api("post", f"/assets/{asset_id}/connectors", json={"connector_id": conn_id})
    return conn_id, asset_id


def _poll_cr(cr_id: str, timeout_s: int = CR_TIMEOUT) -> dict:
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

    def _launch_fn(creds):
        return _launch_etcd(creds)

    ami_id = get_or_create_smoke_ami(
        cache_key="etcd/3.4",
        setup_hash="3.4.34",
        launch_fn=_launch_fn,
        snapshot_name="nexplane-smoke-etcd-3.4",
    )
    log(f"  Using etcd AMI: {ami_id}")

    # Launch instance from (possibly newly cached) AMI
    instance_id, _ec2, private_ip = _launch_etcd(aws_creds)
    run_id            = uuid.uuid4().hex[:6]
    conn_id, asset_id = _register_asset(private_ip, run_id)

    _state.update({
        "connector_id":      conn_id,
        "asset_id":          asset_id,
        "instance_id":       instance_id,
        "private_ip":        private_ip,
        "provisioned_by_us": True,
    })

    log("  Installing nexplane agent on smoke instance")
    install_nexplane_agent_on_instance(
        instance_id, asset_id, aws_creds, private_ip=private_ip, timeout_s=300
    )

    log("[PHASE 1: provision] PASSED")


# ---------------------------------------------------------------------------
# Phase 2: upgrade CR lifecycle
# ---------------------------------------------------------------------------

def test_phase2_upgrade():
    if not _state.get("connector_id"):
        pytest.skip("Phase 1 did not complete")

    conn_id  = _state["connector_id"]
    asset_id = _state["asset_id"]
    run_id   = uuid.uuid4().hex[:6]

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-etcd-upgrade-{run_id}",
        "change_type": "etcd_upgrade",
        "desired_outcome": {
            "source_version": _SOURCE_VERSION,
            "target_version": _TARGET_VERSION,
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
         json={"decision": "approved", "comment": "etcd upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} -- expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"
    assert "3.5" in result.get("target_version", ""), (
        f"Expected target_version containing '3.5', got: {result.get('target_version')!r}"
    )

    log(f"  etcd upgraded to {result.get('target_version')}")
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

    for res_id, path in [
        (asset_id, f"/assets/{asset_id}"),
        (conn_id,  f"/connectors/{conn_id}"),
    ]:
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
