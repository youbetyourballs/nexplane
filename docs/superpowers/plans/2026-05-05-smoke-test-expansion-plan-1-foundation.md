# Smoke Test Expansion — Plan 1: Foundation (smoke_helpers + file split)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract all shared infrastructure from `test_cloud_live.py` into `smoke_helpers.py`, split the existing 2,028-line file into per-provider files (`test_aws_live.py`, `test_gcp_live.py`, `test_azure_live.py`), and turn `test_cloud_live.py` into the cross-cloud orchestrator with backward-compat error messages.

**Architecture:** `smoke_helpers.py` exports `NexplaneClient`, constants, helpers, and cloud SDK client factories. Each provider file imports from it and contains its own phases + `main()`. `test_cloud_live.py` becomes thin: imports, cross-cloud phase stubs (X1/X2), and a `main()` that redirects old phase letters (A–O) to the correct provider file with a helpful error. All existing tests must still pass after the split.

**Tech Stack:** Python 3.12, boto3, google-cloud-compute, azure-mgmt-compute, azure-identity, httpx

---

## Files

**Create:**
- `backend/tests/smoke/smoke_helpers.py` — shared infrastructure
- `backend/tests/smoke/test_aws_live.py` — AWS phases A–K (migrated from test_cloud_live.py)
- `backend/tests/smoke/test_gcp_live.py` — GCP phases L–M (migrated)
- `backend/tests/smoke/test_azure_live.py` — Azure phases N–O (migrated)

**Modify:**
- `backend/tests/smoke/test_cloud_live.py` — gutted to cross-cloud stubs + redirect errors

---

### Task 1: Create `smoke_helpers.py`

**Files:**
- Create: `backend/tests/smoke/smoke_helpers.py`

Extract everything from `test_cloud_live.py` that is shared across provider files. This is lines 41–519 of the existing file (imports, constants, `log`, `fail`, `NexplaneClient`, Tailscale helpers, cleanup helpers, cloud SDK client factories).

- [ ] **Step 1: Create `smoke_helpers.py` with the complete content**

```python
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
```

- [ ] **Step 2: Verify the file parses**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/smoke_helpers.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/smoke_helpers.py
git commit -m "feat(smoke): extract smoke_helpers.py — shared infrastructure for all smoke test files"
```

---

### Task 2: Create `test_aws_live.py`

**Files:**
- Create: `backend/tests/smoke/test_aws_live.py`

This file contains all AWS-specific phases (A–K) migrated from `test_cloud_live.py`, plus a `cleanup()` function and its own `main()`. The content is identical to the corresponding sections in `test_cloud_live.py` but with the shared infrastructure replaced by imports from `smoke_helpers`.

- [ ] **Step 1: Create `test_aws_live.py`**

Create `backend/tests/smoke/test_aws_live.py` with this structure:

```python
#!/usr/bin/env python3
"""
Nexplane AWS Live Smoke Test — Phases A–K (and new P–T).

Usage:
    python backend/tests/smoke/test_aws_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases A,B,C,D,E,F,G,H,I,K \\
        --tailscale-auth-key tskey-auth-<key>

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

Requirements:
    AWS connector with credentials + NexplaneEC2TestProfile IAM role
    Tailscale connector with reusable pre-authorized auth key
"""
import secrets
import string
import time
from typing import Optional

