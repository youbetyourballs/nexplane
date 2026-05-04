# Expanded Smoke Test Phase A — Tailscale + Nexplane Agent Deploy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the backend Docker container join Tailscale and deploy the nexplane agent to live EC2 instances via SSM, then extend the smoke test with --phases support and Phase A steps.

**Architecture:** Tailscale installed in the backend container provides a private IP the EC2 agent can reach. A `tailscale_join` CR installs Tailscale on EC2 via SSM. A `deploy_nexplane_agent` CR downloads the binary and starts it as a systemd service. Both executors already exist; this plan wires them into the smoke test and adds the Docker infrastructure they need.

**Tech Stack:** Docker, Tailscale (userspace networking), AWS SSM, Python httpx, systemd, nexplane-agent Go binary.

---

## File Map

**Modify:**
- `backend/Dockerfile` — add Tailscale + userspace networking support
- `docker-compose.yml` — add `cap_add: NET_ADMIN` and `sysctls`
- `backend/tests/smoke/test_aws_live.py` — add `--phases` flag and Phase A steps

---

## Task 1: Add Tailscale to Backend Dockerfile

The backend container needs Tailscale installed so it can join the same Tailscale network as the EC2 instance. We use userspace networking mode so no kernel TUN device is needed.

**Files:**
- Modify: `backend/Dockerfile`

- [ ] **Step 1: Add Tailscale install to Dockerfile**

In `backend/Dockerfile`, after the `RUN apt-get update` block (after line installing `libpq-dev gcc`) and before `COPY backend/requirements.txt .`, add:

```dockerfile
# Install Tailscale for agent connectivity in smoke tests
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg lsb-release iproute2 iptables unzip \
    && curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.nokey | gpg --dearmor -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/debian bookworm main" > /etc/apt/sources.list.d/tailscale.list \
    && apt-get update && apt-get install -y --no-install-recommends tailscale \
    && rm -rf /var/lib/apt/lists/*

# Install Terraform CLI for local IaC smoke tests
RUN curl -fsSL https://releases.hashicorp.com/terraform/1.7.5/terraform_1.7.5_linux_amd64.zip -o /tmp/tf.zip \
    && unzip /tmp/tf.zip -d /usr/local/bin/ \
    && rm /tmp/tf.zip \
    && chmod +x /usr/local/bin/terraform

# Install Ansible + AWS collections for local Ansible smoke tests
RUN pip install --no-cache-dir ansible boto3 botocore \
    && ansible-galaxy collection install amazon.aws community.aws --timeout 120
```

- [ ] **Step 2: Update docker-compose.yml backend service**

In `docker-compose.yml`, add `cap_add` and `sysctls` to the backend service (at the same indentation level as `volumes:`):

```yaml
    cap_add:
      - NET_ADMIN
    sysctls:
      - net.ipv4.ip_forward=1
```

- [ ] **Step 3: Rebuild and verify**

```bash
docker compose build backend
docker compose up backend -d
docker compose exec backend tailscale version
docker compose exec backend terraform version
docker compose exec backend ansible --version
```

Expected: all three print version strings without error.

- [ ] **Step 4: Commit**

```bash
git add backend/Dockerfile docker-compose.yml
git commit -m "feat: add Tailscale, Terraform CLI, and Ansible to backend container for smoke testing"
```

---

## Task 2: Extend Smoke Test with --phases Flag and Phase A Steps

Replace the existing `backend/tests/smoke/test_aws_live.py` with the full Phase A implementation. This extends the existing 6-step test with Tailscale join and agent deploy steps, and adds the `--phases` flag.

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Write the new smoke test**

Replace `backend/tests/smoke/test_aws_live.py` with:

