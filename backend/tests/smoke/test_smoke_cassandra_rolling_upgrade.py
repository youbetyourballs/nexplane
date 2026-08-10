# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Cassandra Rolling Upgrade

Phases:
  1. provision   -- launch Cassandra 4.0 EC2 from cached AMI, install nexplane agent
  2. upgrade     -- CR lifecycle: cassandra_rolling_upgrade 4.0->4.1, assert completed
  3. rollback    -- trigger rollback, assert rolled_back is True
  4. teardown    -- terminate instance, deregister connector/asset

AMI cache key: /nexplane/smoke-amis/cassandra/4.0
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_cassandra_rolling_upgrade.py -v -s
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
    install_nexplane_agent_on_instance,
    get_or_create_smoke_ami,
)

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL    = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_CASSANDRA_AMI_KEY = "cassandra/4.0"
_SSM_PROFILE       = "nexplane-smoke-ssm"

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


def _build_cassandra_ami(aws_creds) -> tuple:
    """Build a Cassandra 4.0 AMI from scratch. Called once by get_or_create_smoke_ami.

    Installs Cassandra 4.0.13 on AL2, waits for port 9042, returns (instance_id, ec2, None)
    for get_or_create_smoke_ami to snapshot and cache.
    """
    import base64

    ec2 = _boto3_client("ec2", aws_creds)

    resp = ec2.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name",                "Values": ["amzn2-ami-hvm-2.0.*-x86_64-gp2"]},
            {"Name": "state",               "Values": ["available"]},
            {"Name": "virtualization-type", "Values": ["hvm"]},
        ],
    )
    images = sorted(resp["Images"], key=lambda x: x["CreationDate"], reverse=True)
    if not images:
        pytest.fail("No AL2 AMI found")
    al2_ami = images[0]["ImageId"]

    install_lines = [
        "#!/bin/bash",
        "set -e",
        "amazon-linux-extras install java-openjdk11 -y",
        "CVER=4.0.13",
        "curl -sfL https://archive.apache.org/dist/cassandra/${CVER}/apache-cassandra-${CVER}-bin.tar.gz | tar -xz -C /opt/",
        "ln -sfn /opt/apache-cassandra-${CVER} /opt/cassandra",
        "useradd -r cassandra 2>/dev/null || true",
        "mkdir -p /var/lib/cassandra /var/log/cassandra",
        "chown -R cassandra:cassandra /var/lib/cassandra /var/log/cassandra /opt/cassandra",
        # listen_address must be 0.0.0.0 so it works on any IP after AMI boot
        "sed -i 's/listen_address: localhost/listen_address: 0.0.0.0/' /opt/cassandra/conf/cassandra.yaml",
        "sed -i 's/rpc_address: localhost/rpc_address: 0.0.0.0/' /opt/cassandra/conf/cassandra.yaml",
        r"printf '[Unit]\nDescription=Cassandra\n[Service]\nUser=cassandra\nExecStart=/opt/cassandra/bin/cassandra -f\nRestart=always\n[Install]\nWantedBy=multi-user.target\n' > /etc/systemd/system/cassandra.service",
        "systemctl daemon-reload && systemctl enable cassandra && systemctl start cassandra",
    ]
    user_data = base64.b64encode("\n".join(install_lines).encode()).decode()

    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    kwargs = dict(
        ImageId=al2_ami,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-cassandra-build"},
            {"Key": "nexplane-purpose", "Value": "smoke-ami-build"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Cassandra AMI build instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for Cassandra port 9042 on {private_ip} (up to 20 min — fresh install)")
    deadline = time.time() + 1200
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = socket.create_connection((private_ip, 9042), timeout=5)
            s.close()
            log(f"  Cassandra port 9042 open on {private_ip}")
            break
        except OSError:
            pass

    return instance_id, ec2, None


def _launch_cassandra_from_ami(ec2, ami_id, aws_creds) -> tuple:
    """Boot a fresh instance from the cached Cassandra AMI.

    Cassandra is already installed; just start the service and wait for port 9042.
    Returns (instance_id, private_ip).
    """
    import base64

    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    # listen_address is 0.0.0.0 (set at AMI build), so no IP fixup needed on boot.
    launch_user_data = base64.b64encode(b"""#!/bin/bash
systemctl stop cassandra 2>/dev/null || true
sleep 3
systemctl reset-failed cassandra 2>/dev/null || true
systemctl start cassandra
""").decode()

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=launch_user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-cassandra-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-cassandra-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Cassandra instance {instance_id} from AMI {ami_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for Cassandra port 9042 on {private_ip} (up to 10 min — booting from AMI)")
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = socket.create_connection((private_ip, 9042), timeout=5)
            s.close()
            log(f"  Cassandra port 9042 open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"Cassandra never reachable on {private_ip}:9042 within 10 min (launch from AMI)")


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-cassandra-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-cassandra-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "cassandra_node", "ip": private_ip},
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
        cache_key=_CASSANDRA_AMI_KEY,
        setup_hash="4.0",
        launch_fn=_build_cassandra_ami,
        snapshot_name="nexplane-smoke-cassandra-4.0",
    )
    log(f"  Using Cassandra AMI: {ami_id}")

    # Launch a live instance from the cached AMI for this smoke run
    ec2            = _boto3_client("ec2", aws_creds)
    instance_id, private_ip = _launch_cassandra_from_ami(ec2, ami_id, aws_creds)
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
        "title":       f"smoke-cassandra-upgrade-{run_id}",
        "change_type": "cassandra_rolling_upgrade",
        "desired_outcome": {
            "source_version":        "4.0",
            "target_version":        "4.1",
            "nodes":                 [{"host": private_ip, "port": 9042, "dc": "datacenter1"}],
            "upgrade_delay_seconds": 10,
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
         json={"decision": "approved", "comment": "cassandra rolling upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} -- expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed",   f"Executor status: {result}"
    assert result.get("target_version") == "4.1", f"target_version mismatch: {result}"

    verify = result.get("verify_result", {})
    assert verify.get("all_nodes_ok"), f"Cassandra verify not OK: {verify}"
    assert result.get("snapshot_tag"),  "No snapshot_tag in result -- snapshot missing"

    log(f"  Cassandra upgraded 4.0->4.1, all_nodes_ok, snapshot_tag={result['snapshot_tag']}")
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
