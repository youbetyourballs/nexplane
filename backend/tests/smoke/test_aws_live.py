#!/usr/bin/env python3
"""
AWS Live Smoke Test — Nexplane Phase 1 & 2 validation.

Runs against a live AWS account via the Nexplane API. Creates and destroys
real AWS resources. Intended to catch regressions in EC2, SSM, key pair,
and agent-related components.

Usage:
    python backend/tests/smoke/test_aws_live.py \
        --base-url http://localhost:8000 \
        --email admin@nexplane.local \
        --password changeme

Requirements:
    - AWS connector configured in Nexplane with valid credentials
    - IAM instance profile NexplaneEC2TestProfile exists
    - iam:PassRole granted to connector IAM user
"""
import argparse
import sys
import time
from typing import Optional

import httpx

KEY_NAME = "nexplane-smoke-test-key"
INSTANCE_NAME = "nexplane-smoke-test-01"
TIMEOUT_SECONDS = 300  # 5 minutes per CR step


def log(msg: str, ok: bool = True) -> None:
    prefix = "✅" if ok else "❌"
    print(f"{prefix} {msg}")


def fail(msg: str) -> None:
    log(msg, ok=False)
    sys.exit(1)


class NexplaneClient:
    def __init__(self, base_url: str, email: str, password: str):
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30)
        resp = self.client.post(f"{self.base}/auth/login", json={"email": email, "password": password})
        resp.raise_for_status()
        token = resp.json()["access_token"]
        self.client.headers["Authorization"] = f"Bearer {token}"

    def get_cloud_account_asset_id(self) -> str:
        resp = self.client.get(f"{self.base}/assets", params={"asset_type": "cloud_account"})
        resp.raise_for_status()
        assets = resp.json()
        if not assets:
            fail("No cloud_account asset found — run EC2 discovery on the AWS connector first")
        return assets[0]["id"]

    def get_asset_by_name(self, name: str) -> Optional[dict]:
        resp = self.client.get(f"{self.base}/assets", params={"q": name})
        resp.raise_for_status()
        for a in resp.json():
            if a["name"] == name:
                return a
        return None

    def create_cr(self, title: str, change_type: str, asset_id: str, desired_outcome: dict) -> str:
        resp = self.client.post(f"{self.base}/change-requests", json={
            "title": title,
            "description": f"Smoke test: {title}",
            "change_type": change_type,
            "target_asset_ids": [asset_id],
            "desired_outcome": desired_outcome,
        })
        resp.raise_for_status()
        return resp.json()["id"]

    def generate_plan(self, cr_id: str) -> None:
        resp = self.client.post(f"{self.base}/change-requests/{cr_id}/plan")
        if not resp.is_success:
            fail(f"Plan generation failed for CR {cr_id}: {resp.text}")

    def submit_for_approval(self, cr_id: str) -> None:
        resp = self.client.post(f"{self.base}/change-requests/{cr_id}/submit-for-approval")
        if not resp.is_success:
            fail(f"Submit for approval failed for CR {cr_id}: {resp.text}")

    def approve(self, cr_id: str) -> None:
        resp = self.client.post(f"{self.base}/change-requests/{cr_id}/approve", json={
            "decision": "approved", "comment": "Smoke test auto-approval"
        })
        if not resp.is_success:
            fail(f"Approval failed for CR {cr_id}: {resp.text}")

    def execute_cr(self, cr_id: str) -> None:
        resp = self.client.post(f"{self.base}/change-requests/{cr_id}/execute")
        if not resp.is_success:
            fail(f"Execute failed for CR {cr_id}: {resp.text}")

    def wait_for_completion(self, cr_id: str, step_name: str) -> dict:
        deadline = time.time() + TIMEOUT_SECONDS
        while time.time() < deadline:
            resp = self.client.get(f"{self.base}/change-requests/{cr_id}")
            resp.raise_for_status()
            cr = resp.json()
            if cr["status"] == "completed":
                log(f"{step_name} completed")
                return cr
            if cr["status"] in ("failed", "rolled_back", "rejected"):
                fail(f"{step_name} ended with status '{cr['status']}' (CR: {cr_id})")
            time.sleep(5)
        fail(f"{step_name} timed out after {TIMEOUT_SECONDS}s (CR: {cr_id})")

    def run_cr(self, title: str, change_type: str, asset_id: str, desired_outcome: dict) -> dict:
        """Full CR lifecycle: create → plan → approve → execute → wait."""
        print(f"  Running CR: {title}")
        cr_id = self.create_cr(title, change_type, asset_id, desired_outcome)
        self.generate_plan(cr_id)
        self.submit_for_approval(cr_id)
        self.approve(cr_id)
        self.execute_cr(cr_id)
        return self.wait_for_completion(cr_id, title)


