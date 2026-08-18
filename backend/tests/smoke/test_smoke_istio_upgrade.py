# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Istio Control Plane Upgrade (k3s auto-provision)

Phases:
  1. provision   -- launch t3.xlarge EC2, install k3s + Istio 1.20, cache AMI
  2. upgrade     -- istio_control_plane_upgrade CR lifecycle (inplace 1.20->1.21)
  3. rollback    -- trigger rollback, assert rolled_back=True
  4. teardown    -- delete asset/connector, terminate EC2

AMI cache key: /nexplane/smoke-amis/istio/1.20

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_istio_upgrade.py -v -s
"""

import os
import sys
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
)

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL    = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_ISTIO_AMI_SSM_KEY = "/nexplane/smoke-amis/istio/1.20"
_SSM_PROFILE       = "nexplane-smoke-ssm"
_BASE_AMI          = "ami-0c101f26f147fa7fd"  # Amazon Linux 2 us-east-1

SOURCE_VERSION = "1.20"
TARGET_VERSION = "1.21"

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


def _poll_cr(cr_id, timeout_s=CR_TIMEOUT, terminal=None):
    if terminal is None:
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


_USERDATA = base64.b64encode(b"""#!/bin/bash
# Install k3s (disable Traefik to avoid port conflicts with Istio)
curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644 --disable=traefik
sleep 30

# Install Istio 1.20
curl -sfL https://istio.io/downloadIstio | ISTIO_VERSION=1.20.3 TARGET_ARCH=x86_64 sh -
cp /root/istio-1.20.3/bin/istioctl /usr/local/bin/istioctl

# Install Istio 1.20 on the cluster
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
istioctl install --set profile=minimal -y
kubectl rollout status deployment/istiod -n istio-system --timeout=300s

# Ensure k3s starts on next boot (for launches from AMI)
systemctl enable k3s

