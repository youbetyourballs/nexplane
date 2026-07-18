# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Certificate Rotation Smoke Test

Phases:
  1. HAPPY PATH — zero-dependent run, cert issued, FILO rollback
  2. TYPE-B CONSUMER — Kubernetes secret rotation + rollback (self-provisions kind if needed)
  3. COMPROMISE TRIGGER — rollback_strategy must be 'reissue'
  4. VERIFY FAILURE → PAUSED → ROLLBACK — synthetic unreachable host
  5. AUTO-TRIGGER — expiry worker creates draft CR for near-expiry cert via CertificateInventory

Run from EC2:
  docker exec nexplane-backend-1 python -m pytest \\
    /app/tests/smoke/test_certificate_rotation_smoke.py -v -s
"""

import base64
import hashlib
import os
import re
import ssl
import socket
import sys
import time
import uuid

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import (
    NexplaneClient,
    _get_aws_boto3_client,
    get_connector_creds_from_db,
    log,
)

BASE_URL = os.environ.get("PLATFORM_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")
SMOKE_SUBJECT = "nexplane-smoke-cert.internal"
EXPIRING_SUBJECT = "nexplane-smoke-expiring.internal"
EXEC_TIMEOUT = 180
ROLLBACK_TIMEOUT = 120

# Kind cluster provisioning constants
KUBE_API_PORT = 6443
AL2023_AMI = "ami-0953476d60561c955"
S3_TOOLS_BUCKET = "nexplane-agent-downloads"
KUBECTL_VERSION = "v1.29.0"
KIND_VERSION = "v0.24.0"
KIND_NODE_IMAGE = "kindest/node:v1.30.0"

_SETUP_SCRIPT_CACHE_KEY = b"cert-kind-0.24.0-v1"

SETUP_SCRIPT = f"""
set -e
PRIVATE_IP=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)
echo "Private IP: $PRIVATE_IP"
dnf install -y docker 2>/dev/null || apt-get install -y docker.io 2>/dev/null || true
systemctl enable docker && systemctl start docker
for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break || sleep 2; done
aws s3 cp s3://{S3_TOOLS_BUCKET}/smoke-tools/kubectl-{KUBECTL_VERSION} /usr/local/bin/kubectl
chmod +x /usr/local/bin/kubectl
aws s3 cp s3://{S3_TOOLS_BUCKET}/smoke-tools/kind-{KIND_VERSION} /usr/local/bin/kind
chmod +x /usr/local/bin/kind
aws s3 cp s3://{S3_TOOLS_BUCKET}/smoke-tools/kindest-node-v1.30.0.tar.gz - | docker load
sysctl -w net.ipv4.ip_forward=1
cat > /tmp/kind-config.yaml <<KINDEOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  apiServerAddress: "0.0.0.0"
  apiServerPort: 6443
KINDEOF
echo "Creating kind cluster..."
kind create cluster --name smoke-test --config /tmp/kind-config.yaml --wait 300s \\
  --image kindest/node:v1.30.0 >/tmp/kind-out.txt 2>&1 \\
  && echo "KIND_CLUSTER_READY" \\
  || {{ echo "KIND_FAILED"; tail -30 /tmp/kind-out.txt; exit 1; }}
iptables -I INPUT -p tcp --dport 6443 -j ACCEPT 2>/dev/null || true
kind get kubeconfig --name smoke-test > /tmp/smoke-kubeconfig.yaml 2>/dev/null
mkdir -p /root/.kube && cp /tmp/smoke-kubeconfig.yaml /root/.kube/config
echo "K8S_RBAC_SETUP_COMPLETE"
"""

_RESTART_SCRIPT = """
set -e
sysctl -w net.ipv4.ip_forward=1
systemctl start docker
for i in $(seq 1 20); do docker info >/dev/null 2>&1 && break || sleep 3; done
kind delete cluster --name smoke-test 2>/dev/null || true
cat > /tmp/kind-config.yaml <<KINDEOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
networking:
  apiServerAddress: "0.0.0.0"
  apiServerPort: 6443
KINDEOF
echo "Creating kind cluster..."
kind create cluster --name smoke-test --config /tmp/kind-config.yaml --wait 300s \\
  --image kindest/node:v1.30.0 >/tmp/kind-out.txt 2>&1 \\
  && echo "KIND_CLUSTER_READY" \\
  || { echo "KIND_FAILED"; tail -20 /tmp/kind-out.txt; exit 1; }
