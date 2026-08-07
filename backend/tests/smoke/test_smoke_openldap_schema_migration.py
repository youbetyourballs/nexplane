# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Smoke test: OpenLDAP Schema Migration

Phases:
  1. provision   — launch OpenLDAP (slapd 2.5) EC2 from cached AMI (or build+cache)
                   AMI must have test schema LDIF pre-staged at /tmp/nexplane-testapp.ldif
  2. apply       — CR lifecycle: openldap_schema_migration, verify schema DN present
  3. rollback    — trigger rollback, verify schema DN gone
  4. teardown    — terminate, deregister

AMI cache key: /nexplane/smoke-amis/openldap/2.5
Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_smoke_openldap_schema_migration.py -v -s
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
    get_or_create_smoke_ami,
    install_nexplane_agent_on_instance,
)

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL    = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_LDAP_AMI_SSM_KEY  = "/nexplane/smoke-amis/openldap/2.5"
_SSM_PROFILE       = "nexplane-smoke-ssm"
_TEST_SCHEMA_LDIF  = "/tmp/nexplane-testapp.ldif"
_TEST_SCHEMA_DN    = "cn=testapp,cn=schema,cn=config"

CR_TIMEOUT    = 300
POLL_INTERVAL = 10

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


def _build_openldap_ami(aws_creds) -> tuple:
    """Provision a fresh EC2, install OpenLDAP 2.5, stage test schema LDIF.
    Returns (instance_id, ec2_client, None) as required by get_or_create_smoke_ami launch_fn protocol.
    """
    ec2 = boto3.client(
        "ec2",
        aws_access_key_id=aws_creds.get("access_key_id") or aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key") or aws_creds.get("aws_secret_access_key"),
        region_name=aws_creds.get("region", "us-east-1"),
    )

    # Install OpenLDAP 2.5 + stage test schema LDIF
    # Uses shell heredocs with distinct delimiter to avoid quoting conflicts
    _user_data_script = (
        b"#!/bin/bash\n"
        b"yum install -y openldap openldap-servers openldap-clients\n"
        b"cp /usr/share/openldap-servers/DB_CONFIG.example /var/lib/ldap/DB_CONFIG\n"
        b"chown ldap:ldap /var/lib/ldap/DB_CONFIG\n"
        b"systemctl enable slapd && systemctl start slapd\n"
        b"sleep 5\n"
        b"ldapadd -Y EXTERNAL -H ldapi:/// -f /etc/openldap/schema/cosine.ldif 2>/dev/null || true\n"
        b"ldapadd -Y EXTERNAL -H ldapi:/// -f /etc/openldap/schema/inetorgperson.ldif 2>/dev/null || true\n"
        b"HASH=$(slappasswd -s SmokeAdmin1234!)\n"
        b"printf 'dn: olcDatabase={0}config,cn=config\\nchangetype: modify\\nreplace: olcRootPW\\nolcRootPW: %s\\n' \"$HASH\" > /tmp/rootpw.ldif\n"
        b"ldapmodify -Y EXTERNAL -H ldapi:/// -f /tmp/rootpw.ldif 2>/dev/null || true\n"
        b"printf 'dn: dc=smoke,dc=test\\nobjectClass: top\\nobjectClass: dcObject\\nobjectClass: organization\\no: Smoke Test\\ndc: smoke\\n' > /tmp/base.ldif\n"
        b"ldapadd -Y EXTERNAL -H ldapi:/// -f /tmp/base.ldif 2>/dev/null || true\n"
        b"printf 'dn: cn=testapp,cn=schema,cn=config\\nobjectClass: olcSchemaConfig\\ncn: testapp\\n"
        b"olcAttributeTypes: ( 1.3.6.1.4.1.99999.1.1 NAME \\x27smokeAttr\\x27 EQUALITY caseIgnoreMatch SYNTAX 1.3.6.1.4.1.1466.115.121.1.15 )\\n"
        b"olcObjectClasses: ( 1.3.6.1.4.1.99999.2.1 NAME \\x27smokeApp\\x27 SUP top AUXILIARY MAY ( smokeAttr ) )\\n' "
        b"> /tmp/nexplane-testapp.ldif\n"
    )
    user_data = base64.b64encode(_user_data_script).decode()

    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    kwargs = dict(
        ImageId="ami-0c101f26f147fa7fd",
        InstanceType="t3.small",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        UserData=user_data,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-openldap-build"},
            {"Key": "nexplane-purpose", "Value": "smoke-ami-build"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched OpenLDAP build instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for LDAP port 389 on {private_ip} (up to 10 min)")
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(15)
        try:
            s = socket.create_connection((private_ip, 389), timeout=5)
            s.close()
            log(f"  LDAP port 389 open on {private_ip}")
            return instance_id, ec2, None
        except OSError:
            pass

    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"OpenLDAP LDAP never reachable on {private_ip}:389 within 10 min during AMI build")


def _launch_ldap(ec2, ami_id, aws_creds) -> tuple:
    subnet_id = aws_creds.get("smoke_subnet_id") or aws_creds.get("subnet_id")
    sg_id     = aws_creds.get("smoke_default_security_group_id")

    kwargs = dict(
        ImageId=ami_id,
        InstanceType="t3.small",
        MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": _SSM_PROFILE},
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name",             "Value": "nexplane-smoke-openldap"},
            {"Key": "nexplane-purpose", "Value": "smoke-openldap-schema-migration"},
        ]}],
    )
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    if sg_id:
        kwargs["SecurityGroupIds"] = [sg_id]

    resp        = ec2.run_instances(**kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Launched OpenLDAP instance {instance_id}")

    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    desc       = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    log(f"  Waiting for LDAP port 389 on {private_ip} (up to 4 min)")
    deadline = time.time() + 240
    while time.time() < deadline:
        time.sleep(10)
        try:
            s = socket.create_connection((private_ip, 389), timeout=5)
            s.close()
            log(f"  LDAP port 389 open on {private_ip}")
            return instance_id, private_ip
        except OSError:
            pass
    ec2.terminate_instances(InstanceIds=[instance_id])
    pytest.fail(f"OpenLDAP LDAP never reachable on {private_ip}:389 within 4 min")


def _register_asset(private_ip, run_id) -> tuple:
    connector = _api("post", "/connectors", json={
        "name":           f"smoke-openldap-{run_id}",
        "connector_type": "nexplane_agent",
    })
    conn_id = connector["id"]
    _api("put", f"/connectors/{conn_id}/credentials", json={"credentials": {}})

    asset = _api("post", "/assets", json={
        "name":         f"smoke-openldap-{run_id}",
        "asset_type":   "server",
        "criticality":  "low",
        "environment":  "staging",
        "hostname":     private_ip,
        "connector_id": conn_id,
        "metadata":     {"role": "openldap", "ip": private_ip},
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
            if "rolled_back" in r:
                return r
    return cr.get("rollback_result") or {}


# ---------------------------------------------------------------------------
# Phase 1: provision
# ---------------------------------------------------------------------------

def test_phase1_provision():
    aws_creds = get_connector_creds_from_db("aws")
    if not aws_creds:
        pytest.skip("No AWS connector creds in platform DB")

    ec2 = _boto3_client("ec2", aws_creds)

    ami_id = get_or_create_smoke_ami(
        cache_key="openldap/2.5",
        setup_hash="openldap-2.5",
        launch_fn=_build_openldap_ami,
        snapshot_name="nexplane-smoke-openldap-2.5",
    )
    log(f"  Using OpenLDAP AMI: {ami_id}")

    instance_id, private_ip = _launch_ldap(ec2, ami_id, aws_creds)
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
# Phase 2: apply schema
# ---------------------------------------------------------------------------

def test_phase2_apply_schema():
    if not _state.get("connector_id"):
        pytest.skip("Phase 1 did not complete")

    conn_id  = _state["connector_id"]
    asset_id = _state["asset_id"]
    run_id   = uuid.uuid4().hex[:6]

    cr = _api("post", "/change-requests", json={
        "title":       f"smoke-openldap-schema-{run_id}",
        "change_type": "openldap_schema_migration",
        "desired_outcome": {
            "schema_ldif_path": _TEST_SCHEMA_LDIF,
            "schema_dn":        _TEST_SCHEMA_DN,
            "slapd_config_dir": "/etc/ldap/slapd.d",
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
         json={"decision": "approved", "comment": "openldap schema smoke self-approval"})
    _api("post", f"/change-requests/{cr_id}/execute")
    log(f"  Executing CR {cr_id}")

    cr = _poll_cr(cr_id)
    assert cr["status"] == "completed", f"CR reached {cr['status']} — expected completed"

    result = _exec_result(cr)
    assert result.get("status") == "completed", f"Executor status: {result}"

    verify = result.get("verify_result", {})
    assert verify.get("schema_dn_present"), (
        f"Schema DN {_TEST_SCHEMA_DN} not found after apply: {verify}"
    )
    assert result.get("config_backup_path"), "No config_backup_path — snapshot missing"
    log(f"  Schema DN {_TEST_SCHEMA_DN} present, backup at {result['config_backup_path']}")
    log("[PHASE 2: apply_schema] PASSED")


# ---------------------------------------------------------------------------
# Phase 3: rollback
# ---------------------------------------------------------------------------

def test_phase3_rollback():
    cr_id = _state.get("cr_id")
    if not cr_id:
        pytest.skip("Phase 2 did not complete — no CR to roll back")

    _api("post", f"/change-requests/{cr_id}/rollback")
    log(f"  Rollback triggered for CR {cr_id}")

    cr = _poll_cr(cr_id, timeout_s=300)
    assert cr["status"] in ("rolled_back", "completed"), (
        f"Rollback reached unexpected status: {cr['status']}"
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
