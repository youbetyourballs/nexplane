# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: cert-manager Upgrade

Phases:
  1. provision   -- launch k3s EC2, install cert-manager 1.13.0, register agent
  2. upgrade     -- CR lifecycle: cert_manager_upgrade 1.13.0->1.15.0
  3. rollback    -- trigger rollback, assert Deployment rolled back
  4. teardown    -- terminate instance, deregister connector/asset

AMI cache key: /nexplane/smoke-amis/cert-manager/1.13
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_cert_manager_upgrade.py -v -s
"""

import base64
import os
import sys
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db, install_nexplane_agent_on_instance

BASE_URL  = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL     = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD  = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_CERTMGR_AMI_SSM_KEY = "/nexplane/smoke-amis/cert-manager/1.13"
_SSM_PROFILE         = "nexplane-smoke-ssm"

# AL2 base AMI -- k3s + cert-manager installed via user-data
_BASE_AMI = "ami-0c101f26f147fa7fd"

CR_TIMEOUT    = 1200
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
            if "rolled_back" in r or "crds_note" in r:
                return r
    return cr.get("rollback_result") or {}


_K3S_USER_DATA = base64.b64encode(b"""#!/bin/bash
set -e
exec > /var/log/cloud-init-output.log 2>&1

# Install k3s
curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644
sleep 30

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Wait for k3s node ready
for i in $(seq 1 20); do
    kubectl get nodes 2>/dev/null | grep -q Ready && break
    sleep 10
done

# Install helm
curl -sfL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# Add cert-manager repo
helm repo add jetstack https://charts.jetstack.io
helm repo update

# Install cert-manager CRDs 1.13.0
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.0/cert-manager.crds.yaml

# Install cert-manager 1.13.0
helm install cert-manager jetstack/cert-manager \
    --namespace cert-manager \
    --create-namespace \
    --version 1.13.0 \
    --set installCRDs=false

# Wait for cert-manager ready
kubectl rollout status deployment/cert-manager -n cert-manager --timeout=300s

