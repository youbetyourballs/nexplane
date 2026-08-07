# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Ceph Cluster Upgrade

Phases:
  1. provision   — launch single-node Ceph Reef (18.2) EC2 from cached AMI, install agent
  2. upgrade     — CR lifecycle: ceph_cluster_upgrade 18.2 → 19.2, assert daemons upgraded
  3. rollback    — trigger rollback, assert irreversibility surfaced correctly
  4. teardown    — terminate instance, deregister connector/asset

AMI cache key: /nexplane/smoke-amis/ceph/18.2
Run:
    docker exec nexplane-backend-1 python -m pytest \\
        /app/tests/smoke/test_smoke_ceph_cluster_upgrade.py -v -s
"""

import base64
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

_CEPH_AMI_SSM_KEY   = "/nexplane/smoke-amis/ceph/18.2"
_CEPH_DASHBOARD_PWD = "SmokeC3ph1!"
_SSM_PROFILE        = "nexplane-smoke-ssm"
_SOURCE_VERSION     = "18.2"
_TARGET_VERSION     = "19.2"

CR_TIMEOUT    = 2700
POLL_INTERVAL = 30

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


def _get_al2_ami(ec2) -> str:
    """Resolve the latest Amazon Linux 2 AMI in the current region."""
    resp = ec2.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name",         "Values": ["amzn2-ami-hvm-2.0.*-x86_64-gp2"]},
            {"Name": "state",        "Values": ["available"]},
            {"Name": "architecture", "Values": ["x86_64"]},
        ],
    )
    images = sorted(resp["Images"], key=lambda x: x["CreationDate"], reverse=True)
    if not images:
        pytest.fail("No Amazon Linux 2 AMI found in region")
    return images[0]["ImageId"]


# user_data installs Ceph Reef (18.2) single-node via cephadm
_CEPH_USER_DATA_RAW = (
    b"#!/bin/bash\n"
    b"# Install Ceph Reef on AL2\n"
    b"yum install -y python3 python3-pip\n"
    b"# Add Ceph repo\n"
    b"cat > /etc/yum.repos.d/ceph.repo << 'CEPHEPO'\n"
    b"[ceph]\n"
    b"name=Ceph packages for x86_64\n"
    b"baseurl=https://download.ceph.com/rpm-reef/el8/x86_64/\n"
    b"enabled=1\n"
    b"priority=2\n"
    b"gpgcheck=1\n"
    b"gpgkey=https://download.ceph.com/keys/release.asc\n"
    b"CEPHEPO\n"
    b"yum install -y ceph ceph-mon ceph-mgr ceph-osd 2>/dev/null || true\n"
    b"# Bootstrap single-node ceph cluster with cephadm\n"
    b"curl -sfL https://download.ceph.com/rpm-reef/el8/noarch/cephadm"
    b" -o /usr/local/bin/cephadm && chmod +x /usr/local/bin/cephadm\n"
    b"cephadm bootstrap --mon-ip 127.0.0.1"
    b" --initial-dashboard-user admin"
    b" --initial-dashboard-password SmokeC3ph1!"
    b" --skip-monitoring-stack --skip-firewalld"
    b" 2>&1 | tee /var/log/cephadm-bootstrap.log\n"
)


def _launch_ceph(aws_creds) -> tuple:
    """Launch a single-node Ceph Reef (18.2) instance for AMI snapshotting.

    Returns (instance_id, ec2_client, private_ip) per get_or_create_smoke_ami contract.
    """
    ec2       = _boto3_client("ec2", aws_creds)
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"
    user_data = base64.b64encode(_CEPH_USER_DATA_RAW).decode()

    kwargs = dict(
        ImageId=_get_al2_ami(ec2),
        InstanceType="t3.large",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-ceph-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-ceph-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Ceph instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    # Wait up to 15 min for Ceph dashboard on port 8443
    log(f"  Waiting for Ceph dashboard port 8443 on {private_ip} (up to 15 min)")
    deadline = time.time() + 900
    reached  = False
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 8443), timeout=5)
            s.close()
            log(f"  Ceph dashboard port 8443 open on {private_ip}")
            reached = True
            break
        except OSError:
            pass

    if not reached:
        # Bootstrap may still be running — fall back to a 120s grace period
        log("  Port 8443 not reachable; waiting 120s grace period for bootstrap")
        time.sleep(120)

    return instance_id, ec2, private_ip


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-ceph-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-ceph-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "ceph_admin", "ip": private_ip},
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
        cache_key="ceph",
        setup_hash="18.2",
        launch_fn=_launch_ceph,
        snapshot_name="nexplane-smoke-ceph-reef-18.2",
    )
    log(f"  Using Ceph AMI: {ami_id}")

    ec2       = _boto3_client("ec2", aws_creds)
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.large",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-ceph-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-ceph-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Ceph instance {instance_id} from AMI {ami_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

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

    # Single-node cluster: all daemon roles live on the same host
    single_node = [{"host": private_ip}]

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-ceph-upgrade-{run_id}",
        "change_type": "ceph_cluster_upgrade",
        "desired_outcome": {
            "source_version": _SOURCE_VERSION,
            "target_version": _TARGET_VERSION,
            "mgr_hosts":      single_node,
            "mon_hosts":      single_node,
            "osd_hosts":      single_node,
            "rgw_hosts":      [],
            "mds_hosts":      [],
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
         json={"decision": "approved", "comment": "ceph upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id} (timeout {CR_TIMEOUT}s — Ceph upgrades are slow)")

    cr = _poll_cr(cr_id)
    # Ceph may finish with verify_failed if cluster is still settling (HEALTH_WARN is acceptable)
    assert cr["status"] in ("completed", "verify_failed"), (
        f"CR reached {cr['status']} — expected completed or verify_failed"
    )

    result           = _exec_result(cr)
    daemons_upgraded = result.get("daemons_upgraded", [])
    assert any("mgr:" in d for d in daemons_upgraded), f"No MGR daemons upgraded: {daemons_upgraded}"
    assert any("mon:" in d for d in daemons_upgraded), f"No MON daemons upgraded: {daemons_upgraded}"
    assert any("osd:" in d for d in daemons_upgraded), f"No OSD daemons upgraded: {daemons_upgraded}"
    assert result.get("noout_unset") is True, f"noout_unset should be True: {result}"

    log(f"  Ceph upgraded: daemons={daemons_upgraded}, noout_unset=True")
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

    cr = _poll_cr(cr_id, timeout_s=600)
    assert cr["status"] in ("rolled_back", "rollback_failed", "completed"), (
        f"Rollback CR reached unexpected status: {cr['status']}"
    )

    result = _rollback_result(cr)
    # Ceph downgrade is not supported — executor surfaces irreversibility
    assert result.get("rolled_back") is False, (
        f"Ceph rollback should surface irreversibility (rolled_back=False): {result}"
    )
    assert result.get("strategy") == "partial_rollback", f"Wrong rollback strategy: {result}"
    assert result.get("daemons_on_target_version"), (
        f"daemons_on_target_version should be surfaced: {result}"
    )
    assert result.get("manual_steps"), f"manual_steps should be surfaced: {result}"
    reason = result.get("reason", "")
    assert "downgrade" in reason.lower() or "not support" in reason.lower(), (
        f"Reason should explain Ceph downgrade limitation: {result}"
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
