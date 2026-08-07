# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

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
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {'✅' if ok else '❌'} {msg}")


def fail(msg: str) -> None:
    log(msg, ok=False)
    raise SystemExit(1)


# Budget signals from the backend's AI proxy layer
_BUDGET_SIGNALS = frozenset({
    "quota", "budget", "rate_limit", "insufficient_quota",
    "context_length_exceeded", "billing", "capacity",
})


def check_for_budget_pause(result: "dict | None") -> None:
    """
    Call after every MCP tool call result. If the result contains an AI
    budget-exhaustion signal, print a clear pause message and exit with
    code 2 (distinct from pytest failure exit code 1) so the operator
    knows to expand the API cap before resuming.
    """
    if not isinstance(result, dict):
        return
    # Check HTTP-level error propagated into result
    error_text = str(result.get("error", "")).lower()
    detail_text = str(result.get("detail", "")).lower()
    combined = error_text + " " + detail_text
    if any(sig in combined for sig in _BUDGET_SIGNALS):
        phase = result.get("phase", "unknown phase")
        api = "Claude" if "claude" in combined or "anthropic" in combined else \
              "OpenAI" if "openai" in combined else "AI"
        log(f"BUDGET_PAUSE: {api} API cap hit during {phase}. "
            f"Expand the budget cap then re-run from this phase.", ok=False)
        print(f"\n⚠️  AI API budget exhausted ({api}). "
              f"Passed phases are already committed. "
              f"Expand the cap and resume.\n")
        sys.exit(2)

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
        import httpcore as _hc
        for _attempt in range(3):
            try:
                resp = self.client.post(f"{self.base}{path}", **kwargs)
                if resp.status_code >= 400:
                    try:
                        body = resp.json()
                    except Exception:
                        body = resp.text
                    raise Exception(f"HTTP {resp.status_code} {path}: {body}")
                return resp.json()
            except _hc.RemoteProtocolError:
                if _attempt == 2:
                    raise
                import time as _t; _t.sleep(2)
        raise RuntimeError("unreachable")

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

    def delete(self, path: str, **kwargs) -> dict:
        resp = self.client.delete(f"{self.base}{path}", **kwargs)
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
        data = self.get("/settings/agent-secret")
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

    def register_asset_for_connector(self, name: str, connector_id: str, asset_type: str = "cloud_account") -> str:
        """Register a transient asset linked to a specific connector for use as a CR target."""
        asset = self.post("/assets", json={
            "name": name,
            "asset_type": asset_type,
            "environment": "dev",
            "criticality": "low",
            "connector_id": connector_id,
        })
        return asset["id"]

    def create_cr(self, title: str, change_type: str, asset_id: str, desired_outcome: dict,
                  connector_id: str | None = None) -> str:
        # Always include rollback_strategy so the safety engine passes on production-tagged assets
        outcome = {"rollback_strategy": "snapshot_restore", "_smoke_test": True, **desired_outcome}
        body = {
            "title": title,
            "description": f"Smoke test: {title}",
            "change_type": change_type,
            "target_asset_ids": [asset_id],
            "desired_outcome": outcome,
        }
        if connector_id:
            body["connector_id"] = connector_id
        return self.post("/change-requests", json=body)["id"]

    def run_cr(self, title: str, change_type: str, asset_id: str, desired_outcome: dict,
               connector_id: str | None = None, timeout: int = TIMEOUT_SECONDS) -> dict:
        print(f"  → {title}")
        cr_id = self.create_cr(title, change_type, asset_id, desired_outcome, connector_id=connector_id)
        self.post(f"/change-requests/{cr_id}/plan")
        self.post(f"/change-requests/{cr_id}/submit-for-approval")
        self.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke test"})
        self.post(f"/change-requests/{cr_id}/execute")
        return self._wait_timeout(cr_id, title, timeout)

    def _wait(self, cr_id: str, label: str) -> dict:
        return self._wait_timeout(cr_id, label, TIMEOUT_SECONDS)

    def _wait_timeout(self, cr_id: str, label: str, timeout: int) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            cr = self.get(f"/change-requests/{cr_id}")
            if cr["status"] == "completed":
                log(f"{label}")
                return cr
            if cr["status"] in ("failed", "rolled_back", "rejected"):
                fail(f"{label} — CR ended with status '{cr['status']}' (id: {cr_id})")
            time.sleep(5)
        fail(f"{label} — timed out after {timeout}s")

    def _wait_rollback(self, cr_id: str, label: str) -> None:
        deadline = time.time() + TIMEOUT_SECONDS
        while time.time() < deadline:
            cr = self.get(f"/change-requests/{cr_id}")
            if cr["status"] == "rolled_back":
                log(f"  rolled back: {label}")
                return
            if cr["status"] in ("failed", "completed", "rollback_failed", "rollback_partial"):
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
# Standalone CR helpers (used by setup_module / teardown_module in test files)
# ---------------------------------------------------------------------------

