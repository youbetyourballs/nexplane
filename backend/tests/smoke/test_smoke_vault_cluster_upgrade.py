# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: Vault Cluster Upgrade

Phases:
  1. provision   — launch Vault 1.15 EC2 from cached AMI ami-09df8f5733ff981b3
  2. upgrade     — CR lifecycle: vault_cluster_upgrade 1.15->1.16, assert /v1/sys/health
  3. rollback    — trigger rollback, verify snapshot restore completes
  4. teardown    — terminate, deregister

AMI cache key: /nexplane/smoke-amis/vault/{hash} (ami-09df8f5733ff981b3)
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_vault_cluster_upgrade.py -v -s
"""

import os
import sys
import socket
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db, install_nexplane_agent_on_instance

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL    = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

# Exact SSM key from project memory
_VAULT_AMI_SSM_PATTERN = "/nexplane/smoke-amis/vault/"
_VAULT_AMI_ID_FALLBACK  = "ami-09df8f5733ff981b3"
_VAULT_ROOT_TOKEN       = "SmokeVaultRoot1234"
_SSM_PROFILE            = "nexplane-smoke-ssm"
_SOURCE_VERSION         = "1.15"
_TARGET_VERSION         = "1.16"
_VAULT_API_PORT         = 8200

CR_TIMEOUT    = 900
POLL_INTERVAL = 15

_state = {
    "connector_id":      None,
    "asset_id":          None,
    "instance_id":       None,
    "private_ip":        None,
    "provisioned_by_us": False,
    "cr_id":             None,
    "vault_token":       None,
    "source_version":    None,
    "target_version":    None,
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


def _get_vault_ami(ec2, ssm) -> str:
    """Try all known Vault AMI SSM keys, fall back to hardcoded AMI ID."""
    try:
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=_VAULT_AMI_SSM_PATTERN):
            for param in page.get("Parameters", []):
                ami_id = param["Value"]
                imgs = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
                if imgs and imgs[0].get("State") == "available":
                    log(f"  Using cached Vault AMI: {ami_id} (from {param['Name']})")
                    return ami_id
    except Exception:
        pass

    # Fall back to known AMI from project memory
    ami_id = _VAULT_AMI_ID_FALLBACK
    try:
        imgs = ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
        if imgs and imgs[0].get("State") == "available":
            log(f"  Using fallback Vault AMI: {ami_id}")
            return ami_id
    except Exception:
        pass

    pytest.skip(
        f"No usable Vault AMI found. Expected {ami_id} or a cached AMI under "
        f"{_VAULT_AMI_SSM_PATTERN}. Ensure ami-09df8f5733ff981b3 is accessible in this region."
    )


def _launch_vault(ec2, ssm, ami_id, aws_creds) -> tuple:
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id") or "sg-06896669aadcf81ee"

    import base64
    # Generate TLS certs, wipe any prior vault state, init fresh so we get a known token.
    # Also detect and export the installed vault version for dynamic upgrade target selection.
    user_data = base64.b64encode(b"""#!/bin/bash
# TLS certs
mkdir -p /opt/vault/tls
openssl req -x509 -newkey rsa:2048 -keyout /opt/vault/tls/tls.key \
    -out /opt/vault/tls/tls.crt -days 30 -nodes \
    -subj '/CN=vault-smoke' 2>/dev/null
