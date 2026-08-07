# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: CockroachDB Cluster Upgrade (23.1 -> 23.2)

Phases:
  1. provision   — launch AL2 EC2 with CockroachDB 23.1 from cached AMI
  2. upgrade     — CR lifecycle: cockroachdb_cluster_upgrade, assert status completed + target_version 23.2
  3. rollback    — trigger rollback, assert rolled_back=True
  4. teardown    — terminate instance, deregister connector/asset

AMI cache key: /nexplane/smoke-amis/cockroachdb/23.1
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_cockroachdb_cluster_upgrade.py -v -s
"""

import os
import sys
import socket
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import (
    NexplaneClient,
    log,
    get_connector_creds_from_db,
    get_or_create_smoke_ami,
    install_nexplane_agent_on_instance,
)

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL    = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_CRDB_AMI_CACHE_KEY = "cockroachdb/23.1"
_SSM_PROFILE        = "nexplane-smoke-ssm"

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


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-cockroachdb-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-cockroachdb-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "cockroachdb_node", "ip": private_ip},
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


def _launch_crdb_from_ami(ec2, ami_id, aws_creds) -> tuple:
    """Launch the smoke test instance from the pre-baked CockroachDB 23.1 AMI."""
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    import base64
    user_data = base64.b64encode(b"#!/bin/bash\nsystemctl start cockroachdb || true\n").decode()

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-cockroachdb-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-cockroachdb-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched CockroachDB smoke instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for CockroachDB port 26257 on {private_ip} (up to 120s)")
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = socket.create_connection((private_ip, 26257), timeout=5)
            s.close()
            log(f"  CockroachDB port 26257 open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"CockroachDB port 26257 never reachable on {private_ip} within 120s")


# ---------------------------------------------------------------------------
# Phase 1: provision
# ---------------------------------------------------------------------------

_CRDB_INSTALL_SCRIPT = (
    b"#!/bin/bash\n"
    b"set -e\n"
    b"curl -sfL https://binaries.cockroachdb.com/cockroach-v23.1.23.linux-amd64.tgz | tar -xz\n"
    b"install cockroach-v23.1.23.linux-amd64/cockroach /usr/local/bin/\n"
    b"mkdir -p /var/lib/cockroach\n"
    b"useradd cockroach 2>/dev/null || true\n"
    b"chown cockroach:cockroach /var/lib/cockroach\n"
    b"cat > /etc/systemd/system/cockroachdb.service << 'CRDBSVC'\n"
    b"[Unit]\n"
    b"Description=CockroachDB\n"
    b"[Service]\n"
    b"User=cockroach\n"
    b"ExecStart=/usr/local/bin/cockroach start-single-node --insecure --store=/var/lib/cockroach --listen-addr=:26257 --http-addr=:8080\n"
    b"Restart=always\n"
    b"[Install]\n"
    b"WantedBy=multi-user.target\n"
    b"CRDBSVC\n"
    b"systemctl daemon-reload && systemctl enable cockroachdb && systemctl start cockroachdb\n"
)


def test_phase1_provision():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")

    ec2 = _boto3_client("ec2", aws_creds)

    def launch_fn(aws_creds):
        """launch_fn interface for get_or_create_smoke_ami.

        Accepts aws_creds as its sole argument; returns (instance_id, ec2_client, None).
        """
        import base64
        ec2_client = _boto3_client("ec2", aws_creds)
        subnet_id  = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
        sg_id      = aws_creds.get("smoke_default_security_group_id")

        # Resolve latest AL2 AMI in the target region
        al2_resp = ec2_client.describe_images(
            Owners=["amazon"],
            Filters=[
                {"Name": "name",         "Values": ["amzn2-ami-hvm-2.0.*-x86_64-gp2"]},
                {"Name": "state",        "Values": ["available"]},
                {"Name": "architecture", "Values": ["x86_64"]},
            ],
        )
        al2_images = sorted(al2_resp["Images"], key=lambda i: i["CreationDate"], reverse=True)
        if not al2_images:
            raise RuntimeError("No AL2 AMI found in region")
        al2_ami_id = al2_images[0]["ImageId"]

        user_data = base64.b64encode(_CRDB_INSTALL_SCRIPT).decode()

        kwargs = dict(
            ImageId=al2_ami_id,
            InstanceType="t3.medium",
            MinCount=1, MaxCount=1,
            IamInstanceProfile={"Name": _SSM_PROFILE},
            UserData=user_data,
            TagSpecifications=[{"ResourceType": "instance", "Tags": [
                {"Key": "Name",             "Value": "nexplane-smoke-cockroachdb-ami-build"},
                {"Key": "nexplane-purpose", "Value": "smoke-cockroachdb-ami-build"},
            ]}],
        )
        if subnet_id:
            kwargs["SubnetId"] = subnet_id
        if sg_id:
            kwargs["SecurityGroupIds"] = [sg_id]

        resp        = ec2_client.run_instances(**kwargs)
        instance_id = resp["Instances"][0]["InstanceId"]
        log(f"  AMI build: launched {instance_id}, waiting for CockroachDB port 26257")

        ec2_client.get_waiter("instance_running").wait(InstanceIds=[instance_id])
        desc       = ec2_client.describe_instances(InstanceIds=[instance_id])
        private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(10)
            try:
                s = socket.create_connection((private_ip, 26257), timeout=5)
                s.close()
                log(f"  CockroachDB port 26257 ready on {private_ip}")
                break
            except OSError:
                pass
        else:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
            raise RuntimeError(f"CockroachDB never ready on {private_ip}:26257")

        return instance_id, ec2_client, None

    ami_id = get_or_create_smoke_ami(
        cache_key=_CRDB_AMI_CACHE_KEY,
        setup_hash="23.1",
        launch_fn=launch_fn,
        snapshot_name="nexplane-smoke-cockroachdb-23.1",
    )

    instance_id, private_ip = _launch_crdb_from_ami(ec2, ami_id, aws_creds)
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
    install_nexplane_agent_on_instance(instance_id, asset_id, aws_creds, private_ip=private_ip, timeout_s=300)

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
        "title":       f"smoke-cockroachdb-cluster-upgrade-{run_id}",
        "change_type": "cockroachdb_cluster_upgrade",
        "desired_outcome": {
            "source_version": "23.1",
            "target_version": "23.2",
            "nodes":          [{"host": private_ip, "port": 26257}],
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
         json={"decision": "approved", "comment": "cockroachdb cluster upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} — expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"
    assert result.get("target_version") == "23.2", f"Expected target_version 23.2: {result}"

    log("  CockroachDB cluster upgraded to 23.2")
    log("[PHASE 2: upgrade] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: rollback
# ---------------------------------------------------------------------------

def test_phase3_rollback():
    cr_id = _state.get("cr_id")
    if not cr_id:
        pytest.skip("Phase 2 did not complete — no CR to roll back")

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
