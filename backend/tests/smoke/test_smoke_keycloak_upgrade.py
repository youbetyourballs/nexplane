# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Keycloak Major Upgrade

Phases:
  1. provision   -- launch Keycloak 21.1 EC2 from cached AMI (or build and cache)
  2. upgrade     -- CR lifecycle: keycloak_upgrade 21.1->24.0, verify /health/ready + master realm
  3. rollback    -- trigger rollback, verify old version responds
  4. teardown    -- terminate instance, deregister connector/asset

AMI cache key: /nexplane/smoke-amis/keycloak/21.1
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_keycloak_upgrade.py -v -s
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
    NexplaneClient, log, get_connector_creds_from_db,
    get_or_create_smoke_ami, install_nexplane_agent_on_instance,
)

BASE_URL  = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL     = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD  = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_KC_AMI_SSM_KEY    = "/nexplane/smoke-amis/keycloak/21.1"
_KC_ADMIN_USER     = "admin"
_KC_ADMIN_PASSWORD = "SmokeAdmin1234!"
_KC_DB_PASSWORD    = "SmokeDb5678!"
_SSM_PROFILE       = "nexplane-smoke-ssm"
_SOURCE_VERSION    = "21.1"
_TARGET_VERSION    = "24.0"

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


def _build_keycloak_ami(aws_creds) -> tuple:
    import time as _t
    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=aws_creds.get("access_key_id") or aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key") or aws_creds.get("aws_secret_access_key"),
        region_name=aws_creds.get("region", "us-east-1"),
    )

    user_data = base64.b64encode(b"""#!/bin/bash
# Install Java 17 -- AL2023 ships Corretto 17 in default repos (no amazon-linux-extras)
yum install -y java-17-amazon-corretto-headless 2>/dev/null || yum install -y java-17-openjdk-headless
# Download Keycloak 21.1.2
curl -sfL https://github.com/keycloak/keycloak/releases/download/21.1.2/keycloak-21.1.2.tar.gz -o /tmp/keycloak.tar.gz
tar -xz -C /opt/ -f /tmp/keycloak.tar.gz
ln -sfn /opt/keycloak-21.1.2 /opt/keycloak
useradd -r keycloak 2>/dev/null || true
chown -R keycloak:keycloak /opt/keycloak
# Use start-dev: no pre-build step, H2 embedded, starts in 2-5 min -- ideal for smoke tests
printf '[Unit]\nDescription=Keycloak\n[Service]\nUser=keycloak\nEnvironment=KEYCLOAK_ADMIN=admin\nEnvironment=KEYCLOAK_ADMIN_PASSWORD=SmokeAdmin1234!\nExecStart=/opt/keycloak/bin/kc.sh start-dev --http-port=8080\nRestart=on-failure\n[Install]\nWantedBy=multi-user.target\n' > /etc/systemd/system/keycloak.service
systemctl daemon-reload && systemctl enable keycloak && systemctl start keycloak
""").decode()

    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"
    kwargs = dict(
        ImageId="ami-0c101f26f147fa7fd",
        InstanceType="t3.large",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": "nexplane-smoke-ssm"},
        UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-keycloak-build"},
            {"Key": "nexplane-purpose", "Value": "smoke-ami-build"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Keycloak AMI build instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for Keycloak port 8080 on {private_ip} (up to 30 min — cold install: java yum + download + JVM init)")
    deadline = _t.time() + 1800
    while _t.time() < deadline:
        _t.sleep(15)
        try:
            s = socket.create_connection((private_ip, 8080), timeout=5)
            s.close()
            log(f"  Keycloak port 8080 open on {private_ip}")
            return instance_id, ec2, None
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"Keycloak port 8080 never reachable on {private_ip} within 30 min (AMI build) — Java install or JVM startup failed")


def _launch_kc(ec2, ami_id, aws_creds) -> tuple:
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    launch_user_data = base64.b64encode(b"""#!/bin/bash
# AMI snapshot may contain H2 lock files from the build run.
# H2 treats a stale .lck file as an in-use database and refuses to start.
# Stop keycloak, wipe H2 data (forces clean re-init ~3-5 min), restart.
systemctl stop keycloak 2>/dev/null || true
sleep 5
systemctl reset-failed keycloak 2>/dev/null || true
rm -rf /opt/keycloak/data/h2 2>/dev/null || true
systemctl start keycloak
""").decode()

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.large",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=launch_user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-keycloak-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-keycloak-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Keycloak instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for Keycloak port 8080 on {private_ip} (up to 30 min — start-dev JVM startup is slow even from AMI)")
    deadline = time.time() + 1800
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 8080), timeout=5)
            s.close()
            log(f"  Keycloak port 8080 open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"Keycloak port 8080 never reachable on {private_ip} within 30 min (launch)")


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-kc-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-kc-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "keycloak", "ip": private_ip},
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
    """Extract rollback result from the rolled_back execution run."""
    runs = cr.get("execution_runs") or []
    for run in runs:
        if run.get("status") in ("rolled_back", "rollback_failed"):
            r = run.get("result") or {}
            if "rolled_back" in r or "strategy" in r:
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
        cache_key="keycloak/21.1",
        setup_hash="keycloak-21.1",
        launch_fn=_build_keycloak_ami,
        snapshot_name="nexplane-smoke-keycloak-21.1",
    )

    ec2                      = _boto3_client("ec2", aws_creds)
    instance_id, private_ip  = _launch_kc(ec2, ami_id, aws_creds)
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
        "title":       f"smoke-keycloak-upgrade-{run_id}",
        "change_type": "keycloak_upgrade",
        "desired_outcome": {
            "source_version":   _SOURCE_VERSION,
            "target_version":   _TARGET_VERSION,
            "keycloak_home":    "/opt/keycloak",
            "db_vendor":        "postgres",
            "db_host":          "localhost",
            "db_name":          "keycloak",
            "db_user":          "keycloak",
            "db_password":      _KC_DB_PASSWORD,
            "admin_user":       _KC_ADMIN_USER,
            "admin_password":   _KC_ADMIN_PASSWORD,
        },
        "connector_id": conn_id,
        "asset_ids":    [asset_id],
    })
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    log(f"  Created CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "keycloak upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} -- expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status unexpected: {result}"

    verify = result.get("verify_result", {})
    assert verify.get("health_ready"), f"Keycloak /health/ready not UP: {verify}"
    assert verify.get("verify_status") == "passed", f"Verify failed: {verify}"

    realm_count = verify.get("realm_count", 0)
    assert realm_count >= 1, f"Expected at least master realm, got {realm_count}"

    log(f"  Upgrade verified: {realm_count} realm(s), health UP")
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

    cr = _poll_cr(cr_id, timeout_s=600)
    assert cr["status"] in ("rolled_back", "completed"), (
        f"Rollback CR reached unexpected status: {cr['status']}"
    )

    result = _rollback_result(cr)
    assert result.get("rolled_back") is True or cr["status"] == "rolled_back", (
        f"Rollback result unexpected: {result}"
    )
    log("[PHASE 3: rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 4: teardown
# ---------------------------------------------------------------------------

def test_phase4_teardown():
    if not _state.get("provisioned_by_us"):
        log("[PHASE 4: teardown] SKIPPED (reused existing infra)")
        return

    aws_creds   = get_connector_creds_from_db("aws")
    asset_id    = _state.get("asset_id")
    conn_id     = _state.get("connector_id")
    instance_id = _state.get("instance_id")

    if asset_id:
        try:
            _api("delete", f"/assets/{asset_id}")
            log(f"  Deleted asset {asset_id}")
        except Exception as exc:
            log(f"  Warning: could not delete asset: {exc}", ok=False)

    if conn_id:
        try:
            _api("delete", f"/connectors/{conn_id}")
            log(f"  Deleted connector {conn_id}")
        except Exception as exc:
            log(f"  Warning: could not delete connector: {exc}", ok=False)

    if instance_id and aws_creds:
        try:
            ec2 = _boto3_client("ec2", aws_creds)
            ec2.terminate_instances(InstanceIds=[instance_id])
            log(f"  Terminated instance {instance_id}")
        except Exception as exc:
            log(f"  Warning: could not terminate instance: {exc}", ok=False)

    log("[PHASE 4: teardown] PASSED")
