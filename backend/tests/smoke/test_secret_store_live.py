# backend/tests/smoke/test_secret_store_live.py
"""
Live smoke test for external secret store backends.

Vault leg: spins up a t3.small with vault-dev AMI (cached), tests VaultKVBackend roundtrip.
ASM leg: tests AWSSecretsManagerBackend against real AWS Secrets Manager (runner IAM role).
Regression leg: verifies FernetBackend still works with a freshly encrypted value.

Run from EC2 runner:
    pytest tests/smoke/test_secret_store_live.py -v -s
"""
import json
import os
import time
import uuid
import boto3


SMOKE_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
SMOKE_DATA = {"username": "smokeuser", "password": "smoke-secret-value-12345", "host": "10.0.0.1"}


# ── Vault leg ─────────────────────────────────────────────────────────────────

VAULT_SETUP_SCRIPT = r"""#!/bin/bash
set -e
which vault 2>/dev/null || {
    yum install -y yum-utils 2>/dev/null || apt-get install -y gpg 2>/dev/null || true
    curl -fsSL https://rpm.releases.hashicorp.com/AmazonLinux/hashicorp.repo \
        -o /etc/yum.repos.d/hashicorp.repo 2>/dev/null || true
    yum install -y vault 2>/dev/null || {
        curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp.gpg
        echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
            > /etc/apt/sources.list.d/hashicorp.list
        apt-get update -qq && apt-get install -y vault
    }
}
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=nexplane-smoke-root
pkill vault 2>/dev/null || true
sleep 2
nohup vault server -dev -dev-root-token-id=nexplane-smoke-root -dev-listen-address=0.0.0.0:8200 \
    > /var/log/vault-dev.log 2>&1 &
sleep 5
vault kv enable-versioning secret 2>/dev/null || true
echo "VAULT_SETUP_COMPLETE"
"""

import hashlib
VAULT_SETUP_HASH = hashlib.sha256(VAULT_SETUP_SCRIPT.encode()).hexdigest()[:12]


def _get_or_create_smoke_ami_safe(ssm_client, ec2_client, instance_id, name, setup_hash):
    try:
        from run_on_ec2 import get_or_create_smoke_ami as _fn
        return _fn(ssm_client, ec2_client, instance_id, name, setup_hash)
    except ImportError:
        pass
    try:
        from smoke.run_on_ec2 import get_or_create_smoke_ami as _fn2
        return _fn2(ssm_client, ec2_client, instance_id, name, setup_hash)
    except ImportError:
        pass