import time as _time_sh


def smoke_run_cr(client, change_type, params, asset_ids=None, timeout=360):
    """Execute a CR end-to-end (create→plan→approve→execute→poll) via REST.
    Returns the completed CR dict. Raises AssertionError on failure, TimeoutError on timeout."""
    body = {
        "title": f"[smoke-infra] {change_type}",
        "change_type": change_type,
        "desired_outcome": params,
    }
    if asset_ids:
        body["target_asset_ids"] = asset_ids
    cr = client.post("/change-requests", json=body)
    cr_id = cr["id"]
    client.post(f"/change-requests/{cr_id}/plan")
    deadline = _time_sh.time() + 120
    while _time_sh.time() < deadline:
        cr_state = client.get(f"/change-requests/{cr_id}")
        if cr_state.get("status") == "awaiting_approval":
            break
        if cr_state.get("status") in ("failed", "rejected"):
            raise AssertionError(f"Infra CR {cr_id} failed at planning: {cr_state}")
        _time_sh.sleep(5)
    client.post(f"/change-requests/{cr_id}/submit-for-approval")
    client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke infra"})
    client.post(f"/change-requests/{cr_id}/execute")
    deadline = _time_sh.time() + timeout
    while _time_sh.time() < deadline:
        cr_state = client.get(f"/change-requests/{cr_id}")
        status = cr_state.get("status", "")
        if status == "completed":
            return cr_state
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(f"Infra CR {cr_id} ({change_type}) failed: {cr_state}")
        _time_sh.sleep(10)
    raise TimeoutError(f"Infra CR {cr_id} ({change_type}) timed out after {timeout}s")


def smoke_rollback_cr(client, cr_id, timeout=300):
    """Roll back a completed CR. Swallows all errors (safe for teardown)."""
    try:
        client.post(f"/change-requests/{cr_id}/rollback")
        deadline = _time_sh.time() + timeout
        while _time_sh.time() < deadline:
            cr = client.get(f"/change-requests/{cr_id}")
            if cr.get("status") in ("rolled_back", "rollback_completed", "rollback_failed"):
                return
            _time_sh.sleep(10)
    except Exception as _e:
        print(f"  [teardown] rollback {cr_id} swallowed error: {_e}")

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
        # When running outside Docker (Python 3.9 on EC2 host), app.config uses 3.10+
        # union syntax — try fetching credentials via docker exec instead.
        import subprocess as _sp, json as _json
        try:
            _script = (
                "import asyncio,json,sys\n"
                "async def _m():\n"
                "    from app.database import AsyncSessionLocal\n"
                "    from app.services.connector_service import _attach_credentials\n"
                "    from app.models.connector import Connector,ConnectorType\n"
                "    from sqlalchemy import select\n"
                "    async with AsyncSessionLocal() as db:\n"
                "        r=await db.execute(select(Connector).where(Connector.connector_type==ConnectorType.aws))\n"
                "        conn=r.scalars().first()\n"
                "        if not conn: sys.exit(1)\n"
                "        await _attach_credentials(conn,db)\n"
                "        print(json.dumps(getattr(conn,'credentials',{})))\n"
                "asyncio.run(_m())\n"
            )
            _out = _sp.check_output(
                ["docker", "exec", "nexplane-backend-1", "python3", "-c", _script],
                timeout=15, stderr=_sp.DEVNULL,
            )
            _aws_creds_cache = _json.loads(_out.strip())
        except Exception:
            pass
    if not _aws_creds_cache:
        try:
            from app.config import settings
        except (ImportError, TypeError):
            return None
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
        try:
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
        except ModuleNotFoundError:
            # Running on runner EC2 without app/ on PYTHONPATH — fall back to env-var-injected creds.
            _gcp_creds_cache = get_connector_creds_from_db("gcp")
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