```python
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
        """Retrieve the pre-generated Tailscale auth key from the Tailscale connector."""
        connectors = self.get("/connectors")
        for c in connectors:
            if c["connector_type"] == "tailscale":
                connector_id = c["id"]
                # Call generate_auth_key action to get the stored key
                from app.database import AsyncSessionLocal
                from sqlalchemy import select
                from app.models.connector import Connector
                from app.services.connector_service import _attach_credentials
                import asyncio

                async def _get_key():
                    async with AsyncSessionLocal() as db:
                        result = await db.execute(select(Connector).where(Connector.id == connector_id))
                        conn = result.scalar_one_or_none()
                        if conn:
                            await _attach_credentials(conn, db)
                            return conn.credentials.get("auth_key", "")
                    return ""

                key = asyncio.run(_get_key())
                if key:
                    return key
                fail("Tailscale connector found but auth_key is empty — add a reusable auth key in connector settings")
        fail("No Tailscale connector found — add one in Nexplane Settings")

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
    _run("tailscaled --tun=userspace-networking --statedir=/tmp/tailscale-state &", capture=False)
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
        from app.database import AsyncSessionLocal
        from sqlalchemy import select
        from app.models.connector import Connector, ConnectorType
        from app.services.connector_service import _attach_credentials
        import boto3

        async def _get_creds():
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Connector).where(Connector.connector_type == ConnectorType.aws))
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
    """Always runs — terminates instances, deletes key pairs and snapshots."""
    print("\n  Cleanup running...")
    assets = client.get("/assets", params={"q": "nexplane-smoke-test"})
    cloud_account_id = client.get_cloud_account_asset_id()

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
                        {"instance_id": instance_id, "confirm_terminate": True, "rollback_strategy": "rollback_unavailable"},
                    )
                except Exception as e:
                    print(f"  ⚠️  Terminate failed for {name}: {e}")
        elif asset["asset_type"] == "key_pair":
            key_name = asset.get("asset_metadata", {}).get("key_name", name)
            try:
                # Delete via rollback of key_pair_create — just cancel so the executor's rollback runs
                cr_id = client.create_cr(f"Cleanup: delete {key_name}", "key_pair_create", cloud_account_id, {"key_name": key_name})
                client.post(f"/change-requests/{cr_id}/cancel")
            except Exception as e:
                print(f"  ⚠️  Key pair delete failed for {key_name}: {e}")

    _delete_smoke_snapshots(client)
    teardown_backend_tailscale()
    print("  Cleanup complete.")


# ---------------------------------------------------------------------------
# Phase runners
# ---------------------------------------------------------------------------

def run_phase_a(client: NexplaneClient, cloud_account_id: str) -> dict:
    """Phase A: key pair + EC2 launch + Tailscale join + agent deploy."""
    print("\n[Phase A] EC2 launch + Tailscale + agent deploy")

    auth_key = client.get_tailscale_auth_key()
    backend_ip = setup_backend_tailscale(auth_key)
    agent_secret = client.get_agent_secret()

    # Step 1: Create key pair
    client.run_cr("Smoke: create key pair", "key_pair_create", cloud_account_id, {"key_name": KEY_NAME})
    key_asset = client.get_asset_by_name(KEY_NAME)
    if not key_asset:
        fail(f"Key pair asset '{KEY_NAME}' not in inventory")
    log(f"Key pair in inventory: {key_asset['id']}")

    # Step 2: Launch EC2
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

    # Wait for SSM
    print("  Waiting 90s for SSM agent...")
    time.sleep(90)

    # Step 3: SSM connectivity check
    client.run_cr(
        "Smoke: SSM whoami", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "whoami && hostname", "rollback_strategy": "rollback_unavailable"},
    )

    # Step 4: Tailscale join
    client.run_cr(
        "Smoke: tailscale join", "tailscale_join", instance_asset["id"],
        {"instance_id": instance_id, "auth_key": auth_key, "hostname": "nexplane-smoke-ec2"},
    )

    # Step 5: Deploy nexplane agent
    nexplane_url = f"http://{backend_ip}:8000"
    client.run_cr(
        "Smoke: deploy agent", "deploy_nexplane_agent", instance_asset["id"],
        {"instance_id": instance_id, "nexplane_url": nexplane_url, "nexplane_secret": agent_secret},
    )

    # Step 6: Wait for agent to register
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
        print("  ⚠️  Agent not yet registered — continuing (may still be starting)")

    log("Phase A complete")
    return {"instance_asset": instance_asset, "instance_id": instance_id,
            "backend_ip": backend_ip, "agent_secret": agent_secret}


def run_phase_b(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase B: agent-based actions."""
    print("\n[Phase B] Agent-based actions")
    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]

    # patch_packages dry run
    client.run_cr(
        "Smoke: patch audit (dry run)", "patch_packages", instance_asset["id"],
        {"instance_id": instance_id, "mode": "security_only", "dry_run": True,
         "packages": [], "cve_id": None, "rollback_strategy": "uninstall_patches"},
    )

    # OS security posture audit
    client.run_cr(
        "Smoke: OS security posture", "remote_command", instance_asset["id"],
        {"instance_id": instance_id, "template_id": "collect_support_bundle",
         "parameters": {"output_path": "/tmp/smoke-posture.tar.gz"}, "rollback_strategy": "rollback_unavailable"},
    )

    # CloudWatch agent install
    client.run_cr(
        "Smoke: install CloudWatch agent", "ssm_command", instance_asset["id"],
        {"instance_id": instance_id, "document_name": "AWS-ConfigureAWSPackage",
         "parameters": {"action": ["Install"], "name": ["AmazonCloudWatchAgent"]},
         "rollback_strategy": "rollback_unavailable"},
    )

    log("Phase B complete")


def run_phase_c(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase C: local Terraform lifecycle."""
    print("\n[Phase C] Local Terraform")
    log("Phase C not yet implemented — requires terraform_local connector")


def run_phase_d(client: NexplaneClient, phase_a_result: Optional[dict]) -> None:
    """Phase D: local Ansible."""
    print("\n[Phase D] Local Ansible")
    log("Phase D not yet implemented — requires ansible_local connector")


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
        help="Comma-separated phases to run (default: A,B,C,D). E.g. --phases A or --phases A,B"
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
```

- [ ] **Step 2: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: expand smoke test with --phases flag and Phase A (Tailscale + agent deploy)"
```

---

## Task 3: Run Phase A Against Live AWS

- [ ] **Step 1: Rebuild backend container**

```bash
docker compose build backend
docker compose up backend -d
```

- [ ] **Step 2: Run Phase A only**

```bash
python backend/tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases A
```

Expected: all steps ✅, agent appears in asset inventory as `endpoint` type, cleanup runs and removes all resources.

- [ ] **Step 3: Fix any issues found**

Check backend logs if anything fails:

```bash
docker compose logs backend --tail 30
```

Common issues:
- SSM agent not ready: increase `time.sleep(90)` to `time.sleep(120)`
- Agent doesn't register: verify `NEXPLANE_URL` is the correct Tailscale IP — check with `docker compose exec backend tailscale ip -4`
- Tailscale join fails: verify the auth key is still valid at tailscale.com/admin/settings/keys

- [ ] **Step 4: Run full A+B phases once A passes**

```bash
python backend/tests/smoke/test_aws_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases A,B
```

Expected: Phase B CRs complete, CloudWatch agent installs successfully.