def _delete_smoke_snapshots(client: NexplaneClient) -> None:
    """Delete any EBS snapshots tagged nexplane=smoke-test-* directly via AWS."""
    try:
        from app.database import AsyncSessionLocal
        from app.models.connector import Connector, ConnectorType
        from app.services.connector_service import _attach_credentials
        import asyncio, boto3

        async def _get_creds():
            async with AsyncSessionLocal() as db:
                from sqlalchemy import select
                result = await db.execute(select(Connector).where(Connector.connector_type == ConnectorType.aws))
                connector = result.scalars().first()
                if connector:
                    await _attach_credentials(connector, db)
                    return getattr(connector, 'credentials', {})
            return {}

        creds = asyncio.run(_get_creds())
        if not creds:
            return
        ec2 = boto3.client('ec2',
            aws_access_key_id=creds['access_key_id'],
            aws_secret_access_key=creds['secret_access_key'],
            region_name=creds.get('region', 'us-east-1'),
        )
        snapshots = ec2.describe_snapshots(Owners=['self'], Filters=[
            {'Name': 'tag:nexplane', 'Values': ['smoke-test-*', 'smoke-test-pre-stop', 'smoke-test-pre-terminate']},
        ]).get('Snapshots', [])
        for snap in snapshots:
            try:
                ec2.delete_snapshot(SnapshotId=snap['SnapshotId'])
                print(f"  Deleted snapshot {snap['SnapshotId']}")
            except Exception as e:
                print(f"  ⚠️  Could not delete snapshot {snap['SnapshotId']}: {e}")
    except Exception as e:
        print(f"  ⚠️  Snapshot cleanup skipped: {e}")


def cleanup(client: NexplaneClient) -> None:
    """Terminate any running nexplane-smoke-test instances, delete test key pairs, and delete EBS snapshots.
    Always runs — ensures nothing is left running or costing money after the test."""
    print("\n  Cleanup: terminating smoke-test instances...")
    resp = client.client.get(f"{client.base}/assets", params={"q": "nexplane-smoke-test"})
    if not resp.is_success:
        return
    for asset in resp.json():
        if asset["asset_type"] == "server" and "smoke-test" in asset.get("name", ""):
            instance_id = asset.get("asset_metadata", {}).get("instance_id")
            if instance_id:
                try:
                    cr_id = client.create_cr(
                        f"Cleanup: terminate {asset['name']}",
                        "ec2_terminate",
                        asset["id"],
                        {"instance_id": instance_id, "confirm_terminate": True, "rollback_strategy": "rollback_unavailable"},
                    )
                    client.generate_plan(cr_id)
                    client.submit_for_approval(cr_id)
                    client.approve(cr_id)
                    client.execute_cr(cr_id)
                    client.wait_for_completion(cr_id, f"Cleanup terminate {asset['name']}")
                except Exception as e:
                    print(f"  ⚠️  Cleanup failed for {asset['name']}: {e}")
        elif asset["asset_type"] == "key_pair" and "smoke-test" in asset.get("name", ""):
            key_name = asset.get("asset_metadata", {}).get("key_name", asset["name"])
            cloud_account_id = client.get_cloud_account_asset_id()
            try:
                cr_id = client.create_cr(
                    f"Cleanup: delete key pair {key_name}",
                    "key_pair_create",
                    cloud_account_id,
                    {"key_name": key_name},
                )
                # Use rollback (delete) by cancelling — or run delete directly
                client.client.post(f"{client.base}/change-requests/{cr_id}/cancel")
            except Exception as e:
                print(f"  ⚠️  Key pair cleanup failed for {key_name}: {e}")

    print("  Cleanup: deleting smoke-test EBS snapshots...")
    _delete_smoke_snapshots(client)


