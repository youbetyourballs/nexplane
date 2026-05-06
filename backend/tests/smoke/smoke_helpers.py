#!/usr/bin/env python3
"""
Shared infrastructure for Nexplane multi-cloud smoke tests.

Imported by test_aws_live.py, test_gcp_live.py, test_azure_live.py, test_agent_live.py,
and test_cloud_live.py. Never run directly.
"""
import argparse
import json
import os as _os
import subprocess
import sys
import time
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KEY_NAME = "nexplane-smoke-test-key"
INSTANCE_NAME = "nexplane-smoke-test-01"
TIMEOUT_SECONDS = 600
RDS_PHASE_TIMEOUT_SECONDS = 2700  # 45 minutes for Phase J
GCE_SMOKE_INSTANCE = "nexplane-smoke-gce-01"
GCE_ZONE = "us-central1-a"
AZURE_SMOKE_VM = "nexplane-smoke-azure-01"

_IN_CONTAINER = _os.path.exists("/.dockerenv") or _os.path.exists("/app/app")
if _IN_CONTAINER and "/app" not in sys.path:
    sys.path.insert(0, "/app")

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def log(msg: str, ok: bool = True) -> None:
    print(f"{'✅' if ok else '❌'} {msg}")


def fail(msg: str) -> None:
    log(msg, ok=False)
    raise SystemExit(1)

# ---------------------------------------------------------------------------
# NexplaneClient
# ---------------------------------------------------------------------------

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
        return sorted(matches, key=lambda a: a.get("updated_at", ""), reverse=True)[0]

    def get_agent_secret(self) -> str:
        data = self.post("/settings/agent-secret")
        return data["agent_secret_plaintext"]

    def get_tailscale_auth_key(self, provided_key: str = "") -> str:
        if provided_key:
            return provided_key
        fail("Tailscale auth key required — pass --tailscale-auth-key <key>")
        return ""

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
        try:
            self.post(f"/change-requests/{cr_id}/rollback")
            self._wait_rollback(cr_id, label)
            return True
        except Exception as e:
            print(f"  ⚠️  Rollback request failed for {cr_id} ({label}): {e}")
            return False

    def _run_cr_with_timeout(self, title: str, change_type: str, asset_id: str,
                              desired_outcome: dict, timeout: int = TIMEOUT_SECONDS) -> dict:
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
# Tailscale helpers (AWS-specific but used by AWS phases)
# ---------------------------------------------------------------------------

def _run(cmd: str, capture: bool = True) -> str:
    if _IN_CONTAINER:
        full_cmd = ["sh", "-c", cmd]
    else:
        full_cmd = ["docker", "compose", "exec", "-T", "backend", "sh", "-c", cmd]
    result = subprocess.run(full_cmd, capture_output=capture, text=True)
    if result.returncode != 0 and capture:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return (result.stdout or "").strip()


def _verify_tailscale_reachable(ip: str) -> bool:
    try:
        result = _run(f"curl -fsSL --max-time 5 http://{ip}:8000/downloads/version 2>/dev/null || echo ''")
        return bool(result.strip())
    except Exception:
        return False


def setup_backend_tailscale(auth_key: str) -> str:
    print("  Setting up Tailscale in backend container...")
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

    _run(
        "for f in /proc/[0-9]*/cmdline; do "
        "  p=$(echo $f | grep -o '[0-9]*'); "
        "  cmd=$(cat $f 2>/dev/null | tr '\\0' ' '); "
        "  echo \"$cmd\" | grep -q tailscaled && kill $p 2>/dev/null; "
        "done; rm -f /var/run/tailscale/tailscaled.sock; true",
        capture=False,
    )
    time.sleep(2)
    _run("tailscaled --statedir=/tmp/tailscale-state >/tmp/tailscaled.log 2>&1 &", capture=False)
    time.sleep(5)
    _run(f"tailscale up --authkey={auth_key} --hostname=nexplane-backend --accept-routes --accept-dns=false")

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
# AWS cloud SDK helpers
# ---------------------------------------------------------------------------

_aws_creds_cache: dict = {}


def _get_aws_boto3_client(service: str):
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

# ---------------------------------------------------------------------------
# GCP cloud SDK helpers
# ---------------------------------------------------------------------------

_gcp_creds_cache: dict = {}


def _get_gcp_compute_client():
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

# ---------------------------------------------------------------------------
# Azure cloud SDK helpers
# ---------------------------------------------------------------------------

_azure_creds_cache: dict = {}


def _get_azure_compute_client():
    import threading
    global _azure_creds_cache
    if not _azure_creds_cache:
        from app.database import AsyncSessionLocal
        from app.models.connector import Connector, ConnectorType
        from app.services.connector_service import _attach_credentials
        import asyncio, sqlalchemy as sa
        result_holder: list = [None]
        async def _get():
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    sa.select(Connector).where(Connector.connector_type == ConnectorType.azure)
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
        _azure_creds_cache = result_holder[0] or {}
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.compute import ComputeManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return ComputeManagementClient(credential, creds['subscription_id'])


def _get_azure_storage_client():
    """Return an Azure StorageManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.storage import StorageManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return StorageManagementClient(credential, creds['subscription_id'])

# ---------------------------------------------------------------------------
# AWS-specific cleanup (called by test_aws_live.py cleanup())
# ---------------------------------------------------------------------------

def _delete_smoke_snapshots(client: NexplaneClient) -> None:
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

# ---------------------------------------------------------------------------
# Argument parser factory
# ---------------------------------------------------------------------------

def make_base_parser(description: str) -> argparse.ArgumentParser:
    """Return a parser pre-loaded with common arguments shared across all smoke test files."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    return parser