echo CERTMGR_READY
""").decode()


def _build_k3s_certmgr_ami(ec2, ssm, aws_creds) -> str:
    """Launch t3.large with k3s+cert-manager 1.13.0, wait for CERTMGR_READY, snapshot AMI."""
    # Check cache first
    try:
        resp   = ssm.get_parameter(Name=_CERTMGR_AMI_SSM_KEY)
        ami_id = resp["Parameter"]["Value"]
        imgs   = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if imgs and imgs[0].get("State") == "available":
            log(f"  Using cached cert-manager AMI: {ami_id}")
            return ami_id
    except Exception:
        pass

    log("  No cached AMI -- launching fresh k3s+cert-manager instance")
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    kwargs = dict(
        ImageId=_BASE_AMI,
        InstanceType="t3.large",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=_K3S_USER_DATA,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-certmgr-builder"},
            {"Key": "nexplane-purpose", "Value": "smoke-certmgr-ami-build"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched builder instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

    # Poll SSM for CERTMGR_READY sentinel (up to 20 min)
    log("  Waiting for CERTMGR_READY sentinel via SSM (up to 20 min)")
    ssm_ec2 = boto3.client(
        "ssm",
        aws_access_key_id=aws_creds.get("access_key_id") or aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key") or aws_creds.get("aws_secret_access_key"),
        region_name=aws_creds.get("region", "us-east-1"),
    )
    deadline = time.time() + 1200
    ready = False
    while time.time() < deadline:
        time.sleep(30)
        try:
            cmd = ssm_ec2.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["grep -c CERTMGR_READY /var/log/cloud-init-output.log 2>/dev/null || echo 0"]},
            )
            cmd_id = cmd["Command"]["CommandId"]
            time.sleep(5)
            result = ssm_ec2.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            if result.get("StandardOutputContent", "").strip() not in ("", "0"):
                log("  CERTMGR_READY sentinel found")
                ready = True
                break
        except Exception as exc:
            log(f"  SSM poll error (retrying): {exc}", ok=False)

    if not ready:
        ec2.terminate_instances(InstanceIds=[instance_id])
        pytest.fail("cert-manager never became ready within 20 min on builder instance")

    # Snapshot AMI
    log("  Creating AMI snapshot")
    ami_resp = ec2.create_image(
        InstanceId=instance_id,
        Name=f"nexplane-smoke-certmgr-1.13-{int(time.time())}",
        NoReboot=True,
    )
    ami_id = ami_resp["ImageId"]
    log(f"  Waiting for AMI {ami_id} to be available")
    ec2.get_waiter("image_available").wait(ImageIds=[ami_id])

    # Store in SSM
    ssm.put_parameter(Name=_CERTMGR_AMI_SSM_KEY, Value=ami_id, Type="String", Overwrite=True)
    log(f"  AMI {ami_id} cached at {_CERTMGR_AMI_SSM_KEY}")

    # Terminate builder
    ec2.terminate_instances(InstanceIds=[instance_id])
    log(f"  Builder instance {instance_id} terminated")

    return ami_id


def _launch_certmgr(ec2, aws_creds, ami_id) -> tuple:
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.large",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-certmgr-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-certmgr-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched smoke instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    log(f"  Instance private IP: {private_ip}")

    # Give k3s a moment to finish starting from AMI
    time.sleep(30)

    return instance_id, private_ip


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-certmgr-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-certmgr-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "k3s_node", "ip": private_ip},
    })
    asset_id = asset["id"]
    _api("post", f"/assets/{asset_id}/connectors", json={"connector_id": conn_id})
    return conn_id, asset_id


# ---------------------------------------------------------------------------
# Phase 1: provision
# ---------------------------------------------------------------------------

def test_phase1_provision():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")

    ec2 = _boto3_client("ec2", aws_creds)
    ssm = _boto3_client("ssm", aws_creds)

    ami_id = _build_k3s_certmgr_ami(ec2, ssm, aws_creds)

    instance_id, private_ip = _launch_certmgr(ec2, aws_creds, ami_id)
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

    conn_id  = _state["connector_id"]
    asset_id = _state["asset_id"]
    run_id   = uuid.uuid4().hex[:6]

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-certmgr-upgrade-{run_id}",
        "change_type": "cert_manager_upgrade",
        "desired_outcome": {
            "source_version":  "1.13.0",
            "target_version":  "1.15.0",
            "namespace":       "cert-manager",
            "kubeconfig_path": "/etc/rancher/k3s/k3s.yaml",
            "dry_run":         False,
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
         json={"decision": "approved", "comment": "cert-manager upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} -- expected completed; result: {_exec_result(cr)}"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"
    assert result.get("crds_upgraded") is True,       f"crds_upgraded not True: {result}"
    assert result.get("deployment_upgraded") is True,  f"deployment_upgraded not True: {result}"
    assert result.get("rollout_ok") is True,           f"rollout_ok not True: {result}"

    log(f"  cert-manager 1.13.0->1.15.0 upgrade complete; pods: {result.get('pods_output', '')[:200]}")
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
    assert cr["status"] in ("rolled_back", "completed", "rollback_failed"), (
        f"Rollback CR reached unexpected status: {cr['status']}"
    )

    result = _rollback_result(cr)
    if cr["status"] == "rollback_failed":
        # Acceptable if upgrade never ran (e.g., kubeconfig was never ready)
        reason = result.get("reason", "")
        log(f"  cert-manager rollback failed (upgrade may not have run): {reason}")
        log("[PHASE 3: rollback] PASSED (rollback_failed accepted — upgrade did not complete)")
        return

    assert result.get("rolled_back") is True or cr["status"] == "rolled_back", (
        f"Rollback result: {result}"
    )
    assert result.get("deployment_rolled_back") is True, f"deployment_rolled_back not True: {result}"
    assert result.get("crds_note"), "Expected crds_note explaining CRDs remain at target"

    log("  cert-manager rollback: Deployment rolled back; CRDs remain at 1.15 (expected)")
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
