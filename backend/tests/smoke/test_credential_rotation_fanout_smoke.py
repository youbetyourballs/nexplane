# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Credential Rotation Fanout Smoke Test

Phases:
  1. EXECUTE — pre-seed a K8s ConfigMap with a known value, create a
     credential_rotation_fanout CR to scan for it and replace it, assert
     the new value is present after execution.
  2. ROLLBACK — trigger rollback on the same CR, assert the ConfigMap is
     restored to the original value.

Run from EC2:
  docker exec nexplane-backend-1 python -m pytest \\
    /app/tests/smoke/test_credential_rotation_fanout_smoke.py -v -s

Self-provisioning: if no Kubernetes connector is registered in the platform
database, setup_class will launch a t3.medium EC2 instance in the same VPC
as the platform, install kind, create a cluster, and register a connector
automatically.  The instance is terminated (or left running for AMI caching)
at teardown.
"""

import base64
import hashlib
import os
import re
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

EXECUTE_TIMEOUT = 120
ROLLBACK_TIMEOUT = 120
SMOKE_NAMESPACE = "default"
SMOKE_CM_KEY = "secret"
OLD_VALUE = "old-secret-value"
NEW_VALUE = "new-secret-value"

KUBE_API_PORT = 6443
AL2023_AMI = "ami-0953476d60561c955"
S3_TOOLS_BUCKET = "nexplane-agent-downloads"
KUBECTL_VERSION = "v1.29.0"
KIND_VERSION = "v0.24.0"
KIND_NODE_IMAGE = "kindest/node:v1.30.0"
KIND_NODE_S3KEY = "smoke-tools/kindest-node-v1.30.0.tar.gz"

# The setup script is hashed to produce the AMI cache key.  Bump the sentinel
# string (not the script content) when you want to invalidate the cache.
_SETUP_SCRIPT_CACHE_KEY = b"fanout-kind-0.24.0-v1"

SETUP_SCRIPT = f"""
set -e
PRIVATE_IP=$(curl -s http://169.254.169.254/latest/meta-data/local-ipv4)
echo "Private IP: $PRIVATE_IP"

# Install docker
dnf install -y docker 2>/dev/null || apt-get install -y docker.io 2>/dev/null || true
systemctl enable docker && systemctl start docker
for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break || sleep 2; done

# Download kubectl and kind from S3 VPC endpoint (no internet egress needed)
aws s3 cp s3://{S3_TOOLS_BUCKET}/smoke-tools/kubectl-{KUBECTL_VERSION} /usr/local/bin/kubectl
chmod +x /usr/local/bin/kubectl
aws s3 cp s3://{S3_TOOLS_BUCKET}/smoke-tools/kind-{KIND_VERSION} /usr/local/bin/kind
chmod +x /usr/local/bin/kind

# Pre-load kindest/node image from S3 (runner has no internet; image staged by platform)
aws s3 cp s3://{S3_TOOLS_BUCKET}/smoke-tools/kindest-node-v1.30.0.tar.gz - | docker load

# Enable IP forwarding — required for Docker DNAT to reach containers from other VPC hosts.
sysctl -w net.ipv4.ip_forward=1

# Create kind cluster with API server bound on all interfaces so Docker creates
# a 0.0.0.0:6443 port mapping accessible from the VPC.
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

# Verify Docker port binding
echo "Docker port bindings:"
docker port smoke-test-control-plane 6443/tcp || true
echo "ip_forward: $(cat /proc/sys/net/ipv4/ip_forward)"

# Allow inbound on port 6443 from VPC
iptables -I INPUT -p tcp --dport 6443 -j ACCEPT 2>/dev/null || true

kind get kubeconfig --name smoke-test > /tmp/smoke-kubeconfig.yaml 2>/dev/null
mkdir -p /root/.kube && cp /tmp/smoke-kubeconfig.yaml /root/.kube/config
export KUBECONFIG=/tmp/smoke-kubeconfig.yaml
echo "kubeconfig ready"

echo "K8S_RBAC_SETUP_COMPLETE"
"""

_RESTART_SCRIPT = f"""
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
  || {{ echo "KIND_FAILED"; tail -20 /tmp/kind-out.txt; exit 1; }}
echo "Docker port bindings:"; docker port smoke-test-control-plane 6443/tcp || true
echo "Port 6443 listen:"; ss -tlnp 'sport = :6443' 2>/dev/null || ss -tlnp | grep ':6443' || echo 'not listening'
iptables -I FORWARD -i eth0 -p tcp --dport 6443 -j ACCEPT 2>/dev/null || true
iptables -I FORWARD -o eth0 -p tcp --sport 6443 -j ACCEPT 2>/dev/null || true
iptables -I INPUT -p tcp --dport 6443 -j ACCEPT 2>/dev/null || true
echo "iptables nat DOCKER:"; iptables -t nat -L DOCKER -n 2>/dev/null | grep 6443 || echo 'no DNAT rule'
kind get kubeconfig --name smoke-test > /tmp/smoke-kubeconfig.yaml 2>/dev/null
mkdir -p /root/.kube && cp /tmp/smoke-kubeconfig.yaml /root/.kube/config
export KUBECONFIG=/tmp/smoke-kubeconfig.yaml
echo "RESTART_COMPLETE"
"""

pytestmark = pytest.mark.SMOKE


# ---------------------------------------------------------------------------
# Helpers — K8s direct SDK (verification only, not used for CR execution)
# ---------------------------------------------------------------------------

def _get_k8s_core_client(creds: dict):
    from kubernetes import client as k8s_client, config as k8s_config
    kubeconfig_raw = creds.get("kubeconfig")
    if kubeconfig_raw:
        # kubeconfig may be base64-encoded (as stored in platform DB) or raw YAML
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
            pytest.skip("No kubeconfig or server/token in Kubernetes connector creds")
        configuration = k8s_client.Configuration()
        configuration.host = server
        configuration.api_key = {"authorization": f"Bearer {token}"}
        configuration.verify_ssl = False
        k8s_client.Configuration.set_default(configuration)
    return k8s_client.CoreV1Api()


def _create_or_update_configmap(core_v1, name: str, namespace: str, key: str, value: str) -> None:
    from kubernetes import client as k8s_client
    body = k8s_client.V1ConfigMap(
        metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
        data={key: value},
    )
    try:
        core_v1.read_namespaced_config_map(name, namespace)
        core_v1.replace_namespaced_config_map(name, namespace, body)
        log(f"[FANOUT] Updated ConfigMap {name}/{key}={value!r}")
    except Exception:
        core_v1.create_namespaced_config_map(namespace, body)
        log(f"[FANOUT] Created ConfigMap {name}/{key}={value!r}")


def _read_configmap_value(core_v1, name: str, namespace: str, key: str) -> str:
    cm = core_v1.read_namespaced_config_map(name, namespace)
    return (cm.data or {}).get(key, "")


def _delete_configmap(core_v1, name: str, namespace: str) -> None:
    try:
        core_v1.delete_namespaced_config_map(name, namespace)
        log(f"[FANOUT] Deleted ConfigMap {name}")
    except Exception as exc:
        log(f"[FANOUT] Cleanup skipped: {exc}")


# ---------------------------------------------------------------------------
# Helpers — CR lifecycle via Nexplane API (dogfooding)
# ---------------------------------------------------------------------------

def _run_fanout_cr(client: NexplaneClient, title: str, search_terms: list, scan_scope: list,
                   new_value: str, timeout: int) -> dict:
    """Create → plan → approve → execute a credential_rotation_fanout CR; poll until done."""
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": title,
        "change_type": "credential_rotation_fanout",
        "desired_outcome": {
            "search_terms": search_terms,
            "scan_scope": scan_scope,
            "new_value": new_value,
            "_smoke_test": True,
        },
    })
    assert resp.status_code in (200, 201), f"CR create failed {resp.status_code}: {resp.text}"
    cr_id = resp.json()["id"]
    log(f"[FANOUT] CR created: {cr_id}")

    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        assert r.status_code in (200, 201, 202, 204), f"/{path} failed {r.status_code}: {r.text}"

    r = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"},
    )
    assert r.status_code in (200, 201, 202, 204), f"/approve failed {r.status_code}: {r.text}"

    r = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    assert r.status_code in (200, 201, 202, 204), f"/execute failed {r.status_code}: {r.text}"

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in ("completed", "paused"):
            return cr
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(f"CR {cr_id} ended with status={status!r}: {cr}")
        time.sleep(5)
    raise TimeoutError(f"CR {cr_id} timed out after {timeout}s")


def _rollback_cr(client: NexplaneClient, cr_id: str, timeout: int) -> dict:
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


# ---------------------------------------------------------------------------
# Self-provisioning helpers
# ---------------------------------------------------------------------------

def _ssm_run_poll(ssm_client, instance_id, script, timeout=600, label=""):
    """Send SSM RunShellScript command and poll until complete. Returns stdout."""
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
                raise RuntimeError(
                    f"SSM command [{label}] failed ({status}):\n{stdout[-1000:]}\n{stderr[-500:]}"
                )
            return stdout
        log(f"  [{label}] SSM status: {status} ...")
    raise RuntimeError(f"SSM command [{label}] timed out after {timeout}s")


def _stage_tools_to_s3(s3_client):
    """Upload kubectl, kind, and kindest/node image to S3 so the EC2 runner can fetch them
    without internet egress (VPC gateway endpoint)."""
    import urllib.request as _ur
    import subprocess as _sp
    import gzip as _gz
    import io as _io

    for _tool, _url, _s3key in [
        ("kubectl",
         f"https://storage.googleapis.com/kubernetes-release/release/{KUBECTL_VERSION}/bin/linux/amd64/kubectl",
         f"smoke-tools/kubectl-{KUBECTL_VERSION}"),
        ("kind",
         f"https://github.com/kubernetes-sigs/kind/releases/download/{KIND_VERSION}/kind-linux-amd64",
         f"smoke-tools/kind-{KIND_VERSION}"),
    ]:
        try:
            s3_client.head_object(Bucket=S3_TOOLS_BUCKET, Key=_s3key)
            log(f"  {_tool} already in S3")
        except Exception:
            log(f"  Downloading {_tool} -> S3...")
            _data = _ur.urlopen(_url, timeout=120).read()
            s3_client.put_object(Bucket=S3_TOOLS_BUCKET, Key=_s3key, Body=_data)
            log(f"  {_tool} staged to s3://{S3_TOOLS_BUCKET}/{_s3key}")

    try:
        s3_client.head_object(Bucket=S3_TOOLS_BUCKET, Key=KIND_NODE_S3KEY)
        log("  kindest/node image already in S3")
    except Exception:
        log("  kindest/node not in S3 — pulling via platform host docker and staging...")
        _pull = _sp.run(
            ["docker", "pull", KIND_NODE_IMAGE],
            capture_output=True, text=True, timeout=300,
        )
        if _pull.returncode != 0:
            log(f"  WARNING: docker pull failed (no docker in container?): {_pull.stderr[:200]}", ok=False)
        else:
            _save = _sp.run(["docker", "save", KIND_NODE_IMAGE], capture_output=True, timeout=300)
            _buf = _io.BytesIO()
            with _gz.GzipFile(fileobj=_buf, mode="wb") as _gz_f:
                _gz_f.write(_save.stdout)
            s3_client.put_object(Bucket=S3_TOOLS_BUCKET, Key=KIND_NODE_S3KEY, Body=_buf.getvalue())
            log(f"  kindest/node staged to s3://{S3_TOOLS_BUCKET}/{KIND_NODE_S3KEY}")


def _provision_kind_ec2(ec2_client, ssm_client, iam_client, s3_client):
    """Launch (or reuse an AMI-cached) EC2 instance with a running kind cluster.

    Returns (instance_id, private_ip, kubeconfig_content, created_by_us).
    created_by_us is True when this call launched the instance (so teardown
    knows whether to terminate it).
    """
    setup_hash = hashlib.md5(_SETUP_SCRIPT_CACHE_KEY).hexdigest()
    ami_ssm_key = f"/nexplane/smoke-amis/k8s-kind/{setup_hash[:8]}"

    # Stage tools to S3 so the runner can install without internet egress
    if s3_client:
        try:
            _stage_tools_to_s3(s3_client)
        except Exception as exc:
            log(f"  WARNING: S3 tool staging failed: {exc}", ok=False)

    # Check for cached AMI
    cached_ami = None
    try:
        p = ssm_client.get_parameter(Name=ami_ssm_key)
        candidate = p["Parameter"]["Value"]
        imgs = ec2_client.describe_images(ImageIds=[candidate])["Images"]
        if imgs and imgs[0]["State"] == "available":
            cached_ami = candidate
            log(f"[FANOUT] Using cached k8s AMI: {cached_ami}")
    except Exception:
        pass

    # Resolve VPC + subnet (same VPC as the platform instance i-050bab85006f0b73c)
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
        # Fall back to default VPC
        vpc_id = ec2_client.describe_vpcs(
            Filters=[{"Name": "isDefault", "Values": ["true"]}]
        )["Vpcs"][0]["VpcId"]
        subnets = ec2_client.describe_subnets(
            Filters=[{"Name": "vpcId", "Values": [vpc_id]}]
        )["Subnets"]

    # Prefer AZs that offer t3.medium
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

    # Ensure SG allows inbound 6443 within VPC
    try:
        sgs = ec2_client.describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "group-name", "Values": ["default"]},
            ]
        )["SecurityGroups"]
        if sgs:
            sg_id = sgs[0]["GroupId"]
            port_open = any(
                p.get("FromPort") == KUBE_API_PORT and p.get("ToPort") == KUBE_API_PORT
                for p in sgs[0].get("IpPermissions", [])
            )
            if not port_open:
                ec2_client.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[{
                        "IpProtocol": "tcp",
                        "FromPort": KUBE_API_PORT,
                        "ToPort": KUBE_API_PORT,
                        "IpRanges": [{"CidrIp": "10.0.0.0/8", "Description": "k8s fanout smoke VPC"}],
                        "Ipv6Ranges": [],
                    }],
                )
                log(f"[FANOUT] Opened port {KUBE_API_PORT} in default SG {sg_id}")
    except Exception as exc:
        log(f"  Warning: could not open port in SG: {exc}")

    # Resolve IAM instance profile (needed for SSM)
    instance_profile_name = None
    for name in ("NexplaneEC2TestProfile", "NexplaneSmokeProfile", "EC2InstanceProfileForSSM"):
        try:
            iam_client.get_instance_profile(InstanceProfileName=name)
            instance_profile_name = name
            break
        except Exception:
            pass

    # Look up nexplane-smoke-k8s SG if it exists
    _k8s_sg_id = None
    try:
        _sgs = ec2_client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": ["nexplane-smoke-k8s"]}]
        )["SecurityGroups"]
        _k8s_sg_id = _sgs[0]["GroupId"] if _sgs else None
    except Exception:
        pass

    launch_kwargs = dict(
        ImageId=cached_ami or AL2023_AMI,
        InstanceType="t3.medium",  # kind needs ≥4 GB RAM
        MinCount=1, MaxCount=1,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Name", "Value": "nexplane-smoke-k8s-fanout"},
            {"Key": "nexplane-smoke", "Value": "true"},
        ]}],
        NetworkInterfaces=[{
            "DeviceIndex": 0,
            "SubnetId": subnets[0]["SubnetId"],
            "AssociatePublicIpAddress": False,
            **( {"Groups": [_k8s_sg_id]} if _k8s_sg_id else {} ),
        }],
    )
    if instance_profile_name:
        launch_kwargs["IamInstanceProfile"] = {"Name": instance_profile_name}
    else:
        log("  Warning: no IAM instance profile found — SSM may not work")

    resp = ec2_client.run_instances(**launch_kwargs)
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"[FANOUT] K8s EC2 launched: {instance_id}")

    # Wait for running
    private_ip = ""
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            desc = ec2_client.describe_instances(InstanceIds=[instance_id])
            inst = desc["Reservations"][0]["Instances"][0]
            if inst["State"]["Name"] == "running":
                private_ip = inst.get("PrivateIpAddress", "")
                log(f"  Instance running, private IP: {private_ip}")
                break
        except Exception:
            pass
        time.sleep(8)
    else:
        raise RuntimeError("K8s EC2 never reached running state")

    # Wait for SSM
    log("  Waiting for SSM agent...")
    deadline2 = time.time() + 180
    ssm_ready = False
    while time.time() < deadline2:
        try:
            info = ssm_client.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            if (info["InstanceInformationList"]
                    and info["InstanceInformationList"][0]["PingStatus"] == "Online"):
                ssm_ready = True
                break
        except Exception:
            pass
        time.sleep(10)
    if not ssm_ready:
        raise RuntimeError("SSM agent never came online on K8s EC2")
    log("  SSM ready")

    if not cached_ami:
        log("  Running k8s setup (docker + kind + cluster create, ~5 min)...")
        setup_out = _ssm_run_poll(ssm_client, instance_id, SETUP_SCRIPT, timeout=1200, label="k8s-setup")
        if "K8S_RBAC_SETUP_COMPLETE" in setup_out:
            log("  kind cluster created")
            # Cache as AMI for future runs
            try:
                from run_on_ec2 import get_or_create_smoke_ami
                get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "k8s-kind", setup_hash)
            except ImportError:
                pass
        else:
            raise RuntimeError(f"k8s setup did not complete:\n{setup_out[-500:]}")
    else:
        log("  Starting kind cluster from cached AMI...")
        restart_out = _ssm_run_poll(
            ssm_client, instance_id, _RESTART_SCRIPT, timeout=900, label="k8s-restart"
        )
        for _line in restart_out.splitlines():
            if any(k in _line for k in (
                "Port 6443", "port bindings", "DNAT", "nat DOCKER", "listen",
                "KIND_CLUSTER_READY", "KIND_FAILED", "RESTART_COMPLETE",
            )):
                log(f"  [k8s-diag] {_line.strip()}")
        if "RESTART_COMPLETE" not in restart_out:
            log(f"  Warning: restart may not have completed: {restart_out[-300:]}", ok=False)

    # Fetch kubeconfig
    log("  Fetching kubeconfig...")
    kubeconfig_content = _ssm_run_poll(
        ssm_client, instance_id,
        "kind get kubeconfig --name smoke-test 2>/dev/null "
        "|| cat /tmp/smoke-kubeconfig.yaml 2>/dev/null "
        "|| cat /root/.kube/config",
        timeout=30, label="get-kubeconfig",
    ).strip()

    if not kubeconfig_content:
        raise RuntimeError("Could not retrieve kubeconfig from kind cluster")
    log(f"  kubeconfig fetched ({len(kubeconfig_content)} bytes)")

    # Rewrite server URL and skip TLS verification
    _kube_server_pat = r"server: https://(?:127\.0\.0\.1|0\.0\.0\.0):(\d+)"
    if private_ip and re.search(_kube_server_pat, kubeconfig_content):
        log(f"  Rewriting kubeconfig server -> {private_ip}:{KUBE_API_PORT}")
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


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestCredentialRotationFanoutSmoke:
    """
    End-to-end smoke for credential_rotation_fanout CR type.

    Phase 1: seeds a ConfigMap with OLD_VALUE, runs the fanout CR to replace
    it with NEW_VALUE, and verifies the ConfigMap was updated.

    Phase 2: triggers rollback on the same CR, verifies the ConfigMap is
    restored to OLD_VALUE, then deletes the test ConfigMap.
    """

    @classmethod
    def setup_class(cls):
        cls._provisioned_instance_id = None   # set if we launched the EC2
        cls._provisioned_connector_id = None  # set if we registered the connector
        cls._provisioned_asset_id = None      # set if we registered the asset

        # ------------------------------------------------------------------ #
        # Step 1: check for existing Kubernetes connector                     #
        # ------------------------------------------------------------------ #
        creds = get_connector_creds_from_db("kubernetes")
        if creds:
            log("[FANOUT] Using pre-existing Kubernetes connector from platform DB")
            cls.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
            cls.creds = creds
            cls.core_v1 = _get_k8s_core_client(creds)
            cls.suffix = uuid.uuid4().hex[:8]
            cls.cm_name = f"nexplane-smoke-fanout-{cls.suffix}"
            cls.cr_id = None
            log(f"[FANOUT] Pre-seeding ConfigMap {cls.cm_name} with old value")
            _create_or_update_configmap(cls.core_v1, cls.cm_name, SMOKE_NAMESPACE, SMOKE_CM_KEY, OLD_VALUE)
            return

        # ------------------------------------------------------------------ #
        # Step 2: no existing connector — self-provision via AWS              #
        # ------------------------------------------------------------------ #
        log("[FANOUT] No Kubernetes connector in DB — self-provisioning kind cluster")

        ec2_client = _get_aws_boto3_client("ec2")
        ssm_client = _get_aws_boto3_client("ssm")
        iam_client = _get_aws_boto3_client("iam")
        s3_client = _get_aws_boto3_client("s3")

        if not ec2_client or not ssm_client:
            pytest.skip(
                "No Kubernetes connector registered and AWS credentials unavailable — "
                "cannot self-provision kind cluster"
            )

        # ------------------------------------------------------------------ #
        # Steps 3-5: launch EC2, install kind, fetch + rewrite kubeconfig    #
        # ------------------------------------------------------------------ #
        instance_id, _private_ip, kubeconfig_content = _provision_kind_ec2(
            ec2_client, ssm_client, iam_client, s3_client
        )
        cls._provisioned_instance_id = instance_id

        # ------------------------------------------------------------------ #
        # Step 6: register connector + asset via Nexplane API                #
        # ------------------------------------------------------------------ #
        client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        cls.client = client

        kubeconfig_b64 = base64.b64encode(kubeconfig_content.encode()).decode()
        conn_name = f"nexplane-smoke-k8s-fanout-{instance_id}"
        _k8s_conn = client.post("/connectors", json={
            "connector_type": "kubernetes",
            "name": conn_name,
            "display_name": conn_name,
        })
        _k8s_conn_id = _k8s_conn.get("id")
        # POST /connectors silently drops credentials — must PUT separately
        creds_resp = client.client.put(
            f"{client.base}/connectors/{_k8s_conn_id}/credentials",
            json={"credentials": {"kubeconfig": kubeconfig_b64}},
        )
        assert creds_resp.status_code in (200, 201, 204), (
            f"PUT /connectors/{_k8s_conn_id}/credentials failed {creds_resp.status_code}: {creds_resp.text}"
        )
        log(f"[FANOUT] K8s connector registered: {_k8s_conn_id}")
        cls._provisioned_connector_id = _k8s_conn_id

        asset_name = f"nexplane-smoke-k8s-cluster-{instance_id}"
        _k8s_asset_id = client.register_asset_for_connector(
            asset_name, _k8s_conn_id, asset_type="server"
        )
        log(f"[FANOUT] K8s asset registered: {_k8s_asset_id}")
        cls._provisioned_asset_id = _k8s_asset_id

        # Build creds dict matching what get_connector_creds_from_db returns
        provisioned_creds = {"kubeconfig": kubeconfig_b64}
        cls.creds = provisioned_creds

        cls.core_v1 = _get_k8s_core_client(provisioned_creds)
        cls.suffix = uuid.uuid4().hex[:8]
        cls.cm_name = f"nexplane-smoke-fanout-{cls.suffix}"
        cls.cr_id = None

        log(f"[FANOUT] Pre-seeding ConfigMap {cls.cm_name} with old value")
        _create_or_update_configmap(cls.core_v1, cls.cm_name, SMOKE_NAMESPACE, SMOKE_CM_KEY, OLD_VALUE)

    @classmethod
    def teardown_class(cls):
        log("[FANOUT] Teardown: deleting smoke ConfigMap")
        try:
            _delete_configmap(cls.core_v1, cls.cm_name, SMOKE_NAMESPACE)
        except Exception as exc:
            log(f"[FANOUT] Teardown warning (ConfigMap): {exc}")

        # ------------------------------------------------------------------ #
        # Step 7: clean up provisioned resources (only what we created)      #
        # ------------------------------------------------------------------ #
        if cls._provisioned_connector_id:
            try:
                cls.client.delete(f"/connectors/{cls._provisioned_connector_id}")
                log(f"[FANOUT] Deregistered provisioned connector {cls._provisioned_connector_id}")
            except Exception as exc:
                log(f"[FANOUT] Teardown warning (connector): {exc}")

        if cls._provisioned_instance_id:
            try:
                ec2_client = _get_aws_boto3_client("ec2")
                if ec2_client:
                    ec2_client.terminate_instances(InstanceIds=[cls._provisioned_instance_id])
                    log(f"[FANOUT] Terminated K8s EC2 {cls._provisioned_instance_id}")
            except Exception as exc:
                log(f"[FANOUT] Teardown warning (EC2 terminate): {exc}")

    def test_phase1_execute(self):
        """Fan-out scan finds the seeded ConfigMap and replaces OLD_VALUE with NEW_VALUE."""
        log("[PHASE1] Starting credential_rotation_fanout execute smoke")

        cr = _run_fanout_cr(
            self.client,
            f"[SMOKE] credential_rotation_fanout {self.suffix}",
            search_terms=[OLD_VALUE],
            scan_scope=["kubernetes"],
            new_value=NEW_VALUE,
            timeout=EXECUTE_TIMEOUT,
        )
        assert cr["status"] == "completed", f"Expected completed, got {cr['status']}: {cr}"
        self.__class__.cr_id = cr["id"]
        log(f"[PHASE1] CR {cr['id']} completed")

        # Verify via direct K8s SDK that the ConfigMap was updated
        actual = _read_configmap_value(self.core_v1, self.cm_name, SMOKE_NAMESPACE, SMOKE_CM_KEY)
        assert actual == NEW_VALUE, (
            f"ConfigMap {self.cm_name}[{SMOKE_CM_KEY}] should be {NEW_VALUE!r}, got {actual!r}"
        )
        log(f"[PHASE1] ConfigMap value confirmed: {actual!r}")

        # Verify executor result contains our ConfigMap as an updated consumer
        consumers = []
        for run in cr.get("execution_runs", []):
            result = run.get("result") or {}
            consumers = result.get("consumers", [])
            if consumers:
                break

        updated = [c for c in consumers if c.get("update_result", {}).get("status") == "updated"]
        assert any(self.cm_name in c.get("location", "") for c in updated), (
            f"Expected ConfigMap {self.cm_name} in updated consumers; got: {consumers}"
        )
        log("[PHASE1] PASS")

    def test_phase2_rollback(self):
        """Rollback restores the ConfigMap to OLD_VALUE."""
        if not self.cr_id:
            pytest.skip("Phase 1 did not complete — no CR to roll back")

        log("[PHASE2] Starting rollback smoke")
        rb_cr = _rollback_cr(self.client, self.cr_id, ROLLBACK_TIMEOUT)
        assert rb_cr["status"] in (
            "rolled_back", "rolled_back_with_warnings", "rollback_partial"
        ), f"Unexpected rollback status: {rb_cr['status']}: {rb_cr}"
        log(f"[PHASE2] Rollback status: {rb_cr['status']}")

        # Verify via direct K8s SDK that the ConfigMap was restored
        actual = _read_configmap_value(self.core_v1, self.cm_name, SMOKE_NAMESPACE, SMOKE_CM_KEY)
        assert actual == OLD_VALUE, (
            f"ConfigMap {self.cm_name}[{SMOKE_CM_KEY}] should be restored to {OLD_VALUE!r}, got {actual!r}"
        )
        log(f"[PHASE2] ConfigMap value restored: {actual!r}")
        log("[PHASE2] PASS")
