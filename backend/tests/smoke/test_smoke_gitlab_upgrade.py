# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: GitLab Major Version Upgrade

Single-hop smoke: 16.0.10 -> 16.11 (cheapest valid hop to verify CR lifecycle).
AMI auto-provisioned on cache miss via launch_fn.

AMI cache key: /nexplane/smoke-amis/gitlab/16.0
Instance type: t3.xlarge (GitLab needs 4GB+ RAM)

Phases:
  1. provision  -- get_or_create_smoke_ami (auto-builds if not cached); launch t3.xlarge; register
  2. upgrade    -- gitlab_upgrade CR lifecycle (16.0->16.11)
  3. rollback   -- restore from per-hop backup; assert rolled_back
  4. teardown   -- terminate instance; deregister connector/asset

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_gitlab_upgrade.py -v -s
"""

import os
import sys
import socket
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db, get_or_create_smoke_ami, install_nexplane_agent_on_instance

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL    = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_AMI_SSM_KEY        = "gitlab/16.0"
SOURCE_VERSION      = "16.0"
TARGET_VERSION      = "16.11"
INSTANCE_TYPE       = "t3.xlarge"
GITLAB_ADMIN_TOKEN  = os.environ.get("GITLAB_SMOKE_TOKEN", "SmokeGitLab1234!")
_SSM_PROFILE        = "nexplane-smoke-ssm"

CR_TIMEOUT    = 1800   # 30 min -- backup + upgrade + background migrations
POLL_INTERVAL = 15

_state = {
    "connector_id":      None,
    "asset_id":          None,
    "instance_id":       None,
    "private_ip":        None,
    "provisioned_by_us": False,
    "cr_id":             None,
    "execution_result":  None,
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


def _build_gitlab_ami(aws_creds) -> tuple:
    """Launch a t3.xlarge, install GitLab CE 16.0.10, return (instance_id, ec2_client, None)."""
    import base64

    user_data = base64.b64encode(
        b"#!/bin/bash\n"
        b"yum install -y curl policycoreutils openssh-server openssh-clients postfix\n"
        b"systemctl enable sshd && systemctl start sshd\n"
        b"systemctl enable postfix && systemctl start postfix\n"
        b"curl -sS https://packages.gitlab.com/install/repositories/gitlab/gitlab-ce/script.rpm.sh | bash\n"
        b'EXTERNAL_URL="http://localhost" GITLAB_ROOT_PASSWORD="SmokeGitLab1234!" yum install -y gitlab-ce-16.0.10\n'
        b"gitlab-ctl reconfigure\n"
        # Bake in listen_addresses so NGINX binds to 0.0.0.0 on every launch from this AMI.
        # EXTERNAL_URL=localhost makes NGINX default to 127.0.0.1; this override is required
        # so the platform can reach GitLab when launching a test instance from the cached AMI.
        b"echo \"nginx['listen_addresses'] = ['0.0.0.0']\" >> /etc/gitlab/gitlab.rb\n"
        b"gitlab-ctl reconfigure\n"
        # Sentinel: only written after ALL steps complete. Do NOT use cloud-init log grep --
        # cloud-init logs script source before executing, causing false-positive detection.
        b"touch /tmp/nexplane-gitlab-smoke-ready\n"
    ).decode()

    ec2 = _boto3_client("ec2", aws_creds)

    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    kwargs = dict(
        ImageId="ami-0c101f26f147fa7fd",
        InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-gitlab-build"},
            {"Key": "nexplane-purpose", "Value": "smoke-ami-build"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched GitLab build instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    # Poll sentinel file via SSM (75 min) — yum install + gitlab-ctl reconfigure takes 45-60 min.
    # Sentinel /tmp/nexplane-gitlab-smoke-ready is written AFTER reconfigure completes.
    # Do NOT use cloud-init-output.log grep — cloud-init logs script source before executing.
    log(f"  Waiting for GitLab install sentinel on {instance_id} (up to 75 min)")
    ssm_c  = _boto3_client("ssm", aws_creds)
    deadline = time.time() + 4500  # 75 min
    ready    = False
    while time.time() < deadline:
        time.sleep(30)
        try:
            resp2  = ssm_c.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [
                    "test -f /tmp/nexplane-gitlab-smoke-ready && echo SENTINEL_OK || echo SENTINEL_MISSING"
                ]},
                TimeoutSeconds=30,
            )
            cmd_id = resp2["Command"]["CommandId"]
            time.sleep(5)
            inv = ssm_c.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            if inv.get("Status") == "Success" and "SENTINEL_OK" in inv.get("StandardOutputContent", ""):
                ready = True
                break
        except Exception:
            pass

    if not ready:
        ec2.terminate_instances(InstanceIds=[instance_id])
        pytest.fail(f"GitLab install never completed on {instance_id} within 75 min")

    # AMI build uses EXTERNAL_URL=http://localhost, so GitLab binds to 127.0.0.1 only.
    # Port-80 check here would always fail. Sentinel confirms install is complete -- that's enough.
    log(f"  GitLab install confirmed on build instance {instance_id}")
    return instance_id, ec2, None


def _launch_gitlab(ec2, ami_id, aws_creds) -> tuple:
    """Launch a GitLab instance from a pre-built AMI. Returns (instance_id, private_ip)."""
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    kwargs = dict(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-gitlab-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-gitlab-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched GitLab upgrade instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    # AMI has nginx['listen_addresses'] = ['0.0.0.0'] baked in; port 80 opens once gitlab-runsvdir starts.
    log(f"  Waiting for GitLab port 80 on {private_ip} (up to 15 min)")
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 80), timeout=5)
            s.close()
            log(f"  GitLab port 80 open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass

    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"GitLab never reachable on {private_ip}:80 within 15 min")


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-gitlab-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-gitlab-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "gitlab", "ip": private_ip},
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
        cache_key=_AMI_SSM_KEY,
        setup_hash="gitlab-16.0",
        launch_fn=_build_gitlab_ami,
        snapshot_name="nexplane-smoke-gitlab-16.0",
    )
    log(f"  Using GitLab AMI: {ami_id}")

    ec2 = _boto3_client("ec2", aws_creds)
    instance_id, private_ip = _launch_gitlab(ec2, ami_id, aws_creds)
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
        "title":       f"smoke-gitlab-upgrade-{run_id}",
        "change_type": "gitlab_upgrade",
        "desired_outcome": {
            "source_version":      SOURCE_VERSION,
            "target_version":      TARGET_VERSION,
            "gitlab_host":         private_ip,
            "gitlab_admin_token":  GITLAB_ADMIN_TOKEN,
            "gitlab_package_type": "omnibus",
            "dry_run":             False,
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
         json={"decision": "approved", "comment": "gitlab upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} -- expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"
    assert result.get("final_version", "").startswith(TARGET_VERSION), \
        f"Expected {TARGET_VERSION}.x, got {result.get('final_version')}"
    assert result.get("hops_completed"), "Expected at least one completed hop"

    _state["execution_result"] = result
    log(f"  GitLab upgraded: final_version={result.get('final_version')}, hops={result.get('hops_completed')}")
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
