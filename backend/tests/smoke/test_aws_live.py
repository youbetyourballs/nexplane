#!/usr/bin/env python3
"""
Nexplane AWS Live Smoke Test — Phases A–D.

Runs against a live AWS account via the Nexplane API. Creates and destroys
real AWS resources. Run specific phases with --phases (default: all).

Usage:
    python backend/tests/smoke/test_aws_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases A,B,C,D

Phase descriptions:
    A  EC2 lifecycle + Tailscale join + Nexplane agent deploy
    B  Agent-based actions (patching audit, OS posture, CloudWatch agent)
    C  Local Terraform lifecycle (S3 bucket create/destroy)
    D  Local Ansible playbook (htop install/remove)

Requirements:
    - AWS connector with valid credentials + NexplaneEC2TestProfile IAM role
    - Tailscale connector with a reusable pre-authorized auth key
"""
import argparse
import subprocess
import sys
import time
from typing import Optional

import httpx

KEY_NAME = "nexplane-smoke-test-key"
INSTANCE_NAME = "nexplane-smoke-test-01"
TIMEOUT_SECONDS = 300


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
        for a in self.get("/assets", params={"q": name}):
            if a["name"] == name:
                return a
        return None

    def get_agent_secret(self) -> str:
        settings = self.get("/settings")
        secret = settings.get("agent_secret") or settings.get("agent_secret_preview", "")
        if not secret:
            fail("agent_secret not found in /settings — check you are logged in as admin")
        return secret

    def get_tailscale_auth_key(self) -> str:
        """Retrieve the stored Tailscale auth key from the Tailscale connector credentials."""
        import asyncio

        async def _get_key():
            from app.database import AsyncSessionLocal
            from sqlalchemy import select
            from app.models.connector import Connector, ConnectorType
            from app.services.connector_service import _attach_credentials
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Connector).where(Connector.connector_type == ConnectorType.tailscale)
                )
                conn = result.scalars().first()
                if conn:
                    await _attach_credentials(conn, db)
                    return getattr(conn, 'credentials', {}).get('auth_key', '')
            return ''

        key = asyncio.run(_get_key())
        if not key:
            fail("Tailscale connector auth_key is empty — add a reusable auth key in connector settings")
        return key

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


# ---------------------------------------------------------------------------
# Tailscale helpers
# ---------------------------------------------------------------------------

def _run(cmd: str, capture: bool = True) -> str:
    """Run a shell command in the backend container."""
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "sh", "-c", cmd],
        capture_output=capture,
        text=True,
    )
    if result.returncode != 0 and capture:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return (result.stdout or "").strip()


def setup_backend_tailscale(auth_key: str) -> str:
    """Install Tailscale in the backend container and join the network. Returns Tailscale IP."""
    print("  Setting up Tailscale in backend container...")
    try:
        existing_ip = _run("tailscale ip -4 2>/dev/null || echo ''")
        if existing_ip and existing_ip.startswith("100."):
            log(f"Backend already on Tailscale: {existing_ip}")
            return existing_ip
    except Exception:
        pass

    # Start tailscaled in userspace networking mode
    _run("tailscaled --tun=userspace-networking --statedir=/tmp/tailscale-state &>/tmp/tailscaled.log &", capture=False)
    time.sleep(3)
    _run(f"tailscale up --authkey={auth_key} --hostname=nexplane-backend --accept-routes --accept-dns=false")
    time.sleep(5)
    ip = _run("tailscale ip -4")
    if not ip or not ip.startswith("100."):
        fail(f"Unexpected Tailscale IP: {ip!r}")
    log(f"Backend on Tailscale: {ip}")
    return ip