_gcp_container_creds_cache: dict = {}


def _get_gcp_container_client():
    global _gcp_container_creds_cache
    if not _gcp_container_creds_cache:
        creds = get_connector_creds_from_db("gcp")
        _gcp_container_creds_cache = creds or {}
    creds = _gcp_container_creds_cache
    if not creds:
        return None
    import json as _json
    from google.oauth2 import service_account
    from google.cloud import container_v1
    key_json_raw = creds.get("service_account_key_json", "")
    if isinstance(key_json_raw, str):
        key_json = _json.loads(key_json_raw)
    else:
        key_json = key_json_raw
    credentials = service_account.Credentials.from_service_account_info(
        key_json,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return container_v1.ClusterManagerClient(credentials=credentials)

# ---------------------------------------------------------------------------
# Generic connector credentials helper (reads from platform DB directly)
# ---------------------------------------------------------------------------

_connector_creds_cache: dict = {}


def get_connector_creds_from_db(connector_type_str: str) -> dict:
    """Return decrypted credentials dict for the first connector of connector_type_str.

    On the runner EC2 (where app/ is unavailable), credentials are injected as
    NEXPLANE_CREDS_{TYPE} env vars (base64-encoded JSON) by run_on_ec2.py.
    Falls back to direct DB access when running inside the platform container.
    Returns {} if no credentials are found.
    """
    import threading
    global _connector_creds_cache
    if connector_type_str in _connector_creds_cache:
        return _connector_creds_cache[connector_type_str]

    # Check env var first (runner EC2 path — app module not available)
    import base64 as _b64, json as _json_c, os as _os_c
    _env_key = f"NEXPLANE_CREDS_{connector_type_str.upper().replace('-', '_')}"
    _encoded = _os_c.environ.get(_env_key, "")
    if _encoded:
        try:
            creds = _json_c.loads(_b64.b64decode(_encoded).decode())
            _connector_creds_cache[connector_type_str] = creds
            return creds
        except Exception:
            pass

    # Not in container — try fetching credentials via docker exec
    import subprocess as _subprocess_c, json as _json_c2
    _not_in_container = not (_os_c.path.exists("/.dockerenv") or _os_c.path.exists("/app/app"))
    if _not_in_container:
        _ct_enum = connector_type_str.lower().replace("-", "_")
        _script = (
            "import asyncio,json,sys\n"
            "async def _m():\n"
            "    from app.database import AsyncSessionLocal\n"
            "    from app.services.connector_service import _attach_credentials\n"
            "    from app.models.connector import Connector\n"
            "    from sqlalchemy import select\n"
            "    async with AsyncSessionLocal() as db:\n"
            "        r=await db.execute(select(Connector).where(Connector.connector_type=='" + _ct_enum + "'))\n"
            "        conn=r.scalars().first()\n"
            "        if not conn: sys.exit(1)\n"
            "        await _attach_credentials(conn,db)\n"
            "        print(json.dumps(getattr(conn,'credentials',{})))\n"
            "asyncio.run(_m())\n"
        )
        try:
            _out = _subprocess_c.check_output(
                ["docker", "exec", "nexplane-backend-1", "python3", "-c", _script],
                timeout=15, stderr=_subprocess_c.DEVNULL,
            )
            creds = _json_c2.loads(_out.strip())
            _connector_creds_cache[connector_type_str] = creds
            return creds
        except Exception:
            pass

    from app.config import settings
    from app.models.connector import Connector
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
                    sa.select(Connector).where(
                        Connector.connector_type == connector_type_str
                    )
                )
                conn = result.scalars().first()
                if not conn:
                    return {}
                await _attach_credentials(conn, db)
                return getattr(conn, "credentials", {}) or {}
        finally:
            await engine.dispose()

    def _run_in_thread():
        result_holder[0] = asyncio.run(_get())

    t = threading.Thread(target=_run_in_thread)
    t.start()
    t.join()
    creds = result_holder[0] or {}
    _connector_creds_cache[connector_type_str] = creds
    return creds


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


def _get_oci_container_engine_client():
    """Return an OCI ContainerEngineClient using cached credentials."""
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
    return oci.container_engine.ContainerEngineClient(config)


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