def _launch_vault_instance(ec2_client, ssm_client, iam_client):
    """Launch a t3.small for Vault, returning (instance_id, private_ip)."""
    try:
        from run_on_ec2 import get_default_vpc_subnet, get_ssm_instance_profile
    except ImportError:
        from smoke.run_on_ec2 import get_default_vpc_subnet, get_ssm_instance_profile

    _, subnet_id = get_default_vpc_subnet(ec2_client, instance_type="t3.small")
    instance_profile = get_ssm_instance_profile(iam_client) or "NexplaneEC2TestProfile"

    # Use the default VPC security group (allows intra-VPC traffic)
    vpc_id = ec2_client.describe_vpcs(
        Filters=[{"Name": "isDefault", "Values": ["true"]}]
    )["Vpcs"][0]["VpcId"]
    sgs = ec2_client.describe_security_groups(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "group-name", "Values": ["default"]}]
    )["SecurityGroups"]
    sg_id = sgs[0]["GroupId"]

    ssm_key = f"/nexplane/smoke-amis/vault-dev/{VAULT_SETUP_HASH}"
    cached_ami = None
    try:
        resp = ssm_client.get_parameter(Name=ssm_key)
        cached_ami = resp["Parameter"]["Value"]
        print(f"  Using cached vault-dev AMI: {cached_ami}", flush=True)
    except ssm_client.exceptions.ParameterNotFound:
        pass

    ami_id = cached_ami or "ami-0c02fb55956c7d316"  # Amazon Linux 2 fallback

    resp = ec2_client.run_instances(
        ImageId=ami_id,
        InstanceType="t3.small",
        MinCount=1, MaxCount=1,
        SubnetId=subnet_id,
        SecurityGroupIds=[sg_id],
        IamInstanceProfile={"Name": instance_profile},
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "Name", "Value": "nexplane-smoke-vault-backend"},
                {"Key": "nxp-ec2-test-runner", "Value": "true"},
                {"Key": "nxp-smoke-temp", "Value": "true"},
            ]
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    print(f"  Launched Vault instance: {instance_id}", flush=True)

    ec2_client.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    private_ip = ec2_client.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            r = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["echo ready"]}, TimeoutSeconds=10)
            time.sleep(5)
            out = ssm_client.get_command_invocation(
                CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
            if out["Status"] == "Success":
                break
        except Exception:
            pass
        time.sleep(10)

    if not cached_ami:
        resp_s = ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [VAULT_SETUP_SCRIPT]}, TimeoutSeconds=120)
        # Poll until command completes (up to 180s)
        deadline_setup = time.time() + 180
        out_s = {}
        while time.time() < deadline_setup:
            try:
                out_s = ssm_client.get_command_invocation(
                    CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
                if out_s.get("Status") in ("Success", "Failed", "TimedOut", "Cancelled"):
                    break
            except Exception:
                pass
            time.sleep(10)
        if "VAULT_SETUP_COMPLETE" in out_s.get("StandardOutputContent", ""):
            print("  Vault setup complete, caching AMI", flush=True)
            _get_or_create_smoke_ami_safe(ssm_client, ec2_client, instance_id, "vault-dev", VAULT_SETUP_HASH)
        else:
            print(f"  WARNING: Vault setup may not have completed: {out_s.get('StandardOutputContent', '')[:200]}", flush=True)
    else:
        start_cmd = (
            "export VAULT_ADDR=http://localhost:8200 VAULT_TOKEN=nexplane-smoke-root && "
            "pkill vault 2>/dev/null || true && sleep 2 && "
            "nohup vault server -dev -dev-root-token-id=nexplane-smoke-root "
            "-dev-listen-address=0.0.0.0:8200 > /var/log/vault-dev.log 2>&1 & sleep 5 && echo VAULT_RESTARTED"
        )
        r2 = ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [start_cmd]}, TimeoutSeconds=60)
        # Poll for restart completion
        deadline_restart = time.time() + 60
        out_r = {}
        while time.time() < deadline_restart:
            try:
                out_r = ssm_client.get_command_invocation(
                    CommandId=r2["Command"]["CommandId"], InstanceId=instance_id)
                if out_r.get("Status") in ("Success", "Failed", "TimedOut", "Cancelled"):
                    break
            except Exception:
                pass
            time.sleep(5)
        if "VAULT_RESTARTED" not in out_r.get("StandardOutputContent", ""):
            print(f"  WARNING: Vault restart output: {out_r.get('StandardOutputContent', '')[:100]}", flush=True)

    return instance_id, private_ip