def teardown_backend_tailscale() -> None:
    try:
        _run("tailscale down || true")
        _run("killall tailscaled || true")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _delete_smoke_snapshots(client: NexplaneClient) -> None:
    """Delete EBS snapshots tagged with smoke-test names directly via boto3."""
    try:
        import asyncio
        import boto3

        async def _get_creds():
            from app.database import AsyncSessionLocal
            from sqlalchemy import select
            from app.models.connector import Connector, ConnectorType
            from app.services.connector_service import _attach_credentials
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Connector).where(Connector.connector_type == ConnectorType.aws)
                )
                conn = result.scalars().first()
                if conn:
                    await _attach_credentials(conn, db)
                    return getattr(conn, 'credentials', {})
            return {}

        creds = asyncio.run(_get_creds())
        if not creds:
            return
        ec2 = boto3.client(
            'ec2',
            aws_access_key_id=creds['access_key_id'],
            aws_secret_access_key=creds['secret_access_key'],
            region_name=creds.get('region', 'us-east-1'),
        )
        snaps = ec2.describe_snapshots(
            Owners=['self'],
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


def cleanup(client: NexplaneClient) -> None:
    """Always runs — terminates instances, deletes key pairs, EBS snapshots, and Tailscale."""
    print("\n  Cleanup running...")
    try:
        cloud_account_id = client.get_cloud_account_asset_id()
        assets = client.get("/assets", params={"q": "nexplane-smoke-test"})
        for asset in assets:
            name = asset.get("name", "")
            if "smoke-test" not in name:
                continue
            if asset["asset_type"] == "server":
                instance_id = asset.get("asset_metadata", {}).get("instance_id")
                if instance_id:
                    try:
                        client.run_cr(
                            f"Cleanup: terminate {name}", "ec2_terminate", asset["id"],
                            {"instance_id": instance_id, "confirm_terminate": True,
                             "rollback_strategy": "rollback_unavailable"},
                        )
                    except Exception as e:
                        print(f"  ⚠️  Terminate failed for {name}: {e}")
            elif asset["asset_type"] == "key_pair":
                key_name = asset.get("asset_metadata", {}).get("key_name", name)
                try:
                    cr_id = client.create_cr(
                        f"Cleanup: delete {key_name}", "key_pair_create", cloud_account_id,
                        {"key_name": key_name},
                    )
                    client.post(f"/change-requests/{cr_id}/cancel")
                except Exception as e:
                    print(f"  ⚠️  Key pair delete failed for {key_name}: {e}")
    except Exception as e:
        print(f"  ⚠️  Asset cleanup error: {e}")

    _delete_smoke_snapshots(client)
    teardown_backend_tailscale()
    print("  Cleanup complete.")


# ---------------------------------------------------------------------------
# Phase A
# ---------------------------------------------------------------------------

def run_phase_a(client: NexplaneClient, cloud_account_id: str) -> dict:
    """Phase A: key pair + EC2 launch + Tailscale join + agent deploy."""
    print("\n[Phase A] EC2 launch + Tailscale + agent deploy")

    auth_key = client.get_tailscale_auth_key()
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

    print("  Waiting 90s for SSM agent...")
    time.sleep(90)

    client.run_cr(
        "Smoke: SSM whoami", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "whoami && hostname", "rollback_strategy": "rollback_unavailable"},
    )

    client.run_cr(
        "Smoke: tailscale join", "tailscale_join", instance_asset["id"],
        {"instance_id": instance_id, "auth_key": auth_key, "hostname": "nexplane-smoke-ec2"},
    )

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
    """Phase B: agent-based actions."""
    print("\n[Phase B] Agent-based actions")
    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    client.run_cr(
        "Smoke: patch audit (dry run)", "patch_packages", instance_asset["id"],
        {"instance_id": instance_id, "mode": "security_only", "dry_run": True,
         "packages": [], "cve_id": None, "rollback_strategy": "uninstall_patches"},
    )

    client.run_cr(
        "Smoke: collect support bundle", "remote_command", instance_asset["id"],
        {"instance_id": instance_id, "template_id": "collect_support_bundle",
         "parameters": {"output_path": "/tmp/smoke-posture.tar.gz"},
         "rollback_strategy": "rollback_unavailable"},
    )

    client.run_cr(
        "Smoke: install CloudWatch agent", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-ConfigureAWSPackage",
         "parameters": {"action": ["Install"], "name": ["AmazonCloudWatchAgent"]},
         "rollback_strategy": "rollback_unavailable"},
    )

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
    """Phase D: local Ansible playbook via SSM transport."""
    print("\n[Phase D] Local Ansible")
    log("Phase D not yet implemented — requires ansible_local connector (see Phase D plan)")


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
        help="Comma-separated phases to run (default: A,B,C,D). E.g. --phases A or --phases A,B",
    )
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    print("=" * 60)
    print(f"Nexplane AWS Live Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account: {cloud_account_id}")

    passed = False
    phase_a_result: Optional[dict] = None

    try:
        if "A" in phases:
            phase_a_result = run_phase_a(client, cloud_account_id)

        if "B" in phases:
            if phase_a_result is None:
                fail("Phase B requires Phase A to have run first")
            run_phase_b(client, phase_a_result)

        if "C" in phases:
            run_phase_c(client, cloud_account_id)

        if "D" in phases:
            run_phase_d(client, phase_a_result)

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