def _check_smoke_ami_cache(ssm_client, ec2_client, cache_key: str, setup_hash: str):
    """Check SSM for a cached AMI matching setup_hash. Returns AMI ID string or None."""
    import json
    param_name = f"/nexplane/smoke-amis/{cache_key}/{setup_hash}"
    try:
        resp = ssm_client.get_parameter(Name=param_name)
        data = json.loads(resp["Parameter"]["Value"])
        ami_id = data.get("ami_id", "")
        if not ami_id:
            return None
        images = ec2_client.describe_images(ImageIds=[ami_id]).get("Images", [])
        if images and images[0].get("State") == "available":
            return ami_id
    except Exception:
        pass
    return None


def _wait_ssm_ready_win(ssm_client, instance_id: str, timeout: int = 600) -> None:
    """Poll until the Windows instance is reachable via SSM."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = ssm_client.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            if resp.get("InstanceInformationList"):
                return
        except Exception:
            pass
        time.sleep(10)
    raise TimeoutError(f"SSM not ready for {instance_id} after {timeout}s")


def get_or_create_smoke_ami(
    cache_key: str | None = None,
    setup_hash: str | None = None,
    launch_fn=None,
    snapshot_name: str | None = None,
    # Alternative interface used by service-mesh tests
    ssm_key: str | None = None,
    creds: dict | None = None,
    build_instructions: str | None = None,
) -> str:
    """Return a cached AMI ID.

    Two calling conventions:

    1. launch_fn interface (stateful/identity tests):
         get_or_create_smoke_ami(cache_key, setup_hash, launch_fn)
         On cache miss: provisions via launch_fn, snapshots, stores in SSM.

    2. ssm_key interface (service-mesh tests — AMIs must be pre-built):
         get_or_create_smoke_ami(ssm_key=..., creds=..., build_instructions=...)
         On cache miss: skips with build_instructions so a human can build the AMI.
    """
    import boto3
    import json
    import time as _t

    # --- ssm_key interface ---
    if ssm_key is not None:
        _creds = creds or get_connector_creds_from_db("aws")
        region = _creds.get("region", "us-east-1")
        _ec2 = boto3.client(
            "ec2",
            aws_access_key_id=_creds.get("access_key_id") or _creds.get("aws_access_key_id"),
            aws_secret_access_key=_creds.get("secret_access_key") or _creds.get("aws_secret_access_key"),
            region_name=region,
        )
        _ssm = boto3.client(
            "ssm",
            aws_access_key_id=_creds.get("access_key_id") or _creds.get("aws_access_key_id"),
            aws_secret_access_key=_creds.get("secret_access_key") or _creds.get("aws_secret_access_key"),
            region_name=region,
        )
        try:
            resp = _ssm.get_parameter(Name=ssm_key)
            raw = resp["Parameter"]["Value"]
            ami_id = json.loads(raw).get("ami_id") if raw.startswith("{") else raw
            if ami_id:
                images = _ec2.describe_images(ImageIds=[ami_id]).get("Images", [])
                if images and images[0].get("State") == "available":
                    return ami_id
        except Exception:
            pass
        hint = f"\n  Build instructions: {build_instructions}" if build_instructions else ""
        import pytest as _pytest
        _pytest.skip(f"No smoke AMI at {ssm_key} — build and cache it first.{hint}")

    # --- launch_fn interface ---
    aws_creds = get_connector_creds_from_db("aws")
    region = aws_creds.get("region", "us-east-1")
    _ec2 = boto3.client(
        "ec2",
        aws_access_key_id=aws_creds.get("access_key_id") or aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key") or aws_creds.get("aws_secret_access_key"),
        region_name=region,
    )
    _ssm = boto3.client(
        "ssm",
        aws_access_key_id=aws_creds.get("access_key_id") or aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key") or aws_creds.get("aws_secret_access_key"),
        region_name=region,
    )

    cached = _check_smoke_ami_cache(_ssm, _ec2, cache_key, setup_hash)
    if cached:
        return cached

    instance_id, ec2_client, _ = launch_fn(aws_creds)
    name = snapshot_name or f"nexplane-smoke-{cache_key.replace('/', '-')}-{setup_hash}"

    ec2_client.stop_instances(InstanceIds=[instance_id])
    ec2_client.get_waiter("instance_stopped").wait(InstanceIds=[instance_id])

    try:
        ami_resp = ec2_client.create_image(InstanceId=instance_id, Name=name, NoReboot=True)
        ami_id = ami_resp["ImageId"]
    except Exception as e:
        if "InvalidAMIName.Duplicate" in str(e) or "already in use" in str(e).lower():
            existing = ec2_client.describe_images(
                Filters=[{"Name": "name", "Values": [name]},
                         {"Name": "state", "Values": ["available", "pending"]}]
            ).get("Images", [])
            if existing:
                ami_id = existing[0]["ImageId"]
                log(f"  AMI name duplicate — reusing existing {ami_id}")
                ec2_client.terminate_instances(InstanceIds=[instance_id])
                param_name = f"/nexplane/smoke-amis/{cache_key}/{setup_hash}"
                _ssm.put_parameter(Name=param_name, Value=json.dumps({"ami_id": ami_id, "name": name}),
                                   Type="String", Overwrite=True)
                return ami_id
        raise

    deadline = _t.time() + 600
    while _t.time() < deadline:
        images = ec2_client.describe_images(ImageIds=[ami_id]).get("Images", [])
        if images and images[0].get("State") == "available":
            break
        _t.sleep(15)
    else:
        raise TimeoutError(f"AMI {ami_id} not available after 10 min")

    ec2_client.terminate_instances(InstanceIds=[instance_id])

    param_name = f"/nexplane/smoke-amis/{cache_key}/{setup_hash}"
    _ssm.put_parameter(
        Name=param_name,
        Value=json.dumps({"ami_id": ami_id, "name": name}),
        Type="String",
        Overwrite=True,
    )
    return ami_id


# ---------------------------------------------------------------------------
# Agent installation helper
# ---------------------------------------------------------------------------

_PLATFORM_PRIVATE_IP = "172.31.1.233"
_AGENT_BINARY_LOCAL  = "/app/bin/nexplane-agent-linux-amd64"
_AGENT_SECRET        = "sk-agent-2a9ea7a2367085c2399e4f7f41a2bb2c6fce6d3eaf4f1e7b"


def install_nexplane_agent_on_instance(
    instance_id: str,
    asset_id: str,
    aws_creds: dict,
    private_ip: str = "",
    timeout_s: int = 300,
) -> None:
    """
    Upload the nexplane agent binary to S3, use SSM to install and start it
    on the smoke instance, then wait for AgentRegistration to appear for the asset.
    """
    import boto3 as _boto3
    import uuid as _uuid
    import time as _time_mod
    import os as _os_mod

    region = aws_creds.get("region", "us-east-1")
    s3 = _boto3.client(
        "s3",
        aws_access_key_id=aws_creds.get("access_key_id") or aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key") or aws_creds.get("aws_secret_access_key"),
        region_name=region,
    )
    ssm = _boto3.client(
        "ssm",
        aws_access_key_id=aws_creds.get("access_key_id") or aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("secret_access_key") or aws_creds.get("aws_secret_access_key"),
        region_name=region,
    )

    # Find an S3 bucket we can write to
    bucket = aws_creds.get("smoke_s3_bucket") or aws_creds.get("s3_bucket")
    if not bucket:
        # Try to list buckets and pick the first nexplane one
        try:
            resp = s3.list_buckets()
            buckets = [b["Name"] for b in resp.get("Buckets", [])]
            for b in buckets:
                if "nexplane" in b.lower():
                    bucket = b
                    break
            if not bucket and buckets:
                bucket = buckets[0]
        except Exception:
            pass
    if not bucket:
        raise RuntimeError("No S3 bucket found for agent binary upload")

    # Upload agent binary
    s3_key = f"nexplane-agent/smoke/{_uuid.uuid4().hex}/nexplane-agent-linux-amd64"
    s3.upload_file(_AGENT_BINARY_LOCAL, bucket, s3_key)

    # Generate pre-signed URL valid for 30 minutes
    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": s3_key},
        ExpiresIn=1800,
    )

    platform_url = f"http://{_PLATFORM_PRIVATE_IP}:8000"
    install_script = f"""#!/bin/bash
