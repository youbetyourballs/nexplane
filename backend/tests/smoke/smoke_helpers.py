#!/usr/bin/env python3
from __future__ import annotations  # Python 3.9 compat
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
        self.client = httpx.Client(timeout=300)  # CR execution can take up to 5 min
        self.standalone = not (email and password)
        if not self.standalone:
            try:
                resp = self.client.post(f"{self.base}/auth/login", json={"email": email, "password": password})
                resp.raise_for_status()
                self.client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
            except Exception as _login_e:
                # Backend unreachable or auth failed — fall back to standalone mode so that
                # phases that call executors directly (OPENVAS_SCAN, NESSUS_SCAN, etc.) still run.
                print(f"  WARNING: Backend login failed ({_login_e}) — switching to standalone mode")
                self.standalone = True

    def get(self, path: str, **kwargs) -> dict:
        resp = self.client.get(f"{self.base}{path}", **kwargs)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def get_cr_step_result(cr: dict, step_number: int = 1) -> dict:
        """Extract the result dict for a specific step from a completed CR execution run."""
        for run in cr.get("execution_runs", []):
            if "rollback" in run.get("workflow_id", ""):
                continue
            steps = (run.get("result") or {}).get("execution", {}).get("steps", [])
            for step in steps:
                if step.get("step_number") == step_number:
                    return step.get("result") or {}
        return {}

    def post(self, path: str, **kwargs) -> dict:
        resp = self.client.post(f"{self.base}{path}", **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise Exception(f"HTTP {resp.status_code} {path}: {body}")
        return resp.json()

    def put(self, path: str, **kwargs) -> dict:
        resp = self.client.put(f"{self.base}{path}", **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise Exception(f"HTTP {resp.status_code} {path}: {body}")
        try:
            return resp.json()
        except Exception:
            return {}

    def get_cloud_account_asset_id(self) -> str:
        assets = self.get("/assets", params={"asset_type": "cloud_account"})
        if not assets:
            fail("No cloud_account asset found — run EC2 discovery on the AWS connector first")
        # Prefer connector-linked (live) assets over unlinked demo/seed assets
        linked = [a for a in assets if a.get("connector_id")]
        return (linked[0] if linked else assets[0])["id"]

    def get_connector_cloud_account_id(self, connector_type: str) -> str:
        """Return the cloud_account asset ID for the given connector type (aws, gcp, azure)."""
        try:
            connectors = self.get("/connectors", params={"connector_type": connector_type})
        except Exception:
            connectors = self.get("/connectors")
        if not connectors:
            fail(f"No {connector_type} connector found")
        # Find connector of the right type
        matching = [c for c in connectors if c.get("connector_type") == connector_type]
        if not matching:
            fail(f"No {connector_type} connector found")
        connector_id = matching[0]["id"]
        # Find cloud_account asset linked to this connector
        assets = self.get("/assets", params={"asset_type": "cloud_account"})
        for asset in assets:
            if asset.get("connector_id") == connector_id:
                return asset["id"]
        fail(f"No cloud_account asset found for {connector_type} connector (connector_id={connector_id})")
        return ""  # unreachable

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
        # Smoke tests run inside the backend container with full DB access.
        # Retrieve the auth_key directly from the encrypted ConnectorCredential
        # the same way the executor service does via _attach_credentials().
        try:
            import asyncio as _asyncio
            import threading as _threading
            from app.models.connector import Connector as _Connector
            from app.models.connector_credential import ConnectorCredential as _CC
            from app.services.secrets_service import SecretsService as _Secrets
            from app.config import settings as _cfg
            from sqlalchemy import select as _select
            from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession as _AsyncSession
            from sqlalchemy.orm import sessionmaker as _sessionmaker

            async def _fetch() -> str:
                engine = create_async_engine(_cfg.DATABASE_URL, pool_pre_ping=False)
                _sess = _sessionmaker(engine, class_=_AsyncSession, expire_on_commit=False)
                try:
                    async with _sess() as db:
                        row = await db.execute(
                            _select(_Connector).where(_Connector.connector_type == "tailscale")
                        )
                        conn = row.scalar_one_or_none()
                        if not conn:
                            return ""
                        cred_row = await db.execute(
                            _select(_CC).where(_CC.connector_id == conn.id)
                        )
                        cred = cred_row.scalar_one_or_none()
                        if not cred:
                            return ""
                        svc = _Secrets(_cfg.SECRET_KEY)
                        return svc.decrypt_json(cred.credentials_encrypted).get("auth_key", "")
                finally:
                    await engine.dispose()

            # Run in a separate thread to avoid event-loop conflicts in the
            # pytest async environment (same pattern as _get_aws_boto3_client).
            _result: list = [None]
            def _run():
                _result[0] = _asyncio.run(_fetch())
            t = _threading.Thread(target=_run)
            t.start()
            t.join()
            key = _result[0] or ""
            if key:
                log("Tailscale auth key retrieved from connector credentials")
                return key
        except Exception as exc:
            log(f"Could not retrieve Tailscale key from connector: {exc}")
        fail("Tailscale auth key not found — store credentials in the Tailscale connector or pass --tailscale-auth-key <key>")
        return ""

    def create_cr(self, title: str, change_type: str, asset_id: str, desired_outcome: dict) -> str:
        # Always include rollback_strategy so the safety engine passes on production-tagged assets
        outcome = {"rollback_strategy": "snapshot_restore", "_smoke_test": True, **desired_outcome}
        return self.post("/change-requests", json={
            "title": title,
            "description": f"Smoke test: {title}",
            "change_type": change_type,
            "target_asset_ids": [asset_id],
            "desired_outcome": outcome,
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
    _run("tailscaled --statedir=/var/lib/tailscale-state >/tmp/tailscaled.log 2>&1 &", capture=False)
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
        # When running on an EC2 runner, credentials come from environment variables
        # (set by run_on_ec2.py) to avoid importing app.database which requires asyncpg.
        _env_key = _os.environ.get("AWS_ACCESS_KEY_ID")
        _env_secret = _os.environ.get("AWS_SECRET_ACCESS_KEY")
        if _env_key and _env_secret:
            _aws_creds_cache = {
                "access_key_id": _env_key,
                "secret_access_key": _env_secret,
                "region": _os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
                "session_token": _os.environ.get("AWS_SESSION_TOKEN"),
            }
    if not _aws_creds_cache:
        from app.config import settings
        from app.models.connector import Connector, ConnectorType
        from app.services.connector_service import _attach_credentials
        import asyncio, sqlalchemy as sa
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        result_holder: list = [None]
        async def _get():
            # Create a fresh engine bound to this thread's event loop to avoid
            # "Future attached to a different loop" errors from AsyncSessionLocal.
            engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=False)
            async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with async_session() as db:
                    result = await db.execute(
                        sa.select(Connector).where(Connector.connector_type == ConnectorType.aws)
                    )
                    conn = result.scalars().first()
                    if not conn:
                        return None
                    await _attach_credentials(conn, db)
                    return getattr(conn, 'credentials', {})
            finally:
                await engine.dispose()
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
        from app.config import settings
        from app.models.connector import Connector, ConnectorType
        from app.services.connector_service import _attach_credentials
        import asyncio, sqlalchemy as sa
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        result_holder: list = [None]
        async def _get():
            engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=False)
            async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with async_session() as db:
                    result = await db.execute(
                        sa.select(Connector).where(Connector.connector_type == ConnectorType.gcp)
                    )
                    conn = result.scalars().first()
                    if not conn:
                        return None
                    await _attach_credentials(conn, db)
                    return getattr(conn, 'credentials', {})
            finally:
                await engine.dispose()
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
        from app.config import settings
        from app.models.connector import Connector, ConnectorType
        from app.services.connector_service import _attach_credentials
        import asyncio, sqlalchemy as sa
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        result_holder: list = [None]
        async def _get():
            engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=False)
            async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            try:
                async with async_session() as db:
                    result = await db.execute(
                        sa.select(Connector).where(Connector.connector_type == ConnectorType.azure)
                    )
                    conn = result.scalars().first()
                    if not conn:
                        return None
                    await _attach_credentials(conn, db)
                    return getattr(conn, 'credentials', {})
            finally:
                await engine.dispose()
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


def _get_azure_msi_client():
    """Return an Azure ManagedServiceIdentityClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.msi import ManagedServiceIdentityClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return ManagedServiceIdentityClient(credential, creds['subscription_id'])


def _get_azure_authorization_client():
    """Return an Azure AuthorizationManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.authorization import AuthorizationManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return AuthorizationManagementClient(credential, creds['subscription_id'])


def _get_azure_network_client():
    """Return an Azure NetworkManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.network import NetworkManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return NetworkManagementClient(credential, creds['subscription_id'])


def _get_azure_dns_client():
    """Return an Azure DnsManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.dns import DnsManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return DnsManagementClient(credential, creds['subscription_id'])


def _get_azure_sql_client():
    """Return an Azure SqlManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.sql import SqlManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return SqlManagementClient(credential, creds['subscription_id'])


def _get_azure_monitor_client():
    """Return an Azure MonitorManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.monitor import MonitorManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return MonitorManagementClient(credential, creds['subscription_id'])

# ---------------------------------------------------------------------------
# OCI cloud SDK helpers
# ---------------------------------------------------------------------------

_oci_creds_cache: dict = {}

OCI_CONNECTOR_ID = "0b3cf029-5ca0-4794-b982-6f494aaca372"


def _get_oci_creds(client=None) -> dict:
    """Return OCI credentials dict from the DB (same pattern as AWS/GCP/Azure helpers)."""
    import threading
    global _oci_creds_cache
    if _oci_creds_cache:
        return _oci_creds_cache
    from app.config import settings
    from app.services.connector_service import _attach_credentials
    import asyncio, sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    import uuid as _uuid
    result_holder: list = [None]

    async def _get():
        engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=False)
        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with async_session() as db:
                from app.models.connector import Connector
                result = await db.execute(
                    sa.select(Connector).where(
                        Connector.id == _uuid.UUID(OCI_CONNECTOR_ID)
                    )
                )
                conn = result.scalars().first()
                if not conn:
                    return None
                await _attach_credentials(conn, db)
                return getattr(conn, 'credentials', {})
        finally:
            await engine.dispose()

    def _run_in_thread():
        result_holder[0] = asyncio.run(_get())

    t = threading.Thread(target=_run_in_thread)
    t.start()
    t.join()
    _oci_creds_cache = result_holder[0] or {}
    return _oci_creds_cache


