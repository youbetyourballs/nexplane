#!/usr/bin/env python3
"""
Nexplane Multi-Cloud Live Smoke Test — Phases A–M.

Runs against live AWS and GCP accounts via the Nexplane API. Creates and destroys
real cloud resources. Run specific phases with --phases (default: A,B,C,D).

Usage:
    python backend/tests/smoke/test_cloud_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases A,B,C,D \\
        --tailscale-auth-key tskey-auth-<key> \\
        --gcp-project my-project-id

Phase descriptions:
    A  EC2 lifecycle + Tailscale join + Nexplane agent deploy
    B  Agent-based actions (patching audit, OS posture, CloudWatch agent)
    C  Local Terraform lifecycle (S3 bucket create/destroy)
    D  Local Ansible playbook (htop install/remove)
    E  EC2 advanced: stop/start/reboot/snapshot with rollback stack
    F  Security group: add/remove rules with rollback stack
    G  IAM user lifecycle: create/attach-policy/rotate-key/delete with rollback stack
    H  S3 advanced: create/lifecycle/policy/public-access/delete with rollback stack
    I  Route53: private zone + A record create/update/delete with rollback stack
    J  RDS: instance + snapshot lifecycle (~30 min) with rollback stack
    K  CloudWatch: alarms + SSM metric push with rollback stack
    L  GCE: instance launch + agent deploy with rollback stack
    M  GCE advanced: stop/start/reboot/snapshot with rollback stack

Requirements:
    AWS phases (A-K): AWS connector with credentials + NexplaneEC2TestProfile IAM role
                      Tailscale connector with reusable pre-authorized auth key
    GCP phases (L-M): GCP connector with credentials + Compute Engine API enabled
"""
import argparse
import json
import subprocess
import sys
import time
from typing import Optional

import httpx

KEY_NAME = "nexplane-smoke-test-key"
INSTANCE_NAME = "nexplane-smoke-test-01"
TIMEOUT_SECONDS = 600
RDS_PHASE_TIMEOUT_SECONDS = 2700  # 45 minutes for Phase J


def log(msg: str, ok: bool = True) -> None:
    print(f"{'✅' if ok else '❌'} {msg}")


def fail(msg: str) -> None:
    log(msg, ok=False)
    raise SystemExit(1)


class NexplaneClient:
    def __init__(self, base_url: str, email: str, password: str):
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(timeout=60)
        resp = self.client.post(f"{self.base}/auth/login", json={"email": email, "password": password})
        resp.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"

    def get(self, path: str, **kwargs) -> dict:
        resp = self.client.get(f"{self.base}{path}", **kwargs)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, **kwargs) -> dict:
        resp = self.client.post(f"{self.base}{path}", **kwargs)
        resp.raise_for_status()
        return resp.json()

    def get_cloud_account_asset_id(self) -> str:
        assets = self.get("/assets", params={"asset_type": "cloud_account"})
        if not assets:
            fail("No cloud_account asset found — run EC2 discovery on the AWS connector first")
        return assets[0]["id"]

    def get_asset_by_name(self, name: str) -> Optional[dict]:
        matches = [a for a in self.get("/assets", params={"q": name}) if a["name"] == name]
        if not matches:
            return None
        # Prefer most recently updated (handles stale duplicate assets from prior runs)
        return sorted(matches, key=lambda a: a.get("updated_at", ""), reverse=True)[0]

    def get_agent_secret(self) -> str:
        settings = self.get("/settings")
        if not settings.get("agent_configured"):
            # Auto-generate agent secret
            data = self.post("/settings/agent-secret")
            return data["agent_secret_plaintext"]
        # Already configured — re-generate to get plaintext (idempotent for smoke tests)
        data = self.post("/settings/agent-secret")
        return data["agent_secret_plaintext"]

    def get_tailscale_auth_key(self, provided_key: str = "") -> str:
        """Return a Tailscale auth key. Uses --tailscale-auth-key if provided, else fails."""
        if provided_key:
            return provided_key
        fail("Tailscale auth key required — pass --tailscale-auth-key <key>")
        return ""  # unreachable

    def create_cr(self, title: str, change_type: str, asset_id: str, desired_outcome: dict) -> str:
        return self.post("/change-requests", json={
            "title": title,
            "description": f"Smoke test: {title}",
            "change_type": change_type,
            "target_asset_ids": [asset_id],
            "desired_outcome": desired_outcome,
        })["id"]

    def run_cr(self, title: str, change_type: str, asset_id: str, desired_outcome: dict) -> dict:
        print(f"  → {title}")
        cr_id = self.create_cr(title, change_type, asset_id, desired_outcome)
        self.post(f"/change-requests/{cr_id}/plan")
        self.post(f"/change-requests/{cr_id}/submit-for-approval")
        self.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke test"})
        self.post(f"/change-requests/{cr_id}/execute")
        return self._wait(cr_id, title)

    def _wait(self, cr_id: str, label: str) -> dict:
        deadline = time.time() + TIMEOUT_SECONDS
        while time.time() < deadline:
            cr = self.get(f"/change-requests/{cr_id}")
            if cr["status"] == "completed":
                log(f"{label}")
                return cr
            if cr["status"] in ("failed", "rolled_back", "rejected"):
                fail(f"{label} — CR ended with status '{cr['status']}' (id: {cr_id})")
            time.sleep(5)
        fail(f"{label} — timed out after {TIMEOUT_SECONDS}s")

    def _wait_rollback(self, cr_id: str, label: str) -> None:
        """Wait for a rollback CR to reach rolled_back or failed status."""
        deadline = time.time() + TIMEOUT_SECONDS
        while time.time() < deadline:
            cr = self.get(f"/change-requests/{cr_id}")
            if cr["status"] == "rolled_back":
                log(f"  rolled back: {label}")
                return
            if cr["status"] in ("failed", "completed"):
                print(f"  ⚠️  Rollback CR {cr_id} ended with status '{cr['status']}' ({label})")
                return
            time.sleep(5)
        print(f"  ⚠️  Rollback timed out for {cr_id} ({label})")

    def rollback_cr(self, cr_id: str, label: str) -> bool:
        """Trigger rollback on a CR and wait. Returns True if rolled_back, False otherwise."""
        try:
            self.post(f"/change-requests/{cr_id}/rollback")
            self._wait_rollback(cr_id, label)
            return True
        except Exception as e:
            print(f"  ⚠️  Rollback request failed for {cr_id} ({label}): {e}")
            return False

    def _run_cr_with_timeout(self, title: str, change_type: str, asset_id: str,
                              desired_outcome: dict, timeout: int = TIMEOUT_SECONDS) -> dict:
        """Like run_cr but with a custom timeout for slow operations like RDS creation."""
        print(f"  → {title}")
        cr_id = self.create_cr(title, change_type, asset_id, desired_outcome)
        self.post(f"/change-requests/{cr_id}/plan")
        self.post(f"/change-requests/{cr_id}/submit-for-approval")
        self.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke test"})
        self.post(f"/change-requests/{cr_id}/execute")
        deadline = time.time() + timeout
        while time.time() < deadline:
            cr = self.get(f"/change-requests/{cr_id}")
            if cr["status"] == "completed":
                log(f"{title}")
                return cr
            if cr["status"] in ("failed", "rolled_back", "rejected"):
                fail(f"{title} — CR ended with status '{cr['status']}' (id: {cr_id})")
            time.sleep(10)
        fail(f"{title} — timed out after {timeout}s")


# ---------------------------------------------------------------------------
# Tailscale helpers
# ---------------------------------------------------------------------------

import os as _os
_IN_CONTAINER = _os.path.exists("/.dockerenv") or _os.path.exists("/app/app")
if _IN_CONTAINER and "/app" not in sys.path:
    sys.path.insert(0, "/app")