from smoke_helpers import (
    KEY_NAME, INSTANCE_NAME, TIMEOUT_SECONDS, RDS_PHASE_TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _run, setup_backend_tailscale, teardown_backend_tailscale,
    _delete_smoke_snapshots, _get_aws_boto3_client, _aws_creds_cache,
    make_base_parser,
)
```

Then copy the body of every `run_phase_*` function (A through K + J) verbatim from `test_cloud_live.py` lines 524–1543.

Then copy the `cleanup()` function verbatim from `test_cloud_live.py` lines 467–519.

Then add a `main()` that mirrors the existing `main()` in `test_cloud_live.py` but only handles phases A–K:

```python
def main():
    parser = make_base_parser("Nexplane AWS live smoke test")
    parser.add_argument(
        "--phases", default="A,B,C,D",
        help="Comma-separated phases to run (A-K, J is slow ~35 min). E.g. --phases A or --phases A,B,C,D,E",
    )
    parser.add_argument("--tailscale-auth-key", default="", help="Reusable Tailscale auth key for Phase A")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    print("=" * 60)
    print(f"Nexplane AWS Live Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

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
                fail("Phase K requires Phase A to have run first")
            run_phase_k(client, phase_a_result)

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
            import sys as _sys
            _sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_aws_live.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Run Phase A via the new file to confirm it works**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases C \
  --tailscale-auth-key tskey-auth-REDACTED 2>&1 | tail -10
```

Expected: `✅ ALL SELECTED PHASES PASSED` (Phase C is Terraform, no EC2 needed, fast to verify)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): create test_aws_live.py — AWS phases A-K migrated from test_cloud_live.py"
```

---

### Task 3: Create `test_gcp_live.py`

**Files:**
- Create: `backend/tests/smoke/test_gcp_live.py`

Contains GCP phases L–M migrated from `test_cloud_live.py`, plus its own `main()`.

- [ ] **Step 1: Create `test_gcp_live.py`**

```python
#!/usr/bin/env python3
"""
Nexplane GCP Live Smoke Test — Phases L–M.

Usage:
    python backend/tests/smoke/test_gcp_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases L,M \\
        --tailscale-auth-key tskey-auth-<key> \\
        --gcp-project my-project-id

Phase descriptions:
    L  GCE: instance launch + agent deploy with rollback stack
    M  GCE advanced: stop/start/reboot/snapshot with rollback stack

Requirements:
    GCP connector with credentials + Compute Engine API enabled
    Tailscale connector with reusable pre-authorized auth key (for agent registration)
"""
import time
from typing import Optional

from smoke_helpers import (
    GCE_SMOKE_INSTANCE, GCE_ZONE, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _gcp_creds_cache, _get_gcp_compute_client,
    make_base_parser,
)
```

Then copy `run_phase_l()` and `run_phase_m()` verbatim from `test_cloud_live.py` lines 1554–1903.

Then add:

```python
def main():
    parser = make_base_parser("Nexplane GCP live smoke test")
    parser.add_argument(
        "--phases", default="L,M",
        help="Comma-separated phases to run (L-M). E.g. --phases L or --phases L,M",
    )
    parser.add_argument("--tailscale-auth-key", default="", help="Reusable Tailscale auth key")
    parser.add_argument("--gcp-project", default="", help="GCP project ID (required for GCP phases)")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    print("=" * 60)
    print(f"Nexplane GCP Live Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account: {cloud_account_id}")

    passed = False
    gcp_phase_result: Optional[dict] = None

    try:
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
        if not passed:
            print("\n❌ SMOKE TEST FAILED")
            import sys as _sys
            _sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_gcp_live.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_gcp_live.py
git commit -m "feat(smoke): create test_gcp_live.py — GCP phases L-M migrated from test_cloud_live.py"
```

---

### Task 4: Create `test_azure_live.py`

**Files:**
- Create: `backend/tests/smoke/test_azure_live.py`

Contains Azure phases N–O migrated from `test_cloud_live.py`, plus its own `main()`.

- [ ] **Step 1: Create `test_azure_live.py`**

```python
#!/usr/bin/env python3
"""
Nexplane Azure Live Smoke Test — Phases N–O.

Usage:
    python backend/tests/smoke/test_azure_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases N,O \\
        --tailscale-auth-key tskey-auth-<key> \\
        --azure-resource-group nexplane-smoke-rg

Phase descriptions:
    N  Azure: VM launch + agent deploy with rollback stack
    O  Azure advanced: stop/start/reboot/snapshot with rollback stack

Requirements:
    Azure connector with credentials + Contributor role on subscription
    Pre-existing resource group passed via --azure-resource-group
"""
import time
from typing import Optional

from smoke_helpers import (
    AZURE_SMOKE_VM, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _azure_creds_cache, _get_azure_compute_client,
    make_base_parser,
)
```

Then copy `run_phase_n()` and `run_phase_o()` verbatim from `test_cloud_live.py` lines 1741–1906.

Then add:

```python
def main():
    parser = make_base_parser("Nexplane Azure live smoke test")
    parser.add_argument(
        "--phases", default="N,O",
        help="Comma-separated phases to run (N-O). E.g. --phases N or --phases N,O",
    )
    parser.add_argument("--tailscale-auth-key", default="", help="Reusable Tailscale auth key")
    parser.add_argument("--azure-resource-group", default="", help="Azure resource group (must exist)")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    print("=" * 60)
    print(f"Nexplane Azure Live Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")

    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Cloud account: {cloud_account_id}")

    passed = False
    azure_phase_result: Optional[dict] = None

    try:
        if "N" in phases:
            agent_secret = client.get_agent_secret()
            azure_phase_result = run_phase_n(client, cloud_account_id, args.azure_resource_group, agent_secret)
        if "O" in phases:
            if azure_phase_result is None or azure_phase_result.get("vm_asset") is None:
                fail("Phase O requires Phase N to have run first")
            run_phase_o(client, azure_phase_result, args.azure_resource_group)

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
        if not passed:
            print("\n❌ SMOKE TEST FAILED")
            import sys as _sys
            _sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_azure_live.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_azure_live.py
git commit -m "feat(smoke): create test_azure_live.py — Azure phases N-O migrated from test_cloud_live.py"
```

---

### Task 5: Refactor `test_cloud_live.py` to cross-cloud orchestrator

**Files:**
- Modify: `backend/tests/smoke/test_cloud_live.py`

Replace the entire 2,028-line file with a thin cross-cloud orchestrator. All existing phases (A–O) are now in the provider files. This file:
1. Imports from `smoke_helpers`
2. Contains stub functions for future cross-cloud phases X1 and X2
3. Has a `main()` that either runs X1/X2 or prints a helpful redirect error for old phase letters

- [ ] **Step 1: Replace `test_cloud_live.py` entirely**

```python
#!/usr/bin/env python3
"""
Nexplane Multi-Cloud Consolidation Smoke Test — Phases X1–X2.

Cross-cloud phases that verify the same operation works correctly across
all three providers (AWS, GCP, Azure) in a single run. Built after
Azure Sub-projects B–G are complete.

For provider-specific phases, use the dedicated test files:
    AWS phases A-T  →  python backend/tests/smoke/test_aws_live.py
    GCP phases L-V  →  python backend/tests/smoke/test_gcp_live.py
    Azure phases N-Z → python backend/tests/smoke/test_azure_live.py
    Agent phases    →  python backend/tests/smoke/test_agent_live.py

Usage:
    python backend/tests/smoke/test_cloud_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases X1,X2 \\
        --tailscale-auth-key tskey-auth-<key> \\
        --gcp-project my-project-id \\
        --azure-resource-group nexplane-smoke-rg

Requirements:
    All three cloud connectors configured with live credentials.
    Azure Sub-projects B–G complete (blocked until then).
"""
import sys
from typing import Optional

from smoke_helpers import NexplaneClient, log, fail, make_base_parser

# Old phase letters that have moved to provider-specific files
_PROVIDER_REDIRECT = {
    **{p: "test_aws_live.py" for p in "ABCDEFGHIJK"},
    **{p: "test_gcp_live.py" for p in "LM"},
    **{p: "test_azure_live.py" for p in "NO"},
}


# ---------------------------------------------------------------------------
# Phase X1 — Cross-cloud instance lifecycle (STUB — blocked until Azure B-G)
# ---------------------------------------------------------------------------

def run_phase_x1(client: NexplaneClient, cloud_account_id: str,
                 gcp_project: str, azure_resource_group: str,
                 tailscale_auth_key: str) -> None:
    """Phase X1: Launch on AWS+GCP+Azure, verify inventory, stop all, verify state."""
    print("\n[Phase X1] Cross-Cloud Instance Lifecycle — STUB")
    print("  ⚠️  Phase X1 is not yet implemented.")
    print("  Implement after Azure Sub-projects B–G are complete.")
    print("  This phase launches a VM on AWS, GCP, and Azure simultaneously,")
    print("  verifies all three appear in inventory with correct connector_type,")
    print("  stops all three, verifies power state via each cloud's SDK, then cleans up.")


# ---------------------------------------------------------------------------
# Phase X2 — Cross-cloud snapshot (STUB — blocked until Azure B-G)
# ---------------------------------------------------------------------------

def run_phase_x2(client: NexplaneClient, cloud_account_id: str,
                 gcp_project: str, azure_resource_group: str) -> None:
    """Phase X2: Snapshot on AWS+GCP+Azure, verify, delete."""
    print("\n[Phase X2] Cross-Cloud Snapshot — STUB")
    print("  ⚠️  Phase X2 is not yet implemented.")
    print("  Requires Phase X1 instances to be running.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = make_base_parser("Nexplane multi-cloud consolidation smoke test")
    parser.add_argument(
        "--phases", default="X1,X2",
        help="Cross-cloud phases to run (X1, X2). Old phases A-O have moved to provider files.",
    )
    parser.add_argument("--tailscale-auth-key", default="")
    parser.add_argument("--gcp-project", default="")
    parser.add_argument("--azure-resource-group", default="")
    args = parser.parse_args()
    phases = {p.strip().upper() for p in args.phases.split(",")}

    # Check for old phase letters and redirect
    old_phases = [p for p in phases if p in _PROVIDER_REDIRECT]
    if old_phases:
        print("\n❌ These phases have moved to provider-specific files:\n")
        for p in sorted(old_phases):
            dest = _PROVIDER_REDIRECT[p]
            print(f"  Phase {p}  →  python backend/tests/smoke/{dest} --phases {p}")
        print("\nExample:")
        print("  AWS phases A-K:  python backend/tests/smoke/test_aws_live.py --phases A,B,C,D")
        print("  GCP phases L-M:  python backend/tests/smoke/test_gcp_live.py --phases L,M")
        print("  Azure phases N-O: python backend/tests/smoke/test_azure_live.py --phases N,O")
        print("  Agent all clouds: python backend/tests/smoke/test_agent_live.py")
        sys.exit(1)

    print("=" * 60)
    print(f"Nexplane Multi-Cloud Smoke Test — phases: {', '.join(sorted(phases))}")
    print("=" * 60)

    client = NexplaneClient(args.base_url, args.email, args.password)
    log("Authenticated")
    cloud_account_id = client.get_cloud_account_asset_id()

    passed = False
    try:
        if "X1" in phases:
            run_phase_x1(client, cloud_account_id, args.gcp_project,
                         args.azure_resource_group, args.tailscale_auth_key)
        if "X2" in phases:
            run_phase_x2(client, cloud_account_id, args.gcp_project, args.azure_resource_group)

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
        if not passed:
            print("\n❌ SMOKE TEST FAILED")
            sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify syntax**

```bash
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_cloud_live.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 3: Verify old phase redirect works**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_cloud_live.py \
  --base-url http://localhost:8000 --email admin@acme.example --password admin123 \
  --phases A 2>&1 | head -10
```

Expected: output contains `These phases have moved to provider-specific files` and `test_aws_live.py --phases A`

- [ ] **Step 4: Verify test_aws_live.py still works end-to-end**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 --email admin@acme.example --password admin123 \
  --phases C 2>&1 | tail -5
```

Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 5: Run full backend test suite to confirm no regressions**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: `39 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/tests/smoke/test_cloud_live.py
git commit -m "feat(smoke): refactor test_cloud_live.py to cross-cloud orchestrator with redirect errors for old phases"
```

---

### Task 6: Final verification + README update placeholder

- [ ] **Step 1: Verify all three provider files independently runnable**

```bash
# GCP Phase L (skip if no GCP credentials handy — just syntax check)
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_gcp_live.py').read()); print('GCP OK')"

# Azure (just syntax check)
docker exec nexplane-backend-1 python -c "import ast; ast.parse(open('tests/smoke/test_azure_live.py').read()); print('Azure OK')"

# AWS full run Phase C (Terraform, fast)
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 --email admin@acme.example --password admin123 \
  --phases C 2>&1 | tail -5
```

Expected: `GCP OK`, `Azure OK`, `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 2: Commit final state**

```bash
git add -A
git commit -m "feat(smoke): Plan 1 complete — smoke_helpers.py + per-provider split ready for Plans 2-6"
```

---

**Plan 1 complete.** Plans 2–6 build on this foundation:
- **Plan 2:** `test_aws_live.py` new phases P–T + `rds_replica_create` executor
- **Plan 3:** `test_gcp_live.py` new phases N–R + sub-project stubs
- **Plan 4:** `test_azure_live.py` new phases P–T + sub-project stubs
- **Plan 5:** `test_agent_live.py` — Linux × 3 clouds
- **Plan 6:** `test_agent_live.py` — Windows × 3 clouds + README