def _get_oci_compute_client():
    """Return an OCI ComputeClient using cached credentials."""
    creds = _get_oci_creds()
    if not creds:
        return None
    import oci
    config = {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds.get("region", "us-ashburn-1"),
    }
    return oci.core.ComputeClient(config)


def _get_oci_network_client():
    """Return an OCI VirtualNetworkClient using cached credentials."""
    creds = _get_oci_creds()
    if not creds:
        return None
    import oci
    config = {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds.get("region", "us-ashburn-1"),
    }
    return oci.core.VirtualNetworkClient(config)


def _get_oci_blockstorage_client():
    """Return an OCI BlockstorageClient using cached credentials."""
    creds = _get_oci_creds()
    if not creds:
        return None
    import oci
    config = {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds.get("region", "us-ashburn-1"),
    }
    return oci.core.BlockstorageClient(config)


def _get_oci_identity_client():
    """Return an OCI IdentityClient using cached credentials."""
    creds = _get_oci_creds()
    if not creds:
        return None
    import oci
    config = {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds.get("region", "us-ashburn-1"),
    }
    return oci.identity.IdentityClient(config)


def _get_oci_object_storage_client():
    """Return an OCI ObjectStorageClient using cached credentials."""
    creds = _get_oci_creds()
    if not creds:
        return None
    import oci
    config = {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds.get("region", "us-ashburn-1"),
    }
    return oci.object_storage.ObjectStorageClient(config)


def _get_oci_lb_client():
    """Return an OCI LoadBalancerClient using cached credentials."""
    creds = _get_oci_creds()
    if not creds:
        return None
    import oci
    config = {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds.get("region", "us-ashburn-1"),
    }
    return oci.load_balancer.LoadBalancerClient(config)


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
    parser.add_argument("--email", default="admin@acme.example",
                        help="Nexplane user email (default: admin@acme.example)")
    parser.add_argument("--password", default="admin123",
                        help="Nexplane user password (default: admin123)")
    parser.add_argument(
        "--backend-tailscale-ip", default="",
        help="Pre-known backend Tailscale IP (set by run_on_ec2.py). "
             "When provided, Phase A skips setup_backend_tailscale() since the "
             "backend is already joined to Tailscale by the EC2 runner script."
    )
    return parser