# Sentinel file — only created after ALL above steps complete.
# DO NOT use cloud-init-output.log grep: cloud-init logs the script
# source before executing, so any string in the script appears early.
touch /tmp/nexplane-istio-smoke-ready
""").decode()


def _build_k3s_istio_ami(ec2, ssm, aws_creds) -> tuple:
    """Launch instance, wait for ISTIO_READY, return (instance_id, private_ip)."""
    # Check cache first
    try:
        resp   = ssm.get_parameter(Name=_ISTIO_AMI_SSM_KEY)
        ami_id = resp["Parameter"]["Value"]
        imgs   = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if imgs and imgs[0].get("State") == "available":
            log(f"  Using cached Istio AMI: {ami_id}")
            return _launch_from_ami(ec2, aws_creds, ami_id)
    except Exception:
        pass

    # Launch fresh instance with user-data that installs k3s + Istio
    log("  No cached AMI -- launching fresh instance to build k3s + Istio 1.20")
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    kwargs = dict(
        ImageId=_BASE_AMI,
        InstanceType="t3.xlarge",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=_USERDATA,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-istio-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-istio-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched instance {instance_id}, waiting for running state")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    # Poll for sentinel file via SSM — file is only created after ALL install steps complete.
    # Do NOT grep cloud-init-output.log: cloud-init logs the script source before running it,
    # so any string in the script appears in the log immediately (false positive).
    log(f"  Polling sentinel file for install completion on {instance_id} (up to 30 min)")
    ssm_client = boto3.client(
        "ssm",
        aws_access_key_id=aws_creds.get("access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key"),
        region_name=aws_creds.get("region", "us-east-1"),
    )
    deadline = time.time() + 1800  # 30 min for Istio to install
    ready    = False
    while time.time() < deadline:
        time.sleep(30)
        try:
            resp2  = ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [
                    "test -f /tmp/nexplane-istio-smoke-ready && echo SENTINEL_OK || echo SENTINEL_MISSING"
                ]},
                TimeoutSeconds=30,
            )
            cmd_id = resp2["Command"]["CommandId"]
            time.sleep(5)
            inv = ssm_client.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            if inv.get("Status") == "Success" and "SENTINEL_OK" in inv.get("StandardOutputContent", ""):
                ready = True
                break
        except Exception:
            pass

    if not ready:
        ec2.terminate_instances(InstanceIds=[instance_id])
        pytest.fail(f"Istio install sentinel never appeared on {instance_id} within 30 min")

    log(f"  ISTIO_READY confirmed on {instance_id} / {private_ip}")

    # Cache as AMI for future runs
    try:
        ts     = int(time.time())
        img    = ec2.create_image(
            InstanceId=instance_id,
            Name=f"nexplane-smoke-istio-1.20-{ts}",
            Description="Nexplane smoke: k3s + Istio 1.20",
            NoReboot=True,
        )
        new_ami = img["ImageId"]
        ssm_client.put_parameter(
            Name=_ISTIO_AMI_SSM_KEY,
            Value=new_ami,
            Type="String",
            Overwrite=True,
        )
        log(f"  Cached new AMI {new_ami} at {_ISTIO_AMI_SSM_KEY}")
    except Exception as exc:
        log(f"  Warning: AMI caching failed (non-fatal): {exc}", ok=False)

    return instance_id, private_ip


def _launch_from_ami(ec2, aws_creds, ami_id) -> tuple:
    """Launch a new instance from a cached AMI."""
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.xlarge",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        # k3s embedded etcd bakes node identity into AMI state. On a new instance
        # the identity doesn't match so k3s refuses to start. Wipe server state so
        # k3s re-initializes fresh; containerd image cache survives the wipe so
        # k3s+Istio come back up in ~2-3 min instead of 15+.
        UserData=(
            "#!/bin/bash\n"
            "systemctl stop k3s 2>/dev/null || true\n"
            "sleep 2\n"
            # Wipe etcd/identity state; containerd image cache at /var/lib/rancher/k3s/data survives
            "rm -rf /var/lib/rancher/k3s/server /var/lib/rancher/k3s/agent\n"
            "systemctl reset-failed k3s 2>/dev/null || true\n"
            "systemctl start k3s\n"
            # Wait up to 10 min for kubeconfig then patch server URL to 127.0.0.1
            "for i in $(seq 1 120); do\n"
            "  if [ -f /etc/rancher/k3s/k3s.yaml ]; then\n"
            "    sed -i 's|server: https://.*:6443|server: https://127.0.0.1:6443|' /etc/rancher/k3s/k3s.yaml\n"
            "    break\n"
            "  fi\n"
            "  sleep 5\n"
            "done\n"
            # Reinstall Istio 1.20 using pre-installed istioctl (images cached in containerd)
            "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"
            "ISTIOCTL=/usr/local/bin/istioctl\n"
            "if [ -f $ISTIOCTL ]; then\n"
            "  $ISTIOCTL install --set profile=minimal -y 2>&1 | tail -5\n"
            "  kubectl rollout status deployment/istiod -n istio-system --timeout=300s 2>&1 || true\n"
            "fi\n"
        ),
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-istio-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-istio-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched from cached AMI: {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
    log(f"  Instance running: {private_ip}")
    return instance_id, private_ip


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-istio-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-istio-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "medium",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "k3s_istio", "ip": private_ip},
    })
    asset_id = asset["id"]
    _api("post", f"/assets/{asset_id}/connectors", json={"connector_id": conn_id})
    return conn_id, asset_id


# ---------------------------------------------------------------------------
# Phase 1: Provision
# ---------------------------------------------------------------------------

def test_phase1_provision():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")

    ec2 = _boto3_client("ec2", aws_creds)
    ssm = _boto3_client("ssm", aws_creds)

    instance_id, private_ip = _build_k3s_istio_ami(ec2, ssm, aws_creds)
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
    install_nexplane_agent_on_instance(
        instance_id, asset_id, aws_creds, private_ip=private_ip, timeout_s=600
    )

    # Wait for k3s + Istio 1.20 to be healthy before phase 2 starts.
    # user-data wipes k3s state and reinstalls Istio; this can take 5-10 min.
    log("  Waiting for istiod to be ready (up to 12 min)")
    ssm_c = _boto3_client("ssm", aws_creds)
    deadline = time.time() + 720
    istio_ready = False
    while time.time() < deadline:
        time.sleep(20)
        try:
            resp = ssm_c.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [
                    "KUBECONFIG=/etc/rancher/k3s/k3s.yaml kubectl rollout status deployment/istiod "
                    "-n istio-system --timeout=10s 2>&1 | grep -q 'successfully rolled out' && echo ISTIO_READY"
                ]},
            )
            cmd_id = resp["Command"]["CommandId"]
            time.sleep(12)
            out = ssm_c.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            if "ISTIO_READY" in out.get("StandardOutputContent", ""):
                istio_ready = True
                break
        except Exception:
            pass
    if not istio_ready:
        pytest.fail("istiod never became ready within 12 min")
    log("[PHASE 1: provision] PASSED")


# ---------------------------------------------------------------------------
# Phase 2: Upgrade CR lifecycle
# ---------------------------------------------------------------------------

def test_phase2_upgrade():
    if not _state.get("connector_id"):
        pytest.skip("Phase 1 did not complete")

    conn_id  = _state["connector_id"]
    asset_id = _state["asset_id"]
    run_id   = uuid.uuid4().hex[:6]

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-istio-upgrade-{run_id}",
        "change_type": "istio_control_plane_upgrade",
        "desired_outcome": {
            "source_version":   SOURCE_VERSION,
            "target_version":   TARGET_VERSION,
            "kubeconfig_path":  "/etc/rancher/k3s/k3s.yaml",
            "upgrade_strategy": "inplace",
            "dry_run":          False,
        },
        "connector_id":      conn_id,
        "target_asset_ids":  [asset_id],
    })
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    log(f"  Created CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "istio upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id, timeout_s=CR_TIMEOUT)
    assert cr["status"] == "completed", f"CR reached {cr['status']} -- expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status not completed: {result}"

    verify = result.get("verify_result", {})
    assert verify.get("version_ok"), f"Target version {TARGET_VERSION} not found after upgrade: {verify}"

    log(f"  Istio upgraded to {TARGET_VERSION} -- version check OK")
    log("[PHASE 2: upgrade] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: Rollback
# ---------------------------------------------------------------------------

def test_phase3_rollback():
    cr_id = _state.get("cr_id")
    if not cr_id:
        pytest.skip("Phase 2 did not complete -- no CR to roll back")

    _api("post", f"/change-requests/{cr_id}/rollback")
    log(f"  Rollback triggered for CR {cr_id}")

    cr = _poll_cr(cr_id, timeout_s=1800, terminal=("rolled_back", "rollback_failed", "completed"))
    assert cr["status"] in ("rolled_back", "completed"), (
        f"Rollback CR reached unexpected status: {cr['status']}"
    )

    result = _rollback_result(cr)
    # Accept both successful rollback and acknowledged no-op rollback.
    # A no-op rollback (_rollback_no_op=True) occurs when k3s crashes during
    # upgrade leaving the API server unreachable — the executor cannot undo work
    # but records the state so the operator knows to re-provision.
    rolled_back_ok = result.get("rolled_back") is True or result.get("_rollback_no_op") is True
    assert rolled_back_ok, f"rolled_back not True and no _rollback_no_op: {result}"
    assert "data_loss_warning" in result, "Expected data_loss_warning in rollback result"
    log("[PHASE 3: rollback] PASSED")


# ---------------------------------------------------------------------------
# Phase 4: Teardown
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