def main():
    parser = argparse.ArgumentParser(description="Nexplane AWS live smoke test")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Nexplane API base URL")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    print("=" * 60)
    print("Nexplane AWS Live Smoke Test")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated to Nexplane")

    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account asset: {cloud_account_id}")

    passed = False
    try:
        # Step 1: Create key pair
        print("\n[Step 1] Create key pair")
        client.run_cr(
            "Smoke test: create key pair",
            "key_pair_create",
            cloud_account_id,
            {"key_name": KEY_NAME},
        )
        key_asset = client.get_asset_by_name(KEY_NAME)
        if not key_asset:
            fail(f"Key pair asset '{KEY_NAME}' not found in inventory after creation")
        log(f"Key pair asset in inventory: {key_asset['id']}")

        # Step 2: Launch EC2 with SSM + key pair
        print("\n[Step 2] Launch EC2 instance")
        client.run_cr(
            "Smoke test: launch EC2",
            "ec2_launch",
            cloud_account_id,
            {
                "mode": "quick",
                "name": INSTANCE_NAME,
                "os": "amazon_linux",
                "iam_instance_profile": "NexplaneEC2TestProfile",
                "key_name": KEY_NAME,
                "rollback_strategy": "terminate_instance",
            },
        )
        time.sleep(10)
        instance_asset = client.get_asset_by_name(INSTANCE_NAME)
        if not instance_asset:
            fail(f"Instance asset '{INSTANCE_NAME}' not found in inventory after launch")
        instance_id = instance_asset.get("asset_metadata", {}).get("instance_id")
        if not instance_id:
            fail(f"instance_id missing from asset metadata")
        log(f"Instance in inventory: {instance_id}")

        # Give SSM agent time to register
        print("  Waiting 90s for SSM agent to register...")
        time.sleep(90)

        # Step 3: SSM connectivity check
        print("\n[Step 3] SSM connectivity check")
        client.run_cr(
            "Smoke test: SSM whoami",
            "ssm_command",
            instance_asset["id"],
            {
                "instance_id": instance_id,
                "document_name": "AWS-RunShellScript",
                "command": "whoami && hostname",
                "rollback_strategy": "rollback_unavailable",
            },
        )
        log("SSM command executed successfully")

        # Step 4: Stop instance
        print("\n[Step 4] Stop instance")
        client.run_cr(
            "Smoke test: stop instance",
            "ec2_stop",
            instance_asset["id"],
            {
                "instance_id": instance_id,
                "snapshot_tag": "smoke-test-pre-stop",
                "rollback_strategy": "start_instance",
            },
        )
        log("Instance stopped")

        # Step 5: Start instance
        print("\n[Step 5] Start instance")
        client.run_cr(
            "Smoke test: start instance",
            "ec2_start",
            instance_asset["id"],
            {
                "instance_id": instance_id,
                "rollback_strategy": "stop_instance",
            },
        )
        log("Instance started")

        # Step 6: Terminate instance
        print("\n[Step 6] Terminate instance")
        client.run_cr(
            "Smoke test: terminate instance",
            "ec2_terminate",
            instance_asset["id"],
            {
                "instance_id": instance_id,
                "snapshot_tag": "smoke-test-pre-terminate",
                "confirm_terminate": True,
                "rollback_strategy": "rollback_unavailable",
            },
        )
        log("Instance terminated")

        print("\n" + "=" * 60)
        print("✅ ALL SMOKE TESTS PASSED")
        print("=" * 60)
        passed = True

    except SystemExit:
        passed = False
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        passed = False
    finally:
        cleanup(client)
        if not passed:
            print("\n❌ SMOKE TEST FAILED")
            sys.exit(1)


if __name__ == "__main__":
    main()