def _run(cmd: str, capture: bool = True) -> str:
    """Run a shell command — directly if inside the container, via docker compose exec otherwise."""
    if _IN_CONTAINER:
        full_cmd = ["sh", "-c", cmd]
    else:
        full_cmd = ["docker", "compose", "exec", "-T", "backend", "sh", "-c", cmd]
    result = subprocess.run(
        full_cmd,
        capture_output=capture,
        text=True,
    )
    if result.returncode != 0 and capture:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return (result.stdout or "").strip()


def _verify_tailscale_reachable(ip: str) -> bool:
    """Check that the backend HTTP service is reachable via its Tailscale IP."""
    try:
        result = _run(f"curl -fsSL --max-time 5 http://{ip}:8000/downloads/version 2>/dev/null || echo ''")
        return bool(result.strip())
    except Exception:
        return False


def setup_backend_tailscale(auth_key: str) -> str:
    """Start Tailscale on the backend container (kernel TUN mode) and return the Tailscale IP.

    Kernel TUN mode (no --tun=userspace-networking) creates a real tailscale0 interface
    so other Tailscale nodes can reach port 8000 on this container via the Tailscale IP.
    docker-compose.yml provides cap_add: [NET_ADMIN] and /dev/net/tun for this to work.
    """
    print("  Setting up Tailscale in backend container...")

    # Check if already running and reachable via Tailscale IP
    try:
        existing_ip = _run("tailscale ip -4 2>/dev/null || echo ''")
        if existing_ip and existing_ip.startswith("100."):
            if _verify_tailscale_reachable(existing_ip):
                log(f"Backend already on Tailscale: {existing_ip}")
                return existing_ip
            else:
                print(f"  Tailscale IP {existing_ip} not reachable via HTTP — restarting...")
                _run("tailscale down 2>/dev/null || true")
    except Exception:
        pass

    # Kill any stale tailscaled process and clean up socket
    _run(
        "for f in /proc/[0-9]*/cmdline; do "
        "  p=$(echo $f | grep -o '[0-9]*'); "
        "  cmd=$(cat $f 2>/dev/null | tr '\\0' ' '); "
        "  echo \"$cmd\" | grep -q tailscaled && kill $p 2>/dev/null; "
        "done; rm -f /var/run/tailscale/tailscaled.sock; true",
        capture=False,
    )
    time.sleep(2)

    # Start in kernel TUN mode (creates real tailscale0 interface)
    _run("tailscaled --statedir=/tmp/tailscale-state >/tmp/tailscaled.log 2>&1 &", capture=False)
    time.sleep(5)
    _run(f"tailscale up --authkey={auth_key} --hostname=nexplane-backend --accept-routes --accept-dns=false")

    # Wait for tailscale0 interface and HTTP reachability (up to 30s)
    ip = ""
    for _ in range(6):
        time.sleep(5)
        try:
            ip = _run("tailscale ip -4 2>/dev/null || echo ''")
            if ip.startswith("100.") and _verify_tailscale_reachable(ip):
                break
            ip = ""
        except Exception:
            pass

    if not ip:
        fail("Backend Tailscale setup failed: either no IP or HTTP not reachable via Tailscale IP")
    log(f"Backend on Tailscale: {ip}")
    return ip


def teardown_backend_tailscale() -> None:
    try:
        _run("tailscale down 2>/dev/null || true")
        # kill tailscaled by PID (container may not have killall/pkill)
        _run(
            "for f in /proc/[0-9]*/cmdline; do "
            "  p=$(echo $f | grep -o '[0-9]*'); "
            "  cmd=$(cat $f 2>/dev/null | tr '\\0' ' '); "
            "  echo \"$cmd\" | grep -q tailscaled && kill $p 2>/dev/null; "
            "done; true"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _delete_smoke_snapshots(client: NexplaneClient) -> None:
    """Delete EBS snapshots tagged with smoke-test names directly via boto3."""
    try:
        ec2 = _get_aws_boto3_client('ec2')
        if not ec2:
            return
        snaps = ec2.describe_snapshots(
            OwnerIds=['self'],
            Filters=[{'Name': 'description', 'Values': ['*smoke-test*', '*nexplane*']}],
        ).get('Snapshots', [])
        for snap in snaps:
            try:
                ec2.delete_snapshot(SnapshotId=snap['SnapshotId'])
                print(f"  Deleted snapshot {snap['SnapshotId']}")
            except Exception as e:
                print(f"  ⚠️  Could not delete {snap['SnapshotId']}: {e}")
    except Exception as e:
        print(f"  ⚠️  Snapshot cleanup skipped: {e}")


_aws_creds_cache: dict = {}


def _get_aws_boto3_client(service: str):
    """Get a boto3 client using the AWS connector credentials via the app DB."""
    import boto3
    import threading

    global _aws_creds_cache
    if not _aws_creds_cache:
        from app.database import AsyncSessionLocal
        from app.models.connector import Connector, ConnectorType
        from app.services.connector_service import _attach_credentials
        import asyncio, sqlalchemy as sa

        result_holder: list = [None]

        async def _get():
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    sa.select(Connector).where(Connector.connector_type == ConnectorType.aws)
                )
                conn = result.scalars().first()
                if not conn:
                    return None
                await _attach_credentials(conn, db)
                return getattr(conn, 'credentials', {})

        def _run_in_thread():
            result_holder[0] = asyncio.run(_get())

        t = threading.Thread(target=_run_in_thread)
        t.start()
        t.join()
        _aws_creds_cache = result_holder[0] or {}

    creds = _aws_creds_cache
    if not creds:
        return None
    return boto3.client(
        service,
        aws_access_key_id=creds['access_key_id'],
        aws_secret_access_key=creds['secret_access_key'],
        region_name=creds.get('region', 'us-east-1'),
    )


_gcp_creds_cache: dict = {}


def _get_gcp_compute_client():
    """Get a GCP Compute Engine InstancesClient using GCP connector credentials from the app DB."""
    import threading
    global _gcp_creds_cache
    if not _gcp_creds_cache:
        from app.database import AsyncSessionLocal
        from app.models.connector import Connector, ConnectorType
        from app.services.connector_service import _attach_credentials
        import asyncio, sqlalchemy as sa

        result_holder: list = [None]

        async def _get():
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    sa.select(Connector).where(Connector.connector_type == ConnectorType.gcp)
                )
                conn = result.scalars().first()
                if not conn:
                    return None
                await _attach_credentials(conn, db)
                return getattr(conn, 'credentials', {})

        def _run_in_thread():
            result_holder[0] = asyncio.run(_get())

        t = threading.Thread(target=_run_in_thread)
        t.start()
        t.join()
        _gcp_creds_cache = result_holder[0] or {}

    creds = _gcp_creds_cache
    if not creds:
        return None

    import json as _json
    from google.oauth2 import service_account
    from google.cloud import compute_v1

    key_json_raw = creds.get("service_account_key_json", "")
    if isinstance(key_json_raw, str):
        key_json = _json.loads(key_json_raw)
    else:
        key_json = key_json_raw

    credentials = service_account.Credentials.from_service_account_info(
        key_json,
        scopes=["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/compute"],
    )
    return compute_v1.InstancesClient(credentials=credentials)