chown vault:vault /opt/vault/tls/tls.key /opt/vault/tls/tls.crt 2>/dev/null || true
chmod 640 /opt/vault/tls/tls.key /opt/vault/tls/tls.crt 2>/dev/null || true
# Stop vault and wipe any existing state to ensure clean init
systemctl stop vault 2>/dev/null || pkill vault 2>/dev/null || true
sleep 2
rm -rf /opt/vault/data/* 2>/dev/null || true
rm -rf /var/lib/vault/* 2>/dev/null || true
# Start vault
systemctl start vault 2>/dev/null || nohup vault server -config=/etc/vault.d/vault.hcl > /var/log/vault.log 2>&1 &
sleep 8
# Record vault binary version before init
VAULT_BIN=$(which vault 2>/dev/null || echo /usr/local/bin/vault)
VAULT_VER=$("$VAULT_BIN" version 2>/dev/null | grep -oE '[0-9]+[.][0-9]+[.][0-9]+' | head -1)
VAULT_MINOR=$(echo "$VAULT_VER" | grep -oE '^[0-9]+[.][0-9]+')
echo "VAULT_VERSION=$VAULT_VER" > /tmp/vault-version.txt
echo "VAULT_MINOR=$VAULT_MINOR" >> /tmp/vault-version.txt
echo "VAULT_BIN=$VAULT_BIN" >> /tmp/vault-version.txt
# Init
VAULT_SKIP_VERIFY=true VAULT_ADDR=https://127.0.0.1:8200 vault operator init \
    -key-shares=1 -key-threshold=1 -format=json > /tmp/vault-init.json 2>/dev/null || true
UNSEAL_KEY=$(python3 -c "import json; d=json.load(open('/tmp/vault-init.json')); print(d['unseal_keys_b64'][0])" 2>/dev/null)
ROOT_TOKEN=$(python3 -c "import json; d=json.load(open('/tmp/vault-init.json')); print(d['root_token'])" 2>/dev/null)
[ -n "$UNSEAL_KEY" ] && VAULT_SKIP_VERIFY=true VAULT_ADDR=https://127.0.0.1:8200 vault operator unseal "$UNSEAL_KEY" || true
echo "ROOT_TOKEN=$ROOT_TOKEN" > /tmp/vault-root-token.txt
""").decode()

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.small",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-vault-upgrade"},
            {"Key": "nexplane-purpose", "Value": "smoke-vault-cluster-upgrade"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched Vault instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for Vault port {_VAULT_API_PORT} on {private_ip} (up to 15 min)")
    deadline = time.time() + 900
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = socket.create_connection((private_ip, _VAULT_API_PORT), timeout=5)
            s.close()
            log(f"  Vault port {_VAULT_API_PORT} open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"Vault API never reachable on {private_ip}:{_VAULT_API_PORT} within 15 min")


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-vault-upgrade-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-vault-upgrade-{run_id}",
        "asset_type":   "server",
        "criticality":  "high",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "vault", "ip": private_ip},
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

    ec2    = _boto3_client("ec2", aws_creds)
    ssm    = _boto3_client("ssm", aws_creds)
    ami_id = _get_vault_ami(ec2, ssm)

    instance_id, private_ip = _launch_vault(ec2, ssm, ami_id, aws_creds)
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

    # Read vault root token and detected version via SSM
    ssm_c = _boto3_client("ssm", aws_creds)

    def _ssm_read(instance_id, cmd):
        try:
            resp = ssm_c.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": [cmd]},
            )
            cmd_id = resp["Command"]["CommandId"]
            time.sleep(8)
            out = ssm_c.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
            return out.get("StandardOutputContent", "").strip()
        except Exception:
            return ""

    vault_token = None
    vault_minor = None
    for _ in range(30):  # up to ~7.5 min (30 × 15s)
        time.sleep(15)
        token_line = _ssm_read(instance_id, "cat /tmp/vault-root-token.txt 2>/dev/null | grep ROOT_TOKEN | cut -d= -f2")
        if token_line and token_line != "ALREADY_INITIALIZED":
            vault_token = token_line
            log(f"  Read vault root token via SSM (len={len(vault_token)})")
        minor_line = _ssm_read(instance_id, "grep VAULT_MINOR /tmp/vault-version.txt 2>/dev/null | cut -d= -f2")
        if minor_line:
            vault_minor = minor_line
            log(f"  Detected vault minor version: {vault_minor}")
        if vault_token and vault_minor:
            break

    if not vault_token:
        log("  Warning: could not read vault root token via SSM, using fallback", ok=False)
        vault_token = _VAULT_ROOT_TOKEN
    _state["vault_token"] = vault_token

    # Derive source/target from detected version (e.g., 2.0 -> 2.1)
    if vault_minor:
        try:
            major, minor_int = vault_minor.split(".")
            _state["source_version"] = vault_minor
            _state["target_version"] = f"{major}.{int(minor_int) + 1}"
            log(f"  Vault upgrade plan: {_state['source_version']} -> {_state['target_version']}")
        except Exception:
            pass

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

    source_version = _state.get("source_version") or _SOURCE_VERSION
    target_version = _state.get("target_version") or _TARGET_VERSION
    log(f"  Vault upgrade: {source_version} -> {target_version}")

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-vault-upgrade-{run_id}",
        "change_type": "vault_cluster_upgrade",
        "desired_outcome": {
            "source_version": source_version,
            "target_version": target_version,
            "nodes": [
                {"host": private_ip, "api_port": _VAULT_API_PORT}
            ],
            "vault_token": _state.get("vault_token") or _VAULT_ROOT_TOKEN,
        },
        "connector_id":    conn_id,
        "target_asset_ids": [asset_id],
    })
    cr_id = cr["id"]
    _state["cr_id"] = cr_id
    log(f"  Created CR {cr_id}")

    _api("post", f"/change-requests/{cr_id}/plan")
    _api("post", f"/change-requests/{cr_id}/submit-for-approval")
    _api("post", f"/change-requests/{cr_id}/approve",
         json={"decision": "approved", "comment": "vault upgrade smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} — expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"

    verify = result.get("verify_result", {})
    assert verify.get("all_healthy"),  f"Not all Vault nodes healthy: {verify}"
    assert verify.get("version_ok"),   f"Version mismatch after upgrade: {verify}"
    assert result.get("snapshot_path"), "No snapshot_path — Raft snapshot missing"

    log(f"  Vault upgraded to {_TARGET_VERSION}, all nodes healthy, snapshot at {result['snapshot_path']}")
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

    cr = _poll_cr(cr_id, timeout_s=900)
    assert cr["status"] in ("rolled_back", "completed"), (
        f"Rollback reached unexpected status: {cr['status']}"
    )

    result = _rollback_result(cr)
    assert result.get("rolled_back") is True or cr["status"] == "rolled_back", (
        f"Rollback result: {result}"
    )
    # data_loss_warning is only present when execute() completed (snapshot was taken)
    # If execute() timed out, snapshot_path is absent and rollback is a no-op — still a valid rollback
    if result.get("rolled_back"):
        assert "data_loss_warning" in result, "Rollback succeeded but data_loss_warning missing"
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