def test_vault_backend_live(request):
    """VaultKVBackend roundtrip against a real Vault dev instance."""
    print("\n[VAULT] Starting Vault backend live test", flush=True)

    ec2 = boto3.client("ec2", region_name=SMOKE_REGION)
    ssm = boto3.client("ssm", region_name=SMOKE_REGION)
    iam = boto3.client("iam", region_name=SMOKE_REGION)

    instance_id, private_ip = _launch_vault_instance(ec2, ssm, iam)
    vault_addr = f"http://{private_ip}:8200"
    vault_token = "nexplane-smoke-root"

    def teardown():
        try:
            ec2.terminate_instances(InstanceIds=[instance_id])
            print(f"  [VAULT] Terminated {instance_id}", flush=True)
        except Exception as e:
            print(f"  [VAULT] Teardown warning: {e}", flush=True)
    request.addfinalizer(teardown)

    from app.services.backends.vault_kv_backend import VaultKVBackend
    backend = VaultKVBackend(addr=vault_addr, token=vault_token)

    connector_id = str(uuid.uuid4())
    print(f"  [VAULT] Writing secret for connector {connector_id}", flush=True)
    stored_path = backend.encrypt_json(SMOKE_DATA, connector_id=connector_id)
    print(f"  [VAULT] Stored at: {stored_path}", flush=True)

    retrieved = backend.decrypt_json(stored_path)
    assert retrieved == SMOKE_DATA, f"Vault roundtrip failed: got {retrieved}"
    print("  [VAULT] Roundtrip OK", flush=True)

    import hvac
    client = hvac.Client(url=vault_addr, token=vault_token)
    raw = client.secrets.kv.v2.read_secret_version(
        path=f"nexplane/connectors/{connector_id}", mount_point="secret"
    )
    assert raw["data"]["data"] == SMOKE_DATA
    print("  [VAULT] Direct Vault read OK", flush=True)

    print("[VAULT] PASSED", flush=True)


# ── AWS Secrets Manager leg ───────────────────────────────────────────────────

def test_asm_backend_live():
    """AWSSecretsManagerBackend roundtrip against real AWS Secrets Manager."""
    print("\n[ASM] Starting ASM backend live test", flush=True)

    from app.services.backends.aws_secrets_manager_backend import AWSSecretsManagerBackend
    backend = AWSSecretsManagerBackend(region=SMOKE_REGION)

    connector_id = str(uuid.uuid4())
    print(f"  [ASM] Writing secret for connector {connector_id}", flush=True)
    stored_name = backend.encrypt_json(SMOKE_DATA, connector_id=connector_id)
    print(f"  [ASM] Stored as: {stored_name}", flush=True)

    retrieved = backend.decrypt_json(stored_name)
    assert retrieved == SMOKE_DATA, f"ASM roundtrip failed: got {retrieved}"
    print("  [ASM] Roundtrip OK", flush=True)

    sm = boto3.client("secretsmanager", region_name=SMOKE_REGION)
    raw = sm.get_secret_value(SecretId=stored_name)
    assert json.loads(raw["SecretString"]) == SMOKE_DATA
    print("  [ASM] Direct Secrets Manager read OK", flush=True)

    updated_data = {**SMOKE_DATA, "password": "updated-smoke-value-99999"}
    stored_name = backend.encrypt_json(updated_data, connector_id=connector_id)
    assert backend.decrypt_json(stored_name) == updated_data
    print("  [ASM] Update roundtrip OK", flush=True)

    backend.delete_secret(stored_name)
    print("  [ASM] Secret deleted", flush=True)
    print("[ASM] PASSED", flush=True)


# ── Fernet regression leg ─────────────────────────────────────────────────────

def test_fernet_backend_regression():
    """FernetBackend still works after other backends are present."""
    print("\n[FERNET] Starting Fernet regression test", flush=True)

    from app.services.backends.fernet_backend import FernetBackend
    backend = FernetBackend(secret_key="regression-smoke-test-key")

    data = {"host": "192.168.1.1", "username": "admin", "password": "fernet-regression-ok"}
    encrypted = backend.encrypt_json(data)

    assert "fernet-regression-ok" not in encrypted
    assert "admin" not in encrypted
    print(f"  [FERNET] Encrypted (first 40 chars): {encrypted[:40]}...", flush=True)

    result = backend.decrypt_json(encrypted)
    assert result == data
    print("  [FERNET] Roundtrip OK", flush=True)

    from app.services.secrets_service import SecretsService
    old_svc = SecretsService(secret_key="regression-smoke-test-key")
    legacy_encrypted = old_svc.encrypt_json(data)
    result2 = backend.decrypt_json(legacy_encrypted)
    assert result2 == data
    print("  [FERNET] Legacy SecretsService compatibility OK", flush=True)

    print("[FERNET] PASSED", flush=True)