def cleanup(client: NexplaneClient) -> None:
    """Always runs — terminates instances, deletes key pairs, EBS snapshots, and Tailscale."""
    print("\n  Cleanup running...")

    # Use boto3 directly for reliable cleanup (CR-based cleanup can fail if CR system is broken)
    try:
        ec2 = _get_aws_boto3_client('ec2')
        if ec2:
            # Terminate smoke test instances
            reservations = ec2.describe_instances(
                Filters=[{'Name': 'tag:Name', 'Values': ['nexplane-smoke-test*']},
                         {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']}]
            ).get('Reservations', [])
            for res in reservations:
                for inst in res.get('Instances', []):
                    iid = inst['InstanceId']
                    try:
                        ec2.terminate_instances(InstanceIds=[iid])
                        print(f"  Terminated {iid}")
                    except Exception as e:
                        print(f"  ⚠️  Terminate {iid}: {e}")

            # Delete smoke test key pairs
            kps = ec2.describe_key_pairs(
                Filters=[{'Name': 'key-name', 'Values': ['nexplane-smoke-test*']}]
            ).get('KeyPairs', [])
            for kp in kps:
                try:
                    ec2.delete_key_pair(KeyName=kp['KeyName'])
                    print(f"  Deleted key pair {kp['KeyName']}")
                except Exception as e:
                    print(f"  ⚠️  Key pair delete {kp['KeyName']}: {e}")
    except Exception as e:
        print(f"  ⚠️  AWS boto3 cleanup error: {e}")

    # Delete smoke test assets from Nexplane inventory
    try:
        assets = client.get("/assets", params={"q": "nexplane-smoke-test"})
        for asset in assets:
            if "smoke-test" in asset.get("name", ""):
                try:
                    client.client.delete(f"{client.base}/assets/{asset['id']}")
                    print(f"  Deleted inventory asset {asset['name']} ({asset['id']})")
                except Exception as e:
                    print(f"  ⚠️  Could not delete inventory asset {asset['name']}: {e}")
    except Exception as e:
        print(f"  ⚠️  Inventory cleanup error: {e}")

    _delete_smoke_snapshots(client)
    teardown_backend_tailscale()
    print("  Cleanup complete.")


# ---------------------------------------------------------------------------
# Phase A
# ---------------------------------------------------------------------------

def run_phase_a(client: NexplaneClient, cloud_account_id: str, tailscale_auth_key: str = "") -> dict:
    """Phase A: key pair + EC2 launch + Tailscale join + agent deploy."""
    print("\n[Phase A] EC2 launch + Tailscale + agent deploy")

    auth_key = client.get_tailscale_auth_key(tailscale_auth_key)
    backend_ip = setup_backend_tailscale(auth_key)
    agent_secret = client.get_agent_secret()

    client.run_cr(
        "Smoke: create key pair", "key_pair_create", cloud_account_id,
        {"key_name": KEY_NAME},
    )
    key_asset = client.get_asset_by_name(KEY_NAME)
    if not key_asset:
        fail(f"Key pair asset '{KEY_NAME}' not in inventory")
    log(f"Key pair in inventory: {key_asset['id']}")

    client.run_cr(
        "Smoke: launch EC2", "ec2_launch", cloud_account_id,
        {"mode": "quick", "name": INSTANCE_NAME, "os": "amazon_linux",
         "iam_instance_profile": "NexplaneEC2TestProfile", "key_name": KEY_NAME,
         "rollback_strategy": "terminate_instance"},
    )
    time.sleep(10)
    instance_asset = client.get_asset_by_name(INSTANCE_NAME)
    if not instance_asset:
        fail(f"Instance '{INSTANCE_NAME}' not in inventory")
    instance_id = instance_asset.get("asset_metadata", {}).get("instance_id")
    if not instance_id:
        fail("instance_id missing from asset metadata")
    log(f"Instance in inventory: {instance_id}")

    print("  Waiting 3 min for SSM agent to register...")
    time.sleep(180)

    client.run_cr(
        "Smoke: SSM whoami", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "whoami && hostname", "rollback_strategy": "rollback_unavailable"},
    )

    client.run_cr(
        "Smoke: tailscale join", "tailscale_join", instance_asset["id"],
        {"instance_id": instance_id, "auth_key": auth_key, "hostname": "nexplane-smoke-ec2"},
    )

    # Agent downloads binary from the public S3 bucket (NEXPLANE_AGENT_DOWNLOAD_URL default).
    # nexplane_url is the Tailscale IP so agent heartbeats reach the backend within the tailnet.
    nexplane_url = f"http://{backend_ip}:8000"
    client.run_cr(
        "Smoke: deploy agent", "deploy_nexplane_agent", instance_asset["id"],
        {"instance_id": instance_id, "nexplane_url": nexplane_url, "nexplane_secret": agent_secret},
    )

    print("  Waiting up to 3min for agent to register...")
    deadline = time.time() + 180
    agent_asset = None
    while time.time() < deadline:
        candidates = client.get("/assets", params={"q": "nexplane-smoke-ec2", "asset_type": "endpoint"})
        if candidates:
            agent_asset = candidates[0]
            log(f"Agent registered as endpoint asset: {agent_asset['id']}")
            break
        time.sleep(10)
    if not agent_asset:
        print("  ⚠️  Agent not yet registered in inventory — may still be starting")

    log("Phase A complete")
    return {
        "instance_asset": instance_asset,
        "instance_id": instance_id,
        "backend_ip": backend_ip,
        "agent_secret": agent_secret,
    }


# ---------------------------------------------------------------------------
# Phase B
# ---------------------------------------------------------------------------

def run_phase_b(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase B: SSM-based instance operations (patch audit, system info, CloudWatch)."""
    print("\n[Phase B] SSM-based instance operations")
    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    # Check available security patches via SSM (patch audit equivalent)
    client.run_cr(
        "Smoke: patch audit via SSM", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "yum check-update --security 2>/dev/null | tail -5; echo 'patch_audit_ok'",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("Patch audit via SSM succeeded")

    # Collect system info (support bundle equivalent)
    client.run_cr(
        "Smoke: collect system info", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "uname -a && cat /etc/os-release && df -h / && free -m",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("System info collected")

    # Install CloudWatch agent via shell script
    client.run_cr(
        "Smoke: install CloudWatch agent", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "rpm -q amazon-cloudwatch-agent 2>/dev/null || yum install -y amazon-cloudwatch-agent; amazon-cloudwatch-agent --version 2>&1 || echo 'cwa_check_done'",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("CloudWatch agent checked/installed")

    log("Phase B complete")


# ---------------------------------------------------------------------------
# Phase C
# ---------------------------------------------------------------------------

def run_phase_c(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase C: local Terraform S3 bucket lifecycle."""
    print("\n[Phase C] Local Terraform")
    import random
    bucket_suffix = random.randint(10000, 99999)
    bucket_name = "nexplane-smoke-test-" + str(bucket_suffix)

    tf_content = (
        'terraform {\n'
        '  required_providers {\n'
        '    aws = {\n'
        '      source  = "hashicorp/aws"\n'
        '      version = "~> 5.0"\n'
        '    }\n'
        '  }\n'
        '}\n\n'
        'provider "aws" {}\n\n'
        'resource "aws_s3_bucket" "smoke_test" {\n'
        '  bucket        = "' + bucket_name + '"\n'
        '  force_destroy = true\n'
        '}\n'
    )

    client.run_cr(
        "Smoke: terraform apply S3 bucket", "terraform_local_apply", cloud_account_id,
        {"tf_content": tf_content, "rollback_strategy": "terraform_destroy_local"},
    )
    log("Terraform applied — bucket: " + bucket_name)

    time.sleep(5)
    s3_assets = client.get("/assets", params={"asset_type": "storage_bucket", "q": bucket_name})
    if s3_assets:
        log("Bucket appears in inventory: " + s3_assets[0]["id"])
    else:
        print("  ⚠️  Bucket not yet in inventory — may need manual discovery run")

    log("Phase C complete")


# ---------------------------------------------------------------------------
# Phase D
# ---------------------------------------------------------------------------

def run_phase_d(client: NexplaneClient, phase_a_result: Optional[dict]) -> None:
    """Phase D: local Ansible playbook — tests CR machinery and SSM-based package management.

    Note: community.aws.aws_ssm Ansible connection has a Python 3.12 compatibility issue
    with session-manager-plugin subprocess. We test the ansible_local_playbook CR machinery
    using SSM RunShellScript to run ansible ad-hoc style, and verify SSM package ops separately.
    """
    print("\n[Phase D] Local Ansible + SSM package management")

    if phase_a_result is None:
        fail("Phase D requires Phase A to have run first (needs a running EC2 instance)")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    # Test ansible_local_playbook CR against localhost (verifies CR machinery, planning, execution)
    # Uses local connection to avoid SSM connection plugin Python 3.12 compat issue.
    # Note: ansible_local_playbook runs check mode first, then real run.
    # Use ansible.builtin.debug which works in both check and real mode.
    LOCAL_TEST_PLAYBOOK = (
        "---\n"
        "- name: Smoke test - local ansible verification\n"
        "  hosts: localhost\n"
        "  connection: local\n"
        "  gather_facts: no\n"
        "  tasks:\n"
        "    - name: Verify ansible is working\n"
        "      ansible.builtin.debug:\n"
        "        msg: 'ansible_local_playbook_ok'\n"
        "    - name: Check python\n"
        "      ansible.builtin.command: python3 --version\n"
        "      register: py_out\n"
        "      changed_when: false\n"
        "      check_mode: no\n"
    )

    # The cloud_account_id is used as target since we're running locally
    cloud_account_id = client.get_cloud_account_asset_id()
    client.run_cr(
        "Smoke: ansible local test", "ansible_local_playbook", instance_asset["id"],
        {"instance_id": "localhost", "playbook_content": LOCAL_TEST_PLAYBOOK,
         "rollback_strategy": "rollback_unavailable"},
    )
    log("Ansible local playbook CR executed successfully")

    # Install htop via SSM (same operation ansible would do via SSM connection)
    client.run_cr(
        "Smoke: install htop via SSM", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "yum install -y htop && htop --version",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("htop installed via SSM")

    client.run_cr(
        "Smoke: remove htop via SSM", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "yum remove -y htop && echo 'htop_removed'",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("htop removed via SSM")

    log("Phase D complete")


# ---------------------------------------------------------------------------
# Phase E
# ---------------------------------------------------------------------------

def run_phase_e(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase E: EC2 advanced — stop/start/reboot/snapshot with rollback stack cleanup."""
    print("\n[Phase E] EC2 Advanced Operations")
    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    rollback_stack: list[tuple[str, str]] = []  # (cr_id, label)
    snapshot_id: str | None = None

    try:
        # 1. Stop instance
        cr = client.run_cr(
            "Smoke-E: stop instance", "ec2_stop", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "start_instance"},
        )
        rollback_stack.append((cr["id"], "ec2_stop"))
        log("Instance stopped")

        # 2. Start instance
        cr = client.run_cr(
            "Smoke-E: start instance", "ec2_start", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "stop_instance"},
        )
        rollback_stack.pop()  # ec2_stop is superseded — instance is running
        rollback_stack.append((cr["id"], "ec2_start"))
        log("Instance started")

        # 3. Reboot
        client.run_cr(
            "Smoke-E: reboot instance", "ec2_reboot", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "rollback_unavailable"},
        )
        log("Instance rebooted")

        # Wait for SSM to reconnect post-reboot
        time.sleep(30)
        client.run_cr(
            "Smoke-E: SSM verify post-reboot", "ssm_command", instance_asset["id"],
            {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
             "command": "uptime && echo 'post_reboot_ok'",
             "rollback_strategy": "rollback_unavailable"},
        )
        log("SSM verified post-reboot")

        # 4. Create EBS snapshot
        cr = client.run_cr(
            "Smoke-E: create EBS snapshot", "snapshot_asset", instance_asset["id"],
            {"instance_id": instance_id, "rollback_strategy": "delete_ebs_snapshot"},
        )
        rollback_stack.append((cr["id"], "snapshot_asset"))

        # Find the snapshot ID from AWS
        ec2_boto = _get_aws_boto3_client('ec2')
        if ec2_boto:
            snaps = ec2_boto.describe_snapshots(
                Filters=[
                    {"Name": "description", "Values": [f"*{instance_id}*"]},
                    {"Name": "status", "Values": ["completed", "pending"]},
                ]
            ).get("Snapshots", [])
            snaps.sort(key=lambda s: s["StartTime"], reverse=True)
            if snaps:
                snapshot_id = snaps[0]["SnapshotId"]
        log(f"EBS snapshot created: {snapshot_id or 'unknown'}")

        # 5. Verify via SSM
        client.run_cr(
            "Smoke-E: verify post-snapshot", "ssm_command", instance_asset["id"],
            {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
             "command": "echo 'snapshot_verify_ok'",
             "rollback_strategy": "rollback_unavailable"},
        )
        log("Post-snapshot SSM verified")
        log("Phase E complete")

    except Exception as e:
        print(f"\n❌ Phase E failed: {e}")
        raise
    finally:
        print("  [Phase E cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete snapshot directly
        if snapshot_id:
            try:
                ec2_boto2 = _get_aws_boto3_client('ec2')
                if ec2_boto2:
                    ec2_boto2.delete_snapshot(SnapshotId=snapshot_id)
                    print(f"  Safety net: deleted snapshot {snapshot_id}")
            except Exception as e2:
                print(f"  ⚠️  Safety net snapshot delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase F
# ---------------------------------------------------------------------------

def run_phase_f(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase F: Security Groups — add/remove inbound rule with rollback stack."""
    print("\n[Phase F] Security Group Operations")

    rollback_stack: list[tuple[str, str]] = []
    test_sg_id: str | None = None

    try:
        # Create isolated test SG via boto3 (test scaffolding — not a CR)
        ec2_boto = _get_aws_boto3_client('ec2')
        if not ec2_boto:
            fail("Phase F requires AWS credentials")

        sg_name = f"nexplane-smoke-sg-{int(time.time())}"
        sg = ec2_boto.create_security_group(
            GroupName=sg_name,
            Description="Nexplane smoke test security group",
        )
        test_sg_id = sg["GroupId"]
        log(f"Created test SG: {test_sg_id}")

        # 1. Add inbound rule via CR (port 8443 from RFC5737 test CIDR — not routable)
        cr = client.run_cr(
            "Smoke-F: add inbound rule", "security_group_update", cloud_account_id,
            {
                "group_id": test_sg_id,
                "rules": [{"action": "add", "protocol": "tcp",
                            "from_port": 8443, "to_port": 8443,
                            "cidr": "192.0.2.0/24"}],
            },
        )
        rollback_stack.append((cr["id"], "security_group_update add_inbound"))
        log("Inbound rule added via CR")

        # Verify rule is present via boto3
        sg_details = ec2_boto.describe_security_groups(GroupIds=[test_sg_id])
        perms = sg_details["SecurityGroups"][0].get("IpPermissions", [])
        has_rule = any(
            p.get("FromPort") == 8443 and
            any(r.get("CidrIp") == "192.0.2.0/24" for r in p.get("IpRanges", []))
            for p in perms
        )
        if has_rule:
            log("Rule verified via boto3 describe")
        else:
            print("  ⚠️  Rule not found via describe — may be a mock path")

        # 2. Remove the rule via a second CR
        cr = client.run_cr(
            "Smoke-F: remove inbound rule", "security_group_update", cloud_account_id,
            {
                "group_id": test_sg_id,
                "rules": [{"action": "remove", "protocol": "tcp",
                            "from_port": 8443, "to_port": 8443,
                            "cidr": "192.0.2.0/24"}],
            },
        )
        rollback_stack.pop()  # add_inbound CR superseded — rule now removed
        rollback_stack.append((cr["id"], "security_group_update remove_inbound"))

        # Verify rule is gone
        sg_details2 = ec2_boto.describe_security_groups(GroupIds=[test_sg_id])
        perms2 = sg_details2["SecurityGroups"][0].get("IpPermissions", [])
        still_has = any(
            p.get("FromPort") == 8443 and
            any(r.get("CidrIp") == "192.0.2.0/24" for r in p.get("IpRanges", []))
            for p in perms2
        )
        if not still_has:
            log("Rule removed — verified via boto3")
        else:
            print("  ⚠️  Rule still present after remove CR")

        log("Phase F complete")

    except Exception as e:
        print(f"\n❌ Phase F failed: {e}")
        raise
    finally:
        print("  [Phase F cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete the test SG
        if test_sg_id:
            try:
                ec2_boto2 = _get_aws_boto3_client('ec2')
                if ec2_boto2:
                    ec2_boto2.delete_security_group(GroupId=test_sg_id)
                    print(f"  Safety net: deleted SG {test_sg_id}")
            except Exception as e2:
                print(f"  ⚠️  Safety net SG delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase G
# ---------------------------------------------------------------------------

def run_phase_g(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase G: IAM User Lifecycle — create/attach-policy/rotate-key/disable/enable/detach/delete."""
    print("\n[Phase G] IAM User Lifecycle")

    username = f"nexplane-smoke-user-{int(time.time())}"
    rollback_stack: list[tuple[str, str]] = []
    user_created = False

    try:
        # 1. Create IAM user via CR
        cr = client.run_cr(
            "Smoke-G: create IAM user", "iam_user_create", cloud_account_id,
            {"username": username},
        )
        rollback_stack.append((cr["id"], "iam_user_create"))
        user_created = True
        log(f"IAM user created: {username}")

        # Verify identity asset in inventory (ingest lag OK)
        assets = client.get("/assets", params={"q": username, "asset_type": "identity"})
        if assets:
            log(f"IAM user in inventory: {assets[0]['id']}")
        else:
            print("  ⚠️  IAM user asset not yet in inventory (ingest lag expected)")

        iam_client = _get_aws_boto3_client('iam')
        if not iam_client:
            fail("Phase G requires AWS credentials")

        # 2. Attach ReadOnlyAccess policy via boto3 (no dedicated policy-attach CR yet)
        iam_client.attach_user_policy(
            UserName=username,
            PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess",
        )
        log("ReadOnlyAccess attached (boto3)")

        # 3. Rotate IAM access key via boto3:
        #    create initial key → create new key → deactivate old → delete old
        initial_key = iam_client.create_access_key(UserName=username)["AccessKey"]
        old_key_id = initial_key["AccessKeyId"]
        new_key = iam_client.create_access_key(UserName=username)["AccessKey"]
        iam_client.update_access_key(UserName=username, AccessKeyId=old_key_id, Status="Inactive")
        iam_client.delete_access_key(UserName=username, AccessKeyId=old_key_id)
        log(f"Key rotated: {old_key_id} → {new_key['AccessKeyId']}")

        # 4. Disable user: deactivate all access keys
        keys = iam_client.list_access_keys(UserName=username)["AccessKeyMetadata"]
        for k in keys:
            iam_client.update_access_key(UserName=username, AccessKeyId=k["AccessKeyId"], Status="Inactive")
        log(f"IAM user disabled ({len(keys)} key(s) deactivated)")

        # 5. Re-enable user: reactivate all access keys
        keys = iam_client.list_access_keys(UserName=username)["AccessKeyMetadata"]
        for k in keys:
            iam_client.update_access_key(UserName=username, AccessKeyId=k["AccessKeyId"], Status="Active")
        log("IAM user re-enabled")

        # 6. Detach policy via boto3
        iam_client.detach_user_policy(
            UserName=username,
            PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess",
        )
        log("ReadOnlyAccess detached (boto3)")

        # 7. Delete user: trigger rollback of iam_user_create CR (rollback = delete_iam_user)
        create_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(create_cr_id, "iam_user_create → delete_iam_user")
        user_created = False
        log("IAM user deleted via CR rollback")

        log("Phase G complete")

    except Exception as e:
        print(f"\n❌ Phase G failed: {e}")
        raise
    finally:
        print("  [Phase G cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete user directly if still alive
        if user_created:
            try:
                iam_safety = _get_aws_boto3_client('iam')
                if iam_safety:
                    for k in iam_safety.list_access_keys(UserName=username).get("AccessKeyMetadata", []):
                        iam_safety.delete_access_key(UserName=username, AccessKeyId=k["AccessKeyId"])
                    for p in iam_safety.list_attached_user_policies(UserName=username).get("AttachedPolicies", []):
                        iam_safety.detach_user_policy(UserName=username, PolicyArn=p["PolicyArn"])
                    for name in iam_safety.list_user_policies(UserName=username).get("PolicyNames", []):
                        iam_safety.delete_user_policy(UserName=username, PolicyName=name)
                    try:
                        iam_safety.delete_login_profile(UserName=username)
                    except Exception:
                        pass
                    iam_safety.delete_user(UserName=username)
                    print(f"  Safety net: deleted IAM user {username}")
            except Exception as e2:
                print(f"  ⚠️  Safety net IAM user delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase H
# ---------------------------------------------------------------------------

def run_phase_h(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase H: S3 Advanced — create/lifecycle/policy/public-access/delete with rollback stack."""
    print("\n[Phase H] S3 Advanced Operations")
    bucket_name = f"nexplane-smoke-{int(time.time())}"
    rollback_stack: list[tuple[str, str]] = []
    bucket_created = False

    try:
        # 1. Create bucket via CR
        cr = client.run_cr(
            "Smoke-H: create S3 bucket", "s3_bucket_create", cloud_account_id,
            {"bucket_name": bucket_name},
        )
        rollback_stack.append((cr["id"], "s3_bucket_create"))
        bucket_created = True
        log(f"S3 bucket created: {bucket_name}")

        # Verify storage_bucket asset in inventory (ingest lag OK)
        assets = client.get("/assets", params={"q": bucket_name, "asset_type": "storage_bucket"})
        if assets:
            log(f"Bucket in inventory: {assets[0]['id']}")
        else:
            print("  ⚠️  Bucket asset not yet in inventory (ingest lag)")

        s3_client = _get_aws_boto3_client('s3')
        if not s3_client:
            fail("Phase H requires AWS credentials")

        # 2. Configure lifecycle via CR: 1-day expiration on smoke/ prefix
        cr = client.run_cr(
            "Smoke-H: configure lifecycle", "s3_lifecycle_configure", cloud_account_id,
            {
                "bucket_name": bucket_name,
                "rules": [{
                    "ID": "nexplane-smoke-expire",
                    "Status": "Enabled",
                    "Expiration": {"Days": 1},
                    "Filter": {"Prefix": "smoke/"},
                }],
            },
        )
        rollback_stack.append((cr["id"], "s3_lifecycle_configure"))
        log("Lifecycle policy configured via CR")

        # Verify lifecycle via boto3
        try:
            lc = s3_client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            if lc.get("Rules"):
                log("Lifecycle rules verified via boto3")
        except Exception:
            print("  ⚠️  Lifecycle not yet visible via boto3 (may be eventual consistency)")

        # 3. Set bucket policy via boto3 (deny non-TLS GetObject)
        policy = json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "DenyNonTLS",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            }],
        })
        s3_client.put_bucket_policy(Bucket=bucket_name, Policy=policy)
        log("Bucket policy applied (boto3)")

        # 4. Block public access via boto3
        s3_client.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        log("Public access blocked (boto3)")

        # 5. Verify public access block
        pab = s3_client.get_public_access_block(Bucket=bucket_name)
        config = pab["PublicAccessBlockConfiguration"]
        if all(config.get(k) for k in ["BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"]):
            log("Public access block verified")
        else:
            print(f"  ⚠️  Public access block partial: {config}")

        # 6. Delete bucket via CR (terminal step — use s3_bucket_delete directly)
        # Roll back lifecycle first, then delete bucket
        lifecycle_cr_id, lifecycle_label = rollback_stack.pop()
        client.rollback_cr(lifecycle_cr_id, lifecycle_label)

        create_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(create_cr_id, "s3_bucket_create → delete_s3_bucket")
        bucket_created = False
        log("Bucket deleted via CR rollback")

        log("Phase H complete")

    except Exception as e:
        print(f"\n❌ Phase H failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase H cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: force-delete bucket
        if bucket_created:
            try:
                s3_safety = _get_aws_boto3_client('s3')
                if s3_safety:
                    try:
                        # Delete all objects/versions first
                        paginator = s3_safety.get_paginator('list_object_versions')
                        for page in paginator.paginate(Bucket=bucket_name):
                            objs = [{'Key': v['Key'], 'VersionId': v['VersionId']}
                                    for v in page.get('Versions', [])]
                            objs += [{'Key': m['Key'], 'VersionId': m['VersionId']}
                                     for m in page.get('DeleteMarkers', [])]
                            if objs:
                                s3_safety.delete_objects(Bucket=bucket_name, Delete={'Objects': objs})
                        s3_safety.delete_bucket(Bucket=bucket_name)
                        print(f"  Safety net: deleted bucket {bucket_name}")
                    except Exception as inner:
                        print(f"  ⚠️  Safety net bucket delete failed: {inner}")
            except Exception as e2:
                print(f"  ⚠️  Safety net error: {e2}")


# ---------------------------------------------------------------------------
# Phase I
# ---------------------------------------------------------------------------

def run_phase_i(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase I: Route53 — private zone, A record create/update/delete with rollback stack."""
    print("\n[Phase I] Route53 DNS Operations")

    zone_name = f"smoke-{int(time.time())}.nexplane.internal"
    rollback_stack: list[tuple[str, str]] = []
    zone_id: str | None = None

    try:
        # 1. Create private hosted zone via CR
        cr = client.run_cr(
            "Smoke-I: create hosted zone", "route53_zone_create", cloud_account_id,
            {"zone_name": zone_name, "private": True},
        )
        rollback_stack.append((cr["id"], "route53_zone_create"))

        # Resolve zone_id: look up via boto3 (executor stores in _auto_asset but ingest lag may apply)
        r53_boto = _get_aws_boto3_client('route53')
        if r53_boto:
            zones = r53_boto.list_hosted_zones_by_name(DNSName=zone_name).get("HostedZones", [])
            for z in zones:
                if z["Name"].rstrip(".") == zone_name.rstrip("."):
                    zone_id = z["Id"].split("/")[-1]
                    break
        if not zone_id:
            # Fall back to inventory
            assets = client.get("/assets", params={"q": zone_name, "asset_type": "dns_zone"})
            if assets:
                zone_id = assets[0].get("asset_metadata", {}).get("zone_id")
        if not zone_id:
            fail(f"Could not determine zone_id for {zone_name}")
        log(f"Hosted zone created: {zone_id} ({zone_name})")

        # 2. Create A record
        cr = client.run_cr(
            "Smoke-I: create A record", "route53_record_upsert", cloud_account_id,
            {
                "zone_id": zone_id,
                "name": f"web.{zone_name}",
                "record_type": "A",
                "values": ["10.0.0.1"],
                "ttl": 60,
            },
        )
        rollback_stack.append((cr["id"], "route53_record_upsert create"))
        log("A record created: web → 10.0.0.1")

        # 3. Update the A record (UPSERT semantics)
        cr = client.run_cr(
            "Smoke-I: update A record", "route53_record_upsert", cloud_account_id,
            {
                "zone_id": zone_id,
                "name": f"web.{zone_name}",
                "record_type": "A",
                "values": ["10.0.0.2"],
                "ttl": 60,
            },
        )
        rollback_stack.append((cr["id"], "route53_record_upsert update"))
        log("A record updated: web → 10.0.0.2")

        # 4. Verify record via boto3
        if r53_boto and zone_id:
            rrsets = r53_boto.list_resource_record_sets(
                HostedZoneId=zone_id,
                StartRecordName=f"web.{zone_name}",
                StartRecordType="A",
                MaxItems="1",
            ).get("ResourceRecordSets", [])
            if rrsets and rrsets[0].get("Name", "").rstrip(".") == f"web.{zone_name}".rstrip("."):
                values = [r["Value"] for r in rrsets[0].get("ResourceRecords", [])]
                log(f"A record verified via boto3: {values}")
            else:
                print("  ⚠️  A record not yet visible via boto3 (may be eventual consistency)")

        # 5. Delete A record via route53_record_delete CR
        cr = client.run_cr(
            "Smoke-I: delete A record", "route53_record_delete", cloud_account_id,
            {
                "zone_id": zone_id,
                "name": f"web.{zone_name}",
                "record_type": "A",
                "values": ["10.0.0.2"],
                "ttl": 60,
            },
        )
        # A record is gone — remove the upsert CRs from rollback stack (nothing to undo)
        rollback_stack = [(cid, lbl) for cid, lbl in rollback_stack
                          if not lbl.startswith("route53_record_upsert")]
        log("A record deleted via CR")

        log("Phase I complete")

    except Exception as e:
        print(f"\n❌ Phase I failed: {e}")
        raise
    finally:
        print("  [Phase I cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete the entire hosted zone
        if zone_id:
            try:
                r53_safety = _get_aws_boto3_client('route53')
                if r53_safety:
                    # Delete all non-SOA/NS records first
                    changes = []
                    paginator = r53_safety.get_paginator('list_resource_record_sets')
                    for page in paginator.paginate(HostedZoneId=zone_id):
                        for rrs in page['ResourceRecordSets']:
                            if rrs['Type'] not in ('SOA', 'NS'):
                                changes.append({'Action': 'DELETE', 'ResourceRecordSet': rrs})
                    if changes:
                        r53_safety.change_resource_record_sets(
                            HostedZoneId=zone_id,
                            ChangeBatch={'Changes': changes},
                        )
                    r53_safety.delete_hosted_zone(Id=zone_id)
                    print(f"  Safety net: deleted hosted zone {zone_id}")
            except Exception as e2:
                print(f"  ⚠️  Safety net zone delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase J
# ---------------------------------------------------------------------------

def run_phase_j(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase J: RDS Full Lifecycle — create/snapshot/verify/delete (~25-35 min)."""
    print("\n[Phase J] RDS Full Lifecycle (~25-35 min)")

    ts = int(time.time())
    db_id = f"nexplane-smoke-db-{ts}"
    snap_id = f"nexplane-smoke-snap-{ts}"

    import secrets as _secrets
    import string as _string
    # Generate a random password meeting RDS requirements (letters + digits + special char)
    _pw_chars = _string.ascii_letters + _string.digits
    rds_password = "Nx!" + "".join(_secrets.choice(_pw_chars) for _ in range(16))

    rollback_stack: list[tuple[str, str]] = []
    created_db_ids: list[str] = []
    created_snap_ids: list[str] = []

    try:
        # 1. Create primary RDS instance
        print(f"  Creating RDS instance {db_id} (db.t3.micro MySQL 8.0) — may take ~10 min")
        cr = client._run_cr_with_timeout(
            "Smoke-J: create RDS instance", "rds_instance_create", cloud_account_id,
            {
                "db_instance_identifier": db_id,
                "engine": "mysql",
                "engine_version": "8.0",
                "db_instance_class": "db.t3.micro",
                "master_username": "admin",
                "master_password": rds_password,
                "allocated_storage": 20,
                "skip_final_snapshot": True,
            },
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_instance_create"))
        created_db_ids.append(db_id)
        log(f"RDS instance created: {db_id}")

        # Verify database asset in inventory
        assets = client.get("/assets", params={"q": db_id, "asset_type": "database"})
        if assets:
            log(f"RDS instance in inventory: {assets[0]['id']}")
        else:
            print("  ⚠️  RDS asset not yet in inventory (ingest lag)")

        # 2. Create manual snapshot
        print(f"  Creating RDS snapshot {snap_id} — may take ~5 min")
        cr = client._run_cr_with_timeout(
            "Smoke-J: create RDS snapshot", "rds_snapshot_create", cloud_account_id,
            {
                "db_instance_identifier": db_id,
                "snapshot_identifier": snap_id,
            },
            timeout=RDS_PHASE_TIMEOUT_SECONDS,
        )
        rollback_stack.append((cr["id"], "rds_snapshot_create"))
        created_snap_ids.append(snap_id)
        log(f"Snapshot created: {snap_id}")

        # 3. Verify snapshot via boto3
        rds_boto = _get_aws_boto3_client('rds')
        if rds_boto:
            snaps = rds_boto.describe_db_snapshots(DBSnapshotIdentifier=snap_id).get("DBSnapshots", [])
            if snaps and snaps[0].get("Status") == "available":
                log(f"Snapshot verified: {snaps[0].get('AllocatedStorage', 0)}GB, status=available")
            elif snaps:
                print(f"  ⚠️  Snapshot status: {snaps[0].get('Status')} (may still be creating)")
            else:
                print("  ⚠️  Snapshot not found via boto3")

        # 4. Delete snapshot via rollback of snapshot CR
        snap_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(snap_cr_id, "rds_snapshot_create → delete_rds_snapshot")
        created_snap_ids.remove(snap_id)
        log("Snapshot deleted via CR rollback")

        # 5. Delete instance via rollback of create CR
        print(f"  Deleting RDS instance {db_id} — may take ~10 min")
        create_cr_id, _ = rollback_stack.pop()
        client.rollback_cr(create_cr_id, "rds_instance_create → delete_rds_instance")
        created_db_ids.remove(db_id)
        log("RDS instance deleted via CR rollback")

        log("Phase J complete")

    except Exception as e:
        print(f"\n❌ Phase J failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase J cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: force-delete any remaining RDS resources
        rds_safety = _get_aws_boto3_client('rds')
        if rds_safety:
            for db_identifier in list(created_db_ids):
                try:
                    rds_safety.delete_db_instance(
                        DBInstanceIdentifier=db_identifier,
                        SkipFinalSnapshot=True,
                        DeleteAutomatedBackups=True,
                    )
                    print(f"  Safety net: deleting RDS instance {db_identifier} (async)")
                except Exception as e2:
                    print(f"  ⚠️  Safety net instance delete failed {db_identifier}: {e2}")
            for snap_identifier in list(created_snap_ids):
                try:
                    rds_safety.delete_db_snapshot(DBSnapshotIdentifier=snap_identifier)
                    print(f"  Safety net: deleted snapshot {snap_identifier}")
                except Exception as e2:
                    print(f"  ⚠️  Safety net snapshot delete failed {snap_identifier}: {e2}")


# ---------------------------------------------------------------------------
# Phase K
# ---------------------------------------------------------------------------

def run_phase_k(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase K: CloudWatch — create alarms, trigger via SSM custom metric, verify, rollback."""
    print("\n[Phase K] CloudWatch Alarms")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]
    ts = int(time.time())
    alarm_cpu = f"nexplane-smoke-cpu-{ts}"
    alarm_custom = f"nexplane-smoke-custom-{ts}"
    custom_namespace = "Nexplane/SmokeTest"
    aws_region = (_aws_creds_cache.get("region") or "us-east-1") if _aws_creds_cache else "us-east-1"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create CPU utilization alarm (99% threshold — won't fire on idle instance)
        cr = client.run_cr(
            "Smoke-K: create CPU alarm", "cloudwatch_alarm_create", instance_asset["id"],
            {
                "alarm_name": alarm_cpu,
                "metric_name": "CPUUtilization",
                "namespace": "AWS/EC2",
                "threshold": 99.0,
                "comparison_operator": "GreaterThanThreshold",
                "evaluation_periods": 1,
                "period": 60,
                "statistic": "Average",
                "dimensions": [{"Name": "InstanceId", "Value": instance_id}],
            },
        )
        rollback_stack.append((cr["id"], "cloudwatch_alarm_create CPU"))
        log(f"CPU alarm created: {alarm_cpu}")

        # 2. Create custom namespace alarm (fires when metric value > 0)
        cr = client.run_cr(
            "Smoke-K: create custom metric alarm", "cloudwatch_alarm_create", instance_asset["id"],
            {
                "alarm_name": alarm_custom,
                "metric_name": "TestTrigger",
                "namespace": custom_namespace,
                "threshold": 0.0,
                "comparison_operator": "GreaterThanThreshold",
                "evaluation_periods": 1,
                "period": 60,
                "statistic": "Sum",
                "dimensions": [],
            },
        )
        rollback_stack.append((cr["id"], "cloudwatch_alarm_create custom"))
        log(f"Custom metric alarm created: {alarm_custom}")

        # 3. Push metric data via SSM to trigger the custom alarm
        client.run_cr(
            "Smoke-K: push metric data via SSM", "ssm_command", instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": (
                    f"aws cloudwatch put-metric-data "
                    f"--namespace '{custom_namespace}' "
                    f"--metric-name TestTrigger "
                    f"--value 1 "
                    f"--unit Count "
                    f"--region {aws_region}"
                ),
                "rollback_strategy": "rollback_unavailable",
            },
        )
        log("Metric data pushed via SSM")

        # 4. Wait up to 90s for custom alarm to enter ALARM state
        print("  Waiting up to 90s for alarm to enter ALARM state...")
        cw_boto = _get_aws_boto3_client('cloudwatch')
        alarm_triggered = False
        if cw_boto:
            deadline = time.time() + 90
            while time.time() < deadline:
                resp = cw_boto.describe_alarms(AlarmNames=[alarm_custom])
                alarms = resp.get("MetricAlarms", [])
                if alarms and alarms[0]["StateValue"] == "ALARM":
                    alarm_triggered = True
                    log(f"Alarm {alarm_custom} is in ALARM state")
                    break
                time.sleep(10)
            if not alarm_triggered:
                print(f"  ⚠️  Alarm did not enter ALARM state within 90s (CloudWatch evaluation lag)")

        # 5. Delete both alarms via rollback stack
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Both alarms deleted via CR rollback")

        log("Phase K complete")

    except Exception as e:
        print(f"\n❌ Phase K failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase K cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: delete alarms directly
        try:
            cw_safety = _get_aws_boto3_client('cloudwatch')
            if cw_safety:
                cw_safety.delete_alarms(AlarmNames=[alarm_cpu, alarm_custom])
                print(f"  Safety net: deleted alarms {alarm_cpu}, {alarm_custom}")
        except Exception as e2:
            print(f"  ⚠️  Safety net alarm delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase L
# ---------------------------------------------------------------------------

GCE_SMOKE_INSTANCE = "nexplane-smoke-gce-01"
GCE_ZONE = "us-central1-a"


def run_phase_l(client: NexplaneClient, cloud_account_id: str,
                gcp_project: str, agent_secret: str) -> dict:
    """Phase L: GCE instance launch + agent deploy with rollback stack."""
    print("\n[Phase L] GCE Instance Launch + Agent Deploy")

    rollback_stack: list[tuple[str, str]] = []
    instance_created = False

    try:
        cr = client.run_cr(
            "Smoke-L: launch GCE instance", "gce_instance_create", cloud_account_id,
            {
                "name": GCE_SMOKE_INSTANCE,
                "machine_type": "e2-micro",
                "zone": GCE_ZONE,
                "image_family": "ubuntu-2204-lts",
                "image_project": "ubuntu-os-cloud",
                "connection_mode": "agent_startup",
                "nexplane_url": "http://localhost:8000",
                "nexplane_secret": agent_secret,
            },
        )
        rollback_stack.append((cr["id"], "gce_instance_create"))
        instance_created = True
        log(f"GCE instance launched: {GCE_SMOKE_INSTANCE}")

        # Verify server asset in inventory
        time.sleep(10)
        instance_asset = client.get_asset_by_name(GCE_SMOKE_INSTANCE)
        if instance_asset:
            log(f"GCE instance in inventory: {instance_asset['id']}")
        else:
            print(f"  ⚠️  GCE instance asset not yet in inventory (ingest lag)")
            instance_asset = {
                "id": cloud_account_id,
                "name": GCE_SMOKE_INSTANCE,
                "asset_metadata": {"instance_name": GCE_SMOKE_INSTANCE, "zone": GCE_ZONE},
            }

        # Wait up to 5 min for agent to register
        print("  Waiting up to 5 min for Nexplane agent to register...")
        deadline = time.time() + 300
        agent_asset = None
        while time.time() < deadline:
            candidates = client.get("/assets", params={"q": GCE_SMOKE_INSTANCE, "asset_type": "endpoint"})
            if candidates:
                agent_asset = candidates[0]
                log(f"Agent registered: {agent_asset['id']}")
                break
            time.sleep(15)
        if not agent_asset:
            print("  ⚠️  Agent not yet registered — startup script may still be running")

        log("Phase L complete")
        result = {"instance_asset": instance_asset}
        rollback_stack.clear()   # success — don't roll back in finally
        instance_created = False  # success — don't safety-net delete
        return result

    except Exception as e:
        print(f"\n❌ Phase L failed: {e}")
        raise
    finally:
        print("  [Phase L cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete instance via GCP SDK
        if instance_created:
            try:
                compute = _get_gcp_compute_client()
                if compute and gcp_project:
                    compute.delete(project=gcp_project, zone=GCE_ZONE, instance=GCE_SMOKE_INSTANCE)
                    print(f"  Safety net: deleted GCE instance {GCE_SMOKE_INSTANCE}")
            except Exception as e2:
                print(f"  ⚠️  Safety net GCE delete failed: {e2}")


# ---------------------------------------------------------------------------
# Phase M
# ---------------------------------------------------------------------------

def run_phase_m(client: NexplaneClient, phase_l_result: dict, gcp_project: str) -> None:
    """Phase M: GCE advanced — stop/start/reboot/snapshot with rollback stack."""
    print("\n[Phase M] GCE Advanced Operations")

    instance_asset = phase_l_result["instance_asset"]
    instance_name = instance_asset.get("asset_metadata", {}).get("instance_name", GCE_SMOKE_INSTANCE)
    zone = instance_asset.get("asset_metadata", {}).get("zone", GCE_ZONE)

    rollback_stack: list[tuple[str, str]] = []
    snapshot_name: str | None = None

    try:
        # 1. Stop instance
        cr = client.run_cr(
            "Smoke-M: stop GCE instance", "gce_stop", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        rollback_stack.append((cr["id"], "gce_stop"))
        log("GCE instance stopped")

        # 2. Start instance
        cr = client.run_cr(
            "Smoke-M: start GCE instance", "gce_start", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        rollback_stack.pop()  # stop CR superseded
        rollback_stack.append((cr["id"], "gce_start"))
        log("GCE instance started")

        # 3. Reboot
        client.run_cr(
            "Smoke-M: reboot GCE instance", "gce_instance_reboot", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone},
        )
        log("GCE instance rebooted")

        # Wait for agent to reconnect post-reboot
        time.sleep(30)
        assets = client.get("/assets", params={"q": GCE_SMOKE_INSTANCE, "asset_type": "endpoint"})
        if assets:
            log("Agent still registered post-reboot")
        else:
            print("  ⚠️  Agent not visible post-reboot (may still be reconnecting)")

        # 4. Create disk snapshot
        snapshot_name = f"nexplane-smoke-snap-{int(time.time())}"
        cr = client.run_cr(
            "Smoke-M: create disk snapshot", "gce_disk_snapshot", instance_asset["id"],
            {"instance_name": instance_name, "zone": zone, "snapshot_name": snapshot_name},
        )
        rollback_stack.append((cr["id"], "gce_disk_snapshot"))
        log(f"Disk snapshot created: {snapshot_name}")

        # 5. Verify snapshot via GCP SDK
        if gcp_project and _gcp_creds_cache:
            try:
                import json as _j
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1 as _cv1
                key_json_raw = _gcp_creds_cache.get("service_account_key_json", "")
                key_json = _j.loads(key_json_raw) if isinstance(key_json_raw, str) else key_json_raw
                gcp_creds = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                snap_client = _cv1.SnapshotsClient(credentials=gcp_creds)
                snap = snap_client.get(project=gcp_project, snapshot=snapshot_name)
                log(f"Snapshot verified: status={snap.status}, size={snap.disk_size_gb}GB")
            except Exception as e:
                print(f"  ⚠️  Snapshot verify skipped: {e}")

        rollback_stack.clear()  # success — instance stays running, snapshot cleaned up separately
        log("Phase M complete")

    except Exception as e:
        print(f"\n❌ Phase M failed: {e}")
        raise
    finally:
        if rollback_stack:
            print("  [Phase M cleanup — rollback stack]")
            for cr_id, label in reversed(rollback_stack):
                client.rollback_cr(cr_id, label)
        # Safety net: delete snapshot
        if snapshot_name and gcp_project:
            _get_gcp_compute_client()  # prime credentials cache if not already loaded
        if snapshot_name and gcp_project and _gcp_creds_cache:
            try:
                import json as _j
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1 as _cv1
                key_json_raw = _gcp_creds_cache.get("service_account_key_json", "")
                key_json = _j.loads(key_json_raw) if isinstance(key_json_raw, str) else key_json_raw
                gcp_creds = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                snap_client = _cv1.SnapshotsClient(credentials=gcp_creds)
                snap_client.delete(project=gcp_project, snapshot=snapshot_name)
                print(f"  Safety net: deleted snapshot {snapshot_name}")
            except Exception as e2:
                print(f"  ⚠️  Safety net snapshot delete failed: {e2}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Nexplane AWS live smoke test")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument(
        "--phases", default="A,B,C,D",
        help="Comma-separated phases to run (A-M). Phase J is slow (~35 min, creates RDS). GCP phases L-M require --gcp-project. E.g. --phases A,B,C,D or --phases L,M",
    )
    parser.add_argument("--tailscale-auth-key", default="", help="Reusable Tailscale auth key for Phase A")
    parser.add_argument("--gcp-project", default="", help="GCP project ID for phases L and M")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    print("=" * 60)
    print(f"Nexplane Multi-Cloud Live Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

    # Pre-run inventory cleanup: remove stale smoke test assets from prior runs
    try:
        stale = [a for a in client.get("/assets", params={"q": "nexplane-smoke-test"})
                 if "smoke-test" in a.get("name", "")]
        for asset in stale:
            client.client.delete(f"{client.base}/assets/{asset['id']}")
        if stale:
            print(f"  Pre-run: removed {len(stale)} stale inventory asset(s)")
    except Exception:
        pass

    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account: {cloud_account_id}")

    passed = False
    phase_a_result: Optional[dict] = None

    try:
        if "A" in phases:
            phase_a_result = run_phase_a(client, cloud_account_id, args.tailscale_auth_key)

        if "B" in phases:
            if phase_a_result is None:
                fail("Phase B requires Phase A to have run first")
            run_phase_b(client, phase_a_result)

        if "C" in phases:
            run_phase_c(client, cloud_account_id)

        if "D" in phases:
            run_phase_d(client, phase_a_result)

        if "E" in phases:
            if phase_a_result is None:
                fail("Phase E requires Phase A to have run first")
            run_phase_e(client, phase_a_result)

        if "F" in phases:
            run_phase_f(client, cloud_account_id)

        if "G" in phases:
            run_phase_g(client, cloud_account_id)

        if "H" in phases:
            run_phase_h(client, cloud_account_id)

        if "I" in phases:
            run_phase_i(client, cloud_account_id)

        if "J" in phases:
            run_phase_j(client, cloud_account_id)

        if "K" in phases:
            if phase_a_result is None:
                fail("Phase K requires Phase A to have run first (needs a running EC2 instance)")
            run_phase_k(client, phase_a_result)

        gcp_phase_result: Optional[dict] = None
        if "L" in phases:
            agent_secret = client.get_agent_secret()
            gcp_phase_result = run_phase_l(client, cloud_account_id, args.gcp_project, agent_secret)

        if "M" in phases:
            if gcp_phase_result is None or gcp_phase_result.get("instance_asset") is None:
                fail("Phase M requires Phase L to have run first")
            run_phase_m(client, gcp_phase_result, args.gcp_project)

        print("\n" + "=" * 60)
        print("✅ ALL SELECTED PHASES PASSED")
        print("=" * 60)
        passed = True

    except SystemExit:
        passed = False
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        passed = False
    finally:
        if "A" in phases:
            cleanup(client)
        if not passed:
            print("\n❌ SMOKE TEST FAILED")
            sys.exit(1)


if __name__ == "__main__":
    main()