iptables -I INPUT -p tcp --dport 6443 -j ACCEPT 2>/dev/null || true
kind get kubeconfig --name smoke-test > /tmp/smoke-kubeconfig.yaml 2>/dev/null
mkdir -p /root/.kube && cp /tmp/smoke-kubeconfig.yaml /root/.kube/config
echo "RESTART_COMPLETE"
"""

pytestmark = pytest.mark.SMOKE


# ---------------------------------------------------------------------------
# K8s provisioning helpers
# ---------------------------------------------------------------------------

def _ssm_run_poll(ssm_client, instance_id, script, timeout=600, label=""):
    resp = ssm_client.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [script]},
        TimeoutSeconds=timeout,
    )
    command_id = resp["Command"]["CommandId"]
    deadline = time.time() + timeout + 30
    while time.time() < deadline:
        time.sleep(8)
        try:
            out = ssm_client.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ssm_client.exceptions.InvocationDoesNotExist:
            continue
        status = out["Status"]
        if status in ("Success", "Failed", "Cancelled", "TimedOut"):
            stdout = out.get("StandardOutputContent", "")
            stderr = out.get("StandardErrorContent", "")
            if stderr:
                log(f"  [{label}] stderr: {stderr[:500]}")
            if status != "Success":
                raise RuntimeError(f"SSM command [{label}] failed ({status}):\n{stdout[-1000:]}")
            return stdout
        log(f"  [{label}] SSM status: {status} ...")
    raise RuntimeError(f"SSM command [{label}] timed out after {timeout}s")


def _provision_kind_ec2(ec2_client, ssm_client, iam_client):
    setup_hash = hashlib.md5(_SETUP_SCRIPT_CACHE_KEY).hexdigest()
    ami_ssm_key = f"/nexplane/smoke-amis/k8s-kind/{setup_hash[:8]}"

    cached_ami = None
    try:
        p = ssm_client.get_parameter(Name=ami_ssm_key)
        candidate = p["Parameter"]["Value"]
        imgs = ec2_client.describe_images(ImageIds=[candidate])["Images"]
        if imgs and imgs[0]["State"] == "available":
            cached_ami = candidate
            log(f"[CERT] Using cached k8s AMI: {cached_ami}")
    except Exception:
        pass

    platform_vpc_id = None
    try:
        desc = ec2_client.describe_instances(InstanceIds=["i-050bab85006f0b73c"])
        platform_vpc_id = desc["Reservations"][0]["Instances"][0].get("VpcId")
    except Exception:
        pass

    if platform_vpc_id:
        subnets = ec2_client.describe_subnets(
            Filters=[{"Name": "vpcId", "Values": [platform_vpc_id]}]
        )["Subnets"]
        vpc_id = platform_vpc_id
    else:
        vpc_id = ec2_client.describe_vpcs(
            Filters=[{"Name": "isDefault", "Values": ["true"]}]
        )["Vpcs"][0]["VpcId"]
        subnets = ec2_client.describe_subnets(
            Filters=[{"Name": "vpcId", "Values": [vpc_id]}]
        )["Subnets"]

    try:
        offs = ec2_client.describe_instance_type_offerings(
            LocationType="availability-zone",
            Filters=[{"Name": "instance-type", "Values": ["t3.medium"]}],
        )["InstanceTypeOfferings"]
        azs = {o["Location"] for o in offs}
        subnets = [s for s in subnets if s.get("AvailabilityZone") in azs] or subnets
    except Exception:
        pass
    subnets.sort(key=lambda s: s.get("AvailableIpAddressCount", 0), reverse=True)

    try:
        sgs = ec2_client.describe_security_groups(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]},
                     {"Name": "group-name", "Values": ["default"]}]
        )["SecurityGroups"]
        if sgs:
            sg_id = sgs[0]["GroupId"]
            for cidr in ("10.0.0.0/8", "172.16.0.0/12"):
                already = any(
                    p.get("FromPort") == KUBE_API_PORT and p.get("ToPort") == KUBE_API_PORT
                    and any(r.get("CidrIp") == cidr for r in p.get("IpRanges", []))
                    for p in sgs[0].get("IpPermissions", [])
                )
                if not already:
                    try:
                        ec2_client.authorize_security_group_ingress(
                            GroupId=sg_id,
                            IpPermissions=[{
                                "IpProtocol": "tcp",
                                "FromPort": KUBE_API_PORT,
                                "ToPort": KUBE_API_PORT,
                                "IpRanges": [{"CidrIp": cidr, "Description": f"k8s cert-smoke {cidr}"}],
                                "Ipv6Ranges": [],
                            }],
                        )
                        log(f"  Opened port {KUBE_API_PORT} in SG {sg_id} for {cidr}")
                    except Exception as _sg_exc:
                        log(f"  SG rule {cidr}: {_sg_exc}")
    except Exception as exc:
        log(f"  Warning: SG update failed: {exc}")

    instance_profile_name = None
    for name in ("NexplaneEC2TestProfile", "NexplaneSmokeProfile", "EC2InstanceProfileForSSM"):
        try:
            iam_client.get_instance_profile(InstanceProfileName=name)
            instance_profile_name = name
            break
        except Exception:
            pass

    launch_kwargs = dict(
        ImageId=cached_ami or AL2023_AMI,
        InstanceType="t3.medium",
        MinCount=1, MaxCount=1,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-k8s-cert"},
            {"Key": "nexplane-smoke", "Value": "true"},
        ]}],
        NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": subnets[0]["SubnetId"],
                            "AssociatePublicIpAddress": False}],
    )
    if instance_profile_name:
        launch_kwargs["IamInstanceProfile"] = {"Name": instance_profile_name}

    resp = ec2_client.run_instances(**launch_kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"[CERT] K8s EC2 launched: {instance_id}")

    private_ip = ""
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            desc = ec2_client.describe_instances(InstanceIds=[instance_id])
            inst = desc["Reservations"][0]["Instances"][0]
            if inst["State"]["Name"] == "running":
                private_ip = inst.get("PrivateIpAddress", "")
                log(f"  Running, private IP: {private_ip}")
                break
        except Exception:
            pass
        time.sleep(8)
    else:
        raise RuntimeError("K8s EC2 never reached running state")

    log("  Waiting for SSM agent...")
    deadline2 = time.time() + 180
    while time.time() < deadline2:
        try:
            info = ssm_client.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            if (info["InstanceInformationList"]
                    and info["InstanceInformationList"][0]["PingStatus"] == "Online"):
                break
        except Exception:
            pass
        time.sleep(10)
    else:
        raise RuntimeError("SSM agent never came online")

    if not cached_ami:
        log("  Running k8s setup (~5 min)...")
        setup_out = _ssm_run_poll(ssm_client, instance_id, SETUP_SCRIPT, timeout=1200, label="k8s-setup")
        if "K8S_RBAC_SETUP_COMPLETE" not in setup_out:
            raise RuntimeError(f"k8s setup failed:\n{setup_out[-500:]}")
        log("  kind cluster created")
    else:
        log("  Starting kind cluster from cached AMI...")
        restart_out = _ssm_run_poll(ssm_client, instance_id, _RESTART_SCRIPT, timeout=900, label="k8s-restart")
        if "RESTART_COMPLETE" not in restart_out:
            log(f"  Warning: restart may not have completed: {restart_out[-300:]}")

    log("  Fetching kubeconfig...")
    kubeconfig_content = _ssm_run_poll(
        ssm_client, instance_id,
        "kind get kubeconfig --name smoke-test 2>/dev/null "
        "|| cat /tmp/smoke-kubeconfig.yaml 2>/dev/null "
        "|| cat /root/.kube/config",
        timeout=30, label="get-kubeconfig",
    ).strip()
    if not kubeconfig_content:
        raise RuntimeError("Could not retrieve kubeconfig")

    _kube_server_pat = r"server: https://(?:127\.0\.0\.1|0\.0\.0\.0):(\d+)"
    if private_ip and re.search(_kube_server_pat, kubeconfig_content):
        kubeconfig_content = re.sub(
            _kube_server_pat,
            f"server: https://{private_ip}:{KUBE_API_PORT}",
            kubeconfig_content,
        )
        kubeconfig_content = re.sub(
            r"    certificate-authority-data: [^\n]+\n",
            "    insecure-skip-tls-verify: true\n",
            kubeconfig_content,
        )

    return instance_id, private_ip, kubeconfig_content


def _get_k8s_core_client(creds: dict):
    from kubernetes import client as k8s_client, config as k8s_config
    kubeconfig_raw = creds.get("kubeconfig")
    if kubeconfig_raw:
        if isinstance(kubeconfig_raw, str) and not kubeconfig_raw.strip().startswith("apiVersion"):
            try:
                kubeconfig_raw = base64.b64decode(kubeconfig_raw).decode()
            except Exception:
                pass
        kubeconfig_dict = yaml.safe_load(kubeconfig_raw) if isinstance(kubeconfig_raw, str) else kubeconfig_raw
        k8s_config.load_kube_config_from_dict(kubeconfig_dict)
    else:
        server = creds.get("server")
        token = creds.get("token")
        if not server or not token:
            return None
        configuration = k8s_client.Configuration()
        configuration.host = server
        configuration.api_key = {"authorization": f"Bearer {token}"}
        configuration.verify_ssl = False
        k8s_client.Configuration.set_default(configuration)
    return k8s_client.CoreV1Api()


def _create_or_replace_secret(core_v1, name: str, namespace: str, data: dict) -> None:
    from kubernetes import client as k8s_client
    body = k8s_client.V1Secret(
        metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
        type="kubernetes.io/tls",
        data=data,
    )
    try:
        core_v1.read_namespaced_secret(name, namespace)
        core_v1.replace_namespaced_secret(name, namespace, body)
        log(f"[CERT] Replaced K8s secret {name}")
    except Exception:
        core_v1.create_namespaced_secret(namespace, body)
        log(f"[CERT] Created K8s secret {name}")


def _delete_secret(core_v1, name: str, namespace: str) -> None:
    try:
        core_v1.delete_namespaced_secret(name, namespace)
        log(f"[CERT] Deleted K8s secret {name}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CR lifecycle helpers
# ---------------------------------------------------------------------------

def _client():
    return NexplaneClient(BASE_URL, EMAIL, PASSWORD)


def _step_ca_creds():
    creds = get_connector_creds_from_db("step_ca")
    if not creds:
        pytest.skip("No step_ca connector found")
    return creds


def _k8s_creds():
    return get_connector_creds_from_db("kubernetes")


def _run_cert_rotation_cr(client, title, subject, san=None, scan_scope=None, trigger_reason="scheduled",
                           verify_timeout_seconds=5, timeout=EXEC_TIMEOUT):
    base = client.base
    payload = {
        "title": title,
        "change_type": "certificate_rotation",
        "desired_outcome": {
            "subject": subject,
            "san": san or [subject],
            "not_after": "720h",
            "trigger_reason": trigger_reason,
            "scan_scope": scan_scope or ["nexplane_agent"],
            "verify_timeout_seconds": verify_timeout_seconds,
        },
    }
    r = client.client.post(f"{base}/change-requests", json=payload)
    assert r.status_code in (200, 201), f"CR create failed {r.status_code}: {r.text}"
    cr_id = r.json()["id"]

    for path in ["plan", "submit-for-approval"]:
        r2 = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        assert r2.status_code in (200, 201, 202, 204), f"/{path} failed {r2.status_code}: {r2.text}"

    r3 = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"},
    )
    assert r3.status_code in (200, 201, 202, 204), f"/approve failed {r3.status_code}: {r3.text}"

    r4 = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r4.status_code in (200, 201, 202, 204), f"/execute failed {r4.status_code}: {r4.text}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("completed", "paused"):
            return cr
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(f"CR {cr_id} unexpected status={status!r}: {cr}")
        time.sleep(5)
    raise TimeoutError(f"CR {cr_id} timed out after {timeout}s")


def _rollback_cr(client, cr_id, timeout=ROLLBACK_TIMEOUT):
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    assert r.status_code in (200, 201, 202, 204), f"/rollback failed {r.status_code}: {r.text}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("rolled_back", "rolled_back_with_warnings", "rollback_partial", "rollback_failed"):
            return cr
        time.sleep(5)
    raise TimeoutError(f"Rollback timed out for CR {cr_id}")


def _get_execution_result(cr):
    for run in cr.get("execution_runs", []):
        if "rollback" in (run.get("workflow_id") or ""):
            continue
        result = run.get("result") or {}
        if "dependents" in result:
            return result
        inner = result.get("execution") or {}
        if "dependents" in inner:
            return inner
    return {}


def _get_rollback_result(cr):
    for run in cr.get("execution_runs", []):
        if "rollback" not in (run.get("workflow_id") or ""):
            continue
        result = run.get("result") or {}
        if "rollback_steps" in result:
            return result
    for run in cr.get("execution_runs", []):
        result = run.get("result") or {}
        if "rollback_steps" in result:
            return result
    return {}


def _register_asset(client, name, asset_type="server", extra=None):
    base = client.base
    payload = {
        "name": name,
        "asset_type": asset_type,
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {"hostname": name},
        **(extra or {}),
    }
    r = client.client.post(f"{base}/assets", json=payload)
    assert r.status_code in (200, 201), f"Asset register failed {r.status_code}: {r.text}"
    return r.json()["id"]


def _delete_asset(client, asset_id):
    client.client.delete(f"{client.base}/assets/{asset_id}")


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestCertificateRotation:
    """Live certificate rotation smoke tests — 5 phases."""

    @classmethod
    def setup_class(cls):
        cls.client = _client()
        cls.step_ca_creds = _step_ca_creds()
        cls.smoke_asset_ids = []

        # K8s provisioning state
        cls._k8s_provisioned_instance_id = None
        cls._k8s_provisioned_connector_id = None
        cls._k8s_secret_asset_id = None
        cls._k8s_core_v1 = None
        cls._k8s_connector_id = None

        log("[CERT-ROTATION] setup: issuing initial cert for smoke subject")
        base = cls.client.base
        r = cls.client.client.post(f"{base}/change-requests", json={
            "title": "[SMOKE-SETUP] Initial cert for smoke subject",
            "change_type": "certificate_rotation",
            "desired_outcome": {
                "subject": SMOKE_SUBJECT,
                "san": [SMOKE_SUBJECT],
                "not_after": "720h",
                "trigger_reason": "scheduled",
                "scan_scope": ["kubernetes"],
                "verify_timeout_seconds": 1,
            },
        })
        if r.status_code in (200, 201):
            cr_id = r.json()["id"]
            for path in ["plan", "submit-for-approval"]:
                cls.client.client.post(f"{base}/change-requests/{cr_id}/{path}")
            cls.client.client.post(f"{base}/change-requests/{cr_id}/approve",
                                   json={"decision": "approved", "comment": "smoke-setup"})
            cls.client.client.post(f"{base}/change-requests/{cr_id}/execute")
            deadline = time.time() + 60
            while time.time() < deadline:
                cr = cls.client.client.get(f"{base}/change-requests/{cr_id}").json()
                if cr.get("status") in ("completed", "failed", "paused"):
                    break
                time.sleep(3)

        asset_id = _register_asset(cls.client, SMOKE_SUBJECT, extra={"asset_metadata": {"port": 443, "hostname": SMOKE_SUBJECT}})
        cls.smoke_asset_ids.append(asset_id)
        log(f"[CERT-ROTATION] Registered smoke host asset: {asset_id}")

        # K8s self-provisioning for Phase 2
        k8s_creds = _k8s_creds()
        if k8s_creds:
            log("[CERT-ROTATION] Using pre-existing K8s connector for Phase 2")
            cls._k8s_creds = k8s_creds
            cls._k8s_core_v1 = _get_k8s_core_client(k8s_creds)
        else:
            log("[CERT-ROTATION] No K8s connector — self-provisioning kind cluster for Phase 2")
            ec2_client = _get_aws_boto3_client("ec2")
            ssm_client = _get_aws_boto3_client("ssm")
            iam_client = _get_aws_boto3_client("iam")
            if not ec2_client or not ssm_client:
                log("[CERT-ROTATION] AWS unavailable — Phase 2 will skip")
                cls._k8s_creds = None
            else:
                try:
                    instance_id, _ip, kubeconfig_content = _provision_kind_ec2(
                        ec2_client, ssm_client, iam_client
                    )
                    cls._k8s_provisioned_instance_id = instance_id
                    kubeconfig_b64 = base64.b64encode(kubeconfig_content.encode()).decode()
                    conn_name = f"nexplane-smoke-k8s-cert-{instance_id}"
                    _k8s_conn = cls.client.post("/connectors", json={
                        "connector_type": "kubernetes",
                        "name": conn_name,
                        "display_name": conn_name,
                    })
                    _k8s_conn_id = _k8s_conn.get("id")
                    creds_resp = cls.client.client.put(
                        f"{cls.client.base}/connectors/{_k8s_conn_id}/credentials",
                        json={"credentials": {"kubeconfig": kubeconfig_b64}},
                    )
                    assert creds_resp.status_code in (200, 201, 204), \
                        f"PUT credentials failed {creds_resp.status_code}: {creds_resp.text}"
                    cls._k8s_provisioned_connector_id = _k8s_conn_id
                    cls._k8s_connector_id = _k8s_conn_id
                    cls._k8s_creds = {"kubeconfig": kubeconfig_b64}
                    cls._k8s_core_v1 = _get_k8s_core_client(cls._k8s_creds)
                    log(f"[CERT-ROTATION] K8s cluster ready, connector: {_k8s_conn_id}")
                except Exception as exc:
                    log(f"[CERT-ROTATION] K8s provisioning failed: {exc}")
                    cls._k8s_creds = None

    @classmethod
    def teardown_class(cls):
        log("[CERT-ROTATION] teardown: removing smoke assets")
        for asset_id in cls.smoke_asset_ids:
            try:
                _delete_asset(cls.client, asset_id)
            except Exception as e:
                log(f"[CERT-ROTATION] teardown warning: {e}")

        if cls._k8s_secret_asset_id:
            try:
                _delete_asset(cls.client, cls._k8s_secret_asset_id)
            except Exception:
                pass

        if cls._k8s_provisioned_connector_id:
            try:
                cls.client.delete(f"/connectors/{cls._k8s_provisioned_connector_id}")
                log(f"[CERT-ROTATION] Deregistered K8s connector {cls._k8s_provisioned_connector_id}")
            except Exception as exc:
                log(f"[CERT-ROTATION] teardown warning (connector): {exc}")

        if cls._k8s_provisioned_instance_id:
            try:
                ec2_client = _get_aws_boto3_client("ec2")
                if ec2_client:
                    ec2_client.terminate_instances(InstanceIds=[cls._k8s_provisioned_instance_id])
                    log(f"[CERT-ROTATION] Terminated K8s EC2 {cls._k8s_provisioned_instance_id}")
            except Exception as exc:
                log(f"[CERT-ROTATION] teardown warning (EC2): {exc}")

    def test_phase1_happy_path_cert_issuance(self):
        """Happy path: cert issuance with no dependents, CR completes, rollback re-issues."""
        log("[PHASE1] Starting cert issuance smoke")

        cr = _run_cert_rotation_cr(
            self.client,
            "[SMOKE] Cert rotation phase 1 — happy path",
            SMOKE_SUBJECT,
            scan_scope=["kubernetes"],
            verify_timeout_seconds=2,
        )
        assert cr["status"] == "completed", f"Expected completed, got {cr['status']}: {cr}"
        log("[PHASE1] CR completed")

        result = _get_execution_result(cr)
        rotation_result = result.get("rotation_result", {})
        assert rotation_result.get("fingerprint"), "rotation_result.fingerprint must be set"
        assert rotation_result.get("cert_pem"), "rotation_result.cert_pem must be present"
        log(f"[PHASE1] New cert fingerprint: {rotation_result['fingerprint'][:16]}...")

        log("[PHASE1] Triggering rollback")
        rb_cr = _rollback_cr(self.client, cr["id"])
        assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings"), \
            f"Unexpected rollback status: {rb_cr['status']}"
        log("[PHASE1] PASS")

    def test_phase2_type_b_k8s_secret(self):
        """Type-B K8s secret rotation and rollback — self-provisions kind if no connector exists."""
        if not self.__class__._k8s_core_v1:
            pytest.skip("K8s cluster unavailable (provisioning failed or AWS unavailable)")

        core_v1 = self.__class__._k8s_core_v1

        # Determine K8s connector ID (used to link the asset)
        k8s_connector_id = self.__class__._k8s_connector_id
        if not k8s_connector_id:
            conns_r = self.client.client.get(
                f"{self.client.base}/connectors",
                params={"connector_type": "kubernetes"},
            )
            conns_raw = conns_r.json()
            conns = conns_raw if isinstance(conns_raw, list) else conns_raw.get("items", [])
            k8s_connector_id = conns[0]["id"] if conns else None

        log("[PHASE2] Creating K8s TLS secret with placeholder cert")
        placeholder = base64.b64encode(b"PLACEHOLDER-CERT").decode()
        secret_name = "nexplane-smoke-tls"
        _create_or_replace_secret(
            core_v1, secret_name, "default",
            {"tls.crt": placeholder, "tls.key": placeholder},
        )

        # Register a k8s_secret asset so _scan_dependents finds it
        asset_payload = {
            "asset_metadata": {
                "namespace": "default",
                "labels": {"cert-subject": SMOKE_SUBJECT},
            },
        }
        if k8s_connector_id:
            asset_payload["connector_id"] = k8s_connector_id

        base = self.client.base
        r = self.client.client.post(f"{base}/assets", json={
            "name": secret_name,
            "asset_type": "k8s_secret",
            "environment": "prod",
            "criticality": "medium",
            **asset_payload,
        })
        assert r.status_code in (200, 201), f"k8s_secret asset create failed {r.status_code}: {r.text}"
        k8s_secret_asset_id = r.json()["id"]
        self.__class__._k8s_secret_asset_id = k8s_secret_asset_id
        log(f"[PHASE2] k8s_secret asset registered: {k8s_secret_asset_id}")

        try:
            log("[PHASE2] Running cert rotation with scan_scope=[kubernetes]")
            cr = _run_cert_rotation_cr(
                self.client,
                "[SMOKE] Cert rotation phase 2 — K8s secret",
                SMOKE_SUBJECT,
                scan_scope=["kubernetes"],
                verify_timeout_seconds=3,
            )
            assert cr["status"] == "completed", f"Expected completed: {cr['status']}: {cr}"
            result = _get_execution_result(cr)
            rotation_result = result.get("rotation_result", {})
            new_cert_pem = rotation_result.get("cert_pem", "")

            k8s_dependents = [d for d in result.get("dependents", []) if d["type"] == "k8s_secret"]
            assert k8s_dependents, "Expected at least one K8s secret dependent"
            verified = all((d.get("verify_result") or {}).get("success") for d in k8s_dependents)
            assert verified, f"K8s secret verify failed: {k8s_dependents}"

            # Directly verify the K8s secret data was updated
            import base64 as _b64
            secret = core_v1.read_namespaced_secret(secret_name, "default")
            raw_b64 = (secret.data or {}).get("tls.crt", "")
            stored_pem = _b64.b64decode(raw_b64).decode() if raw_b64 else ""
            assert new_cert_pem.strip() in stored_pem.strip(), \
                "K8s secret tls.crt was not updated with new cert PEM"
            log("[PHASE2] K8s secret verified — triggering rollback")

            rb_cr = _rollback_cr(self.client, cr["id"])
            assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings"), \
                f"Unexpected rollback status: {rb_cr['status']}"
            log("[PHASE2] PASS")

        finally:
            _delete_secret(core_v1, secret_name, "default")
            if self.__class__._k8s_secret_asset_id:
                try:
                    _delete_asset(self.client, self.__class__._k8s_secret_asset_id)
                except Exception:
                    pass
                self.__class__._k8s_secret_asset_id = None

    def test_phase3_compromise_trigger(self):
        """Compromise trigger: rollback_strategy must be 'reissue'."""
        log("[PHASE3] Starting compromise trigger rotation")

        cr = _run_cert_rotation_cr(
            self.client,
            "[SMOKE] Cert rotation phase 3 — compromise",
            SMOKE_SUBJECT,
            scan_scope=["kubernetes"],
            trigger_reason="compromise",
            verify_timeout_seconds=2,
        )
        assert cr["status"] == "completed", f"Expected completed: {cr['status']}: {cr}"
        log("[PHASE3] CR completed — triggering rollback")

        rb_cr = _rollback_cr(self.client, cr["id"])
        assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings")

        rb_result = _get_rollback_result(rb_cr)
        rollback_strategy = rb_result.get("rollback_strategy")
        assert rollback_strategy == "reissue", \
            f"Expected rollback_strategy='reissue' for compromise, got '{rollback_strategy}'"
        log("[PHASE3] rollback_strategy=reissue confirmed")
        log("[PHASE3] PASS")

    def test_phase4_verify_failure_paused_rollback(self):
        """Verify failure: CR pauses; rollback FILO order confirmed."""
        log("[PHASE4] Registering synthetic unreachable host")
        fake_asset_id = _register_asset(
            self.client, SMOKE_SUBJECT,
            extra={"asset_metadata": {"port": 9999, "hostname": SMOKE_SUBJECT}},
        )
        self.smoke_asset_ids.append(fake_asset_id)

        try:
            log("[PHASE4] Running rotation with scan_scope=nexplane_agent (finds synthetic host)")
            cr = _run_cert_rotation_cr(
                self.client,
                "[SMOKE] Cert rotation phase 4 — verify failure",
                SMOKE_SUBJECT,
                scan_scope=["nexplane_agent"],
                verify_timeout_seconds=2,
            )
            assert cr["status"] == "paused", \
                f"Expected paused (synthetic host should fail verify), got {cr['status']}: {cr}"
            log("[PHASE4] CR paused as expected")

            result = _get_execution_result(cr)
            assert result.get("phase") == "verify", f"Expected phase=verify, got {result.get('phase')}"

            dependents = result.get("dependents", [])
            failed = [d for d in dependents if not (d.get("verify_result") or {}).get("success")]
            assert failed, f"Expected at least one failed dependent: {dependents}"
            log(f"[PHASE4] {len(failed)} failed, {len(dependents) - len(failed)} succeeded")

            log("[PHASE4] Triggering rollback")
            rb_cr = _rollback_cr(self.client, cr["id"])
            assert rb_cr["status"] in ("rolled_back", "rolled_back_with_warnings")

            rb_result = _get_rollback_result(rb_cr)
            rb_steps = rb_result.get("rollback_steps", [])
            assert rb_steps, "rollback_steps must be non-empty"

            rb_indices = [s["index"] for s in rb_steps]
            assert rb_indices == sorted(rb_indices, reverse=True), \
                f"Expected FILO (descending indices) in rollback_steps, got {rb_indices}"
            log(f"[PHASE4] FILO confirmed: {rb_indices}")
            log("[PHASE4] PASS")
        finally:
            _delete_asset(self.client, fake_asset_id)
            if fake_asset_id in self.smoke_asset_ids:
                self.smoke_asset_ids.remove(fake_asset_id)

    def test_phase5_auto_trigger_expiry_worker(self):
        """Auto-trigger: expiry worker creates draft CR for near-expiry cert via CertificateInventory."""
        log("[PHASE5] Issuing near-expiry cert (not_after=2h)")

        base = self.client.base
        r = self.client.client.post(f"{base}/change-requests", json={
            "title": "[SMOKE-SETUP] Near-expiry cert for auto-trigger",
            "change_type": "certificate_rotation",
            "desired_outcome": {
                "subject": EXPIRING_SUBJECT,
                "san": [EXPIRING_SUBJECT],
                "not_after": "2h",
                "trigger_reason": "scheduled",
                "scan_scope": ["kubernetes"],
                "verify_timeout_seconds": 1,
            },
        })
        assert r.status_code in (200, 201), f"Setup CR failed: {r.text}"
        setup_cr_id = r.json()["id"]
        for path in ["plan", "submit-for-approval"]:
            self.client.client.post(f"{base}/change-requests/{setup_cr_id}/{path}")
        self.client.client.post(f"{base}/change-requests/{setup_cr_id}/approve",
                                json={"decision": "approved", "comment": "smoke-setup"})
        self.client.client.post(f"{base}/change-requests/{setup_cr_id}/execute")
        deadline = time.time() + 60
        while time.time() < deadline:
            cr = self.client.client.get(f"{base}/change-requests/{setup_cr_id}").json()
            if cr.get("status") in ("completed", "failed", "paused"):
                break
            time.sleep(3)
        assert cr.get("status") == "completed", \
            f"Near-expiry cert CR did not complete: {cr.get('status')}"
        log(f"[PHASE5] Near-expiry cert issued")

        # Verify CertificateInventory entry was written by the executor
        log("[PHASE5] Verifying CertificateInventory entry was written at issuance")
        import asyncio as _asyncio
        import concurrent.futures as _cf
        from app.database import AsyncSessionLocal as _AsyncSessionLocal
        from sqlalchemy import select as _select

        async def _check_inventory():
            from app.models.certificate_inventory import CertificateInventory
            async with _AsyncSessionLocal() as _db:
                res = await _db.execute(
                    _select(CertificateInventory).where(
                        CertificateInventory.subject == EXPIRING_SUBJECT
                    )
                )
                return res.scalars().all()

        with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
            inv_entries = _pool.submit(_asyncio.run, _check_inventory()).result(timeout=30)
        assert inv_entries, (
            f"No CertificateInventory entry for {EXPIRING_SUBJECT} — "
            "executor must write inventory at issuance"
        )
        log(f"[PHASE5] CertificateInventory entry: fingerprint={inv_entries[0].fingerprint[:16]}...")

        # Run the expiry worker — the 2h cert expires within the 30-day threshold
        log("[PHASE5] Running expiry worker (_check_step_ca_certs)")
        from app.workers.credential_expiry_worker import _check_step_ca_certs

        async def _run_worker():
            async with _AsyncSessionLocal() as _db:
                await _check_step_ca_certs(_db)

        with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
            _pool.submit(_asyncio.run, _run_worker()).result(timeout=60)
        log("[PHASE5] Worker ran")

        log("[PHASE5] Checking for auto-created draft CR")
        deadline = time.time() + 30
        auto_cr = None
        while time.time() < deadline:
            r = self.client.client.get(f"{base}/change-requests", params={
                "change_type": "certificate_rotation",
                "status": "awaiting_approval",
            })
            crs = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
            matches = [
                c for c in crs
                if c.get("title", "") == f"[Auto] Certificate rotation: {EXPIRING_SUBJECT}"
            ]
            if matches:
                auto_cr = matches[0]
                break
            time.sleep(3)

        assert auto_cr, f"No auto-triggered certificate_rotation CR found for {EXPIRING_SUBJECT}"
        log(f"[PHASE5] Auto-triggered CR found: {auto_cr['id']}")

        auto_cr_id = auto_cr["id"]
        r = self.client.client.post(f"{base}/change-requests/{auto_cr_id}/approve",
                                    json={"decision": "approved", "comment": "smoke"})
        assert r.status_code in (200, 201, 202, 204)
        r = self.client.client.post(f"{base}/change-requests/{auto_cr_id}/execute")
        assert r.status_code in (200, 201, 202, 204)

        deadline = time.time() + EXEC_TIMEOUT
        while time.time() < deadline:
            final_cr = self.client.client.get(f"{base}/change-requests/{auto_cr_id}").json()
            if final_cr.get("status") in ("completed", "paused", "failed"):
                break
            time.sleep(5)

        assert final_cr.get("status") == "completed", \
            f"Auto-trigger CR did not complete: {final_cr.get('status')}"
        result = _get_execution_result(final_cr)
        assert result.get("rotation_result", {}).get("fingerprint"), "New cert fingerprint must be set"
        log("[PHASE5] PASS")