set -e
curl -sf -o /usr/local/bin/nexplane-agent '{url}'
chmod +x /usr/local/bin/nexplane-agent
export NP_CONTROL_PLANE='{platform_url}'
export NP_SECRET='{_AGENT_SECRET}'
export NP_MODE='service'
nohup /usr/local/bin/nexplane-agent \
    -control-plane '{platform_url}' \
    -secret '{_AGENT_SECRET}' \
    -mode service \
    -poll-interval 5s \
    >> /var/log/nexplane-agent.log 2>&1 &
sleep 3
echo "Agent started"
"""

    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [install_script]},
        TimeoutSeconds=120,
    )
    cmd_id = resp["Command"]["CommandId"]

    # Wait for SSM command to complete
    deadline = _time_mod.time() + 120
    while _time_mod.time() < deadline:
        _time_mod.sleep(5)
        inv = ssm.get_command_invocation(CommandId=cmd_id, InstanceId=instance_id)
        status = inv["Status"]
        if status in ("Success", "Failed", "Cancelled"):
            if status != "Success":
                log(f"  SSM agent install status={status}: {inv.get('StandardErrorContent', '')}", ok=False)
            break

    # Poll for AgentRegistration by IP and re-point to smoke asset
    log(f"  Waiting for nexplane agent to register for {private_ip or asset_id} (up to {timeout_s}s)")
    _wait_for_agent_registration_by_ip(private_ip, asset_id, timeout_s)

    # Clean up S3
    try:
        s3.delete_object(Bucket=bucket, Key=s3_key)
    except Exception:
        pass


def _wait_for_agent_registration(asset_id: str, timeout_s: int = 300) -> None:
    """Block until AgentRegistration exists for asset_id or timeout."""
    import asyncio as _asyncio
    import time as _time_mod
    import uuid as _uuid
    import sys as _sys_mod

    _IN_CONTAINER = _os.path.exists("/.dockerenv") or _os.path.exists("/app/app")
    if not _IN_CONTAINER:
        raise RuntimeError("_wait_for_agent_registration must run inside the nexplane container")

    if "/app" not in _sys_mod.path:
        _sys_mod.path.insert(0, "/app")

    async def _poll():
        from app.database import AsyncSessionLocal
        from app.models.agent import AgentRegistration
        from sqlalchemy import select
        asset_uuid = _uuid.UUID(asset_id) if isinstance(asset_id, str) else asset_id
        deadline = _time_mod.monotonic() + timeout_s
        while _time_mod.monotonic() < deadline:
            async with AsyncSessionLocal() as db:
                r = await db.execute(
                    select(AgentRegistration).where(AgentRegistration.asset_id == asset_uuid)
                )
                reg = r.scalar_one_or_none()
                if reg:
                    log(f"  Agent registered for asset {asset_id} (id={reg.id})")
                    return
            _time_mod.sleep(10)
        raise TimeoutError(f"Agent did not register for asset {asset_id} within {timeout_s}s")

    _asyncio.run(_poll())


def _wait_for_agent_registration_by_ip(private_ip: str, asset_id: str, timeout_s: int = 300) -> None:
    """
    Poll until an AgentRegistration appears whose ip_addresses contains private_ip,
    then re-point it to the smoke asset_id (so dispatch_agent_job finds it).
    """
    import asyncio as _asyncio
    import time as _time_mod
    import uuid as _uuid
    import sys as _sys_mod

    if "/app" not in _sys_mod.path:
        _sys_mod.path.insert(0, "/app")

    async def _poll():
        from app.database import AsyncSessionLocal
        from app.models.agent import AgentRegistration
        from app.models.asset import Asset
        from sqlalchemy import select
        target_uuid = _uuid.UUID(asset_id) if isinstance(asset_id, str) else asset_id
        deadline = _time_mod.monotonic() + timeout_s
        while _time_mod.monotonic() < deadline:
            async with AsyncSessionLocal() as db:
                r = await db.execute(select(AgentRegistration))
                regs = r.scalars().all()
                for reg in regs:
                    ips = reg.ip_addresses or []
                    if private_ip and private_ip in ips:
                        # Re-point registration to smoke asset
                        old_asset_id = reg.asset_id
                        reg.asset_id = target_uuid
                        await db.commit()
                        # Delete auto-created duplicate asset if different
                        if old_asset_id and old_asset_id != target_uuid:
                            dup = await db.get(Asset, old_asset_id)
                            if dup:
                                await db.delete(dup)
                                await db.commit()
                        log(f"  Agent re-linked to smoke asset {asset_id} (was {old_asset_id})")
                        return
                    elif not private_ip and reg.asset_id == target_uuid:
                        log(f"  Agent registered for asset {asset_id}")
                        return
            _time_mod.sleep(10)
        raise TimeoutError(
            f"Agent did not register from IP {private_ip} within {timeout_s}s"
        )

    _asyncio.run(_poll())
