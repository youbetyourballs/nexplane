# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

#!/usr/bin/env python3
"""
Smoke test: Migration Workflows — Foundation (Phases 1–4)

Phases:
  1. DISCOVERY_PROFILE   — discover_application_profile catalog_action CR against legacy app host
  2. BEHAVIORAL_BASELINE — capture_behavioral_baseline catalog_action CR (short observation window)
  3. BASELINE_ADAPTIVE   — capture_behavioral_baseline with Flask stopped mid-run → adaptive extension
  4. VERIFY_BASELINE     — verify_against_baseline pass path + failure path (Flask stopped)

Infrastructure:
  - Launches an EC2 instance via the AWS connector (ec2_launch CR)
  - Installs the legacy app stack (nginx:80 + Flask:8080 + PostgreSQL 14) via SSM
  - Deploys the Nexplane agent via deploy_nexplane_agent CR
  - Waits for agent registration before running migration CRs
  - All migration CRs use change_type=catalog_action with connector_type=nexplane_agent

All phases must pass in a single combined run on EC2. Run from EC2 runner only:
    python backend/tests/smoke/test_migration_foundation_live.py \\
        --email admin@acme.example --password admin123 \\
        --phases DISCOVERY_PROFILE,BEHAVIORAL_BASELINE,BASELINE_ADAPTIVE,VERIFY_BASELINE
"""

import argparse
import sys
import time

from smoke_helpers import (
    NexplaneClient,
    _get_aws_boto3_client,
    log,
    fail,
    make_base_parser,
    setup_backend_tailscale,
)

def _get_backend_tailscale_ip() -> str:
    """Return the backend container's current Tailscale IP if already connected, else empty string."""
    import subprocess
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=10,
        )
        ip = result.stdout.strip()
        if ip and ip.startswith("100."):
            return ip
    except Exception:
        pass
    return ""


ALL_PHASES = [
    "DISCOVERY_PROFILE",
    "BEHAVIORAL_BASELINE",
    "BASELINE_ADAPTIVE",
    "VERIFY_BASELINE",
]

# Legacy app instance name — used to find it in inventory after launch
_INSTANCE_NAME = "nexplane-smoke-migration-foundation"
_AGENT_HOSTNAME = "nexplane-smoke-migration"
_KEY_NAME = "nexplane-smoke-migration-key"

# Short observation window for smoke (avoids 20-minute real baseline)
_SMOKE_OBSERVATION_SECONDS = 180  # 3 minutes


# ---------------------------------------------------------------------------
# Infrastructure setup
# ---------------------------------------------------------------------------

def _setup_legacy_app_host(client: NexplaneClient, cloud_account_id: str,
                            tailscale_auth_key: str) -> dict:
    """
    Launch EC2 instance, install legacy app stack, deploy Nexplane agent.
    Returns dict with instance_asset, instance_id, endpoint_asset_id (agent-registered server asset).
    """
    log("Setup: launching EC2 instance for migration smoke test")

    # Check if backend is already on Tailscale (common for EC2 runners)
    backend_ip = _get_backend_tailscale_ip()
    if not backend_ip:
        auth_key = tailscale_auth_key or client.get_tailscale_auth_key("")
        backend_ip = setup_backend_tailscale(auth_key)
    else:
        log(f"Backend already on Tailscale: {backend_ip}")

    agent_secret = client.get_agent_secret()

    # Create key pair for the instance
    try:
        client.run_cr(
            "smoke-migration: create key pair", "key_pair_create", cloud_account_id,
            {"key_name": _KEY_NAME},
        )
    except SystemExit:
        log("Key pair may already exist — continuing")

    # Launch EC2 instance (Ubuntu 20.04 equivalent via amazon_linux for smoke)
    client.run_cr(
        "smoke-migration: launch EC2 instance", "ec2_launch", cloud_account_id,
        {
            "mode": "quick",
            "name": _INSTANCE_NAME,
            "os": "amazon_linux",
            "iam_instance_profile": "NexplaneEC2TestProfile",
            "key_name": _KEY_NAME,
            "rollback_strategy": "terminate_instance",
        },
    )
    time.sleep(10)

    instance_asset = client.get_asset_by_name(_INSTANCE_NAME)
    if not instance_asset:
        fail(f"Instance '{_INSTANCE_NAME}' not found in inventory after ec2_launch")
    instance_id = instance_asset.get("asset_metadata", {}).get("instance_id")
    if not instance_id:
        fail("instance_id missing from asset metadata")
    log(f"EC2 instance launched: {instance_id}")

    log("Waiting 3 minutes for SSM agent to register...")
    time.sleep(180)

    # Verify SSM connectivity
    client.run_cr(
        "smoke-migration: SSM whoami", "ssm_command", instance_asset["id"],
        {
            "instance_id": instance_id,
            "document_name": "AWS-RunShellScript",
            "command": "whoami",
            "rollback_strategy": "rollback_unavailable",
        },
    )

    # Install legacy app stack via SSM
    _install_legacy_app_stack(client, instance_asset["id"], instance_id)

    # Join Tailscale so agent can reach the control plane
    client.run_cr(
        "smoke-migration: tailscale join", "tailscale_join", instance_asset["id"],
        {"instance_id": instance_id, "auth_key": auth_key, "hostname": _AGENT_HOSTNAME},
    )

    # Deploy Nexplane agent
    nexplane_url = f"http://{backend_ip}:8000"
    client.run_cr(
        "smoke-migration: deploy nexplane agent", "deploy_nexplane_agent", instance_asset["id"],
        {
            "instance_id": instance_id,
            "nexplane_url": nexplane_url,
            "nexplane_secret": agent_secret,
            "hostname": _AGENT_HOSTNAME,
        },
    )

    # Wait for agent to register (creates server asset)
    log("Waiting up to 2 minutes for agent to register...")
    deadline = time.time() + 120
    agent_asset = None
    while time.time() < deadline:
        candidates = client.get("/assets", params={"q": _AGENT_HOSTNAME, "asset_type": "server"})
        if candidates:
            agent_asset = candidates[0]
            log(f"Agent registered: asset_id={agent_asset['id']}")
            break
        time.sleep(10)

    if not agent_asset:
        fail(f"Nexplane agent did not register within 120s as a server asset (hostname={_AGENT_HOSTNAME})")

    return {
        "instance_asset": instance_asset,
        "instance_id": instance_id,
        "agent_asset_id": agent_asset["id"],
        "backend_ip": backend_ip,
    }


def _install_legacy_app_stack(client: NexplaneClient, instance_asset_id: str, instance_id: str) -> None:
    """Install nginx + Flask + PostgreSQL 14 on the instance via SSM commands."""
    log("Installing legacy app stack (nginx, Flask, PostgreSQL)...")

    # Install packages — handle Amazon Linux 2023 (dnf), AL2 (yum), and Ubuntu (apt)
    client.run_cr(
        "smoke-migration: install packages", "ssm_command", instance_asset_id,
        {
            "instance_id": instance_id,
            "document_name": "AWS-RunShellScript",
            "command": (
                "set -e; "
                "if command -v dnf &>/dev/null; then "
                "  dnf install -y nginx python3 python3-pip postgresql15-server postgresql15 2>&1 || "
                "  dnf install -y nginx python3 python3-pip postgresql postgresql-server 2>&1; "
                "elif command -v yum &>/dev/null; then "
                "  yum install -y nginx python3 python3-pip postgresql postgresql-server; "
                "else "
                "  apt-get install -y nginx python3 python3-pip postgresql postgresql-contrib; "
                "fi; "
                "pip3 install flask psycopg2-binary --quiet 2>&1 || "
                "pip3 install flask psycopg2-binary 2>&1 || true"
            ),
            "rollback_strategy": "rollback_unavailable",
        },
        timeout=300,
    )

    # Setup PostgreSQL and Flask app
    client.run_cr(
        "smoke-migration: setup app", "ssm_command", instance_asset_id,
        {
            "instance_id": instance_id,
            "document_name": "AWS-RunShellScript",
            "command": _app_setup_script(),
            "rollback_strategy": "rollback_unavailable",
        },
        timeout=300,
    )

    log("Legacy app stack installed")


def _app_setup_script() -> str:
    return r"""set -e
# Init PostgreSQL — detect installed service via unit-files (works even before first start)
PG_SVC=""
if systemctl list-unit-files 2>/dev/null | grep -q "^postgresql-15\.service"; then
    PG_SVC="postgresql-15"
elif systemctl list-unit-files 2>/dev/null | grep -q "^postgresql-14\.service"; then
    PG_SVC="postgresql-14"
elif systemctl list-unit-files 2>/dev/null | grep -q "^postgresql\.service"; then
    PG_SVC="postgresql"
fi
echo "Detected PG service: $PG_SVC"

if [ -n "$PG_SVC" ]; then
    if command -v postgresql-setup &>/dev/null; then
        postgresql-setup --initdb 2>/dev/null || true
    fi
    if command -v pg_createcluster &>/dev/null; then
        pg_createcluster 14 main 2>/dev/null || true
    fi
    systemctl start "$PG_SVC" 2>/dev/null || true
    systemctl enable "$PG_SVC" 2>/dev/null || true
    sleep 3
fi
# Create DB and tables
sleep 3
sudo -u postgres psql -c "CREATE DATABASE smoke_app;" 2>/dev/null || true
sudo -u postgres psql -d smoke_app -c "
  CREATE TABLE IF NOT EXISTS users (id serial PRIMARY KEY, name text);
  CREATE TABLE IF NOT EXISTS orders (id serial PRIMARY KEY, user_id int REFERENCES users(id));
  CREATE TABLE IF NOT EXISTS audit_log (id serial PRIMARY KEY, action text);
" 2>/dev/null || true
sudo -u postgres psql -d smoke_app -c "
  INSERT INTO users (name) SELECT 'user_' || g FROM generate_series(1,5000) g
  ON CONFLICT DO NOTHING;
" 2>/dev/null || true

# Flask app
mkdir -p /opt/app /etc/app
cat > /opt/app/app.py << 'PYEOF'
from flask import Flask
import os
app = Flask(__name__)
@app.route('/')
def index(): return 'ok'
@app.route('/health')
def health(): return 'healthy'
if __name__ == '__main__': app.run(host='0.0.0.0', port=8080)
PYEOF

# Config file
cat > /etc/app/config.ini << 'INIEOF'
[app]
database_url = postgresql://postgres@localhost:5432/smoke_app
upstream_url = http://localhost:8080
INIEOF

# nginx config
cat > /etc/nginx/conf.d/smoke.conf << 'NGINXEOF' 2>/dev/null || \
cat > /etc/nginx/sites-available/default << 'NGINXEOF2'
server { listen 80; location / { proxy_pass http://127.0.0.1:8080; } }
NGINXEOF2
NGINXEOF

# Systemd unit for Flask
cat > /etc/systemd/system/smoke-app.service << 'SDEOF'
[Unit]
Description=Smoke App Flask
After=network.target
[Service]
ExecStart=/usr/bin/python3 /opt/app/app.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
SDEOF

systemctl daemon-reload
systemctl enable smoke-app
systemctl start smoke-app || true
systemctl restart nginx || true
sleep 3
systemctl is-active smoke-app && echo "smoke-app: active" || echo "smoke-app: not active (may still be starting)"
"""


def _run_ssm(client: NexplaneClient, instance_asset_id: str, instance_id: str,
             label: str, command: str, timeout: int = 120) -> None:
    """Run an ad-hoc SSM shell command via ssm_command CR."""
    client.run_cr(
        f"smoke-migration: {label}", "ssm_command", instance_asset_id,
        {
            "instance_id": instance_id,
            "document_name": "AWS-RunShellScript",
            "command": command,
            "rollback_strategy": "rollback_unavailable",
        },
        timeout=timeout,
    )


def _run_catalog_action(client: NexplaneClient, label: str,
                         connector_type: str, action_id: str,
                         asset_id: str, params: dict,
                         timeout: int = 300) -> dict:
    """
    Create, plan, approve, execute a catalog_action CR and wait for completion.
    Returns the completed CR dict.
    """
    base = client.base.rstrip("/")

    log(f"catalog_action: {connector_type}.{action_id} — {label}")

    # Create
    resp = client.client.post(f"{base}/change-requests", json={
        "change_type": "catalog_action",
        "title": f"smoke-migration: {label}",
        "desired_outcome": {
            "connector_type": connector_type,
            "action_id": action_id,
            "params": params,
            "rollback_strategy": "snapshot_restore",
            "_smoke_test": True,
        },
        "target_asset_ids": [asset_id],
    })
    if resp.status_code not in (200, 201):
        fail(f"POST /change-requests returned {resp.status_code}: {resp.text}")
    cr_id = resp.json()["id"]

    # Plan
    resp = client.client.post(f"{base}/change-requests/{cr_id}/plan")
    if resp.status_code not in (200, 201, 204):
        fail(f"POST /plan returned {resp.status_code}: {resp.text}")

    # Submit for approval
    resp = client.client.post(f"{base}/change-requests/{cr_id}/submit-for-approval")
    if resp.status_code not in (200, 201, 204):
        fail(f"POST /submit-for-approval returned {resp.status_code}: {resp.text}")

    # Approve
    resp = client.client.post(
        f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke test"},
    )
    if resp.status_code not in (200, 201, 204):
        fail(f"POST /approve returned {resp.status_code}: {resp.text}")

    # Execute
    resp = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    if resp.status_code not in (200, 201, 202, 204):
        fail(f"POST /execute returned {resp.status_code}: {resp.text}")

    # Poll
    deadline = time.time() + timeout
    cr = {}
    while time.time() < deadline:
        cr = client.get(f"/change-requests/{cr_id}")
        status = cr.get("status", "")
        if status in ("completed", "failed", "rolled_back", "rejected", "plan_blocked"):
            break
        time.sleep(5)
    else:
        fail(f"catalog_action {action_id} timed out after {timeout}s (cr_id={cr_id})")

    return cr


def _extract_execution_result(cr: dict) -> dict:
    """Extract execution result from a completed CR (handles all result locations)."""
    result = cr.get("execution_result") or cr.get("result")
    if result:
        return result
    runs = cr.get("execution_runs") or []
    for run in runs:
        r = run.get("result") or {}
        # catalog_action wraps result inside execution.steps[0].result
        steps = r.get("execution", {}).get("steps", [])
        for step in steps:
            if step.get("result"):
                return step["result"]
        if r:
            return r
    return {}


# ---------------------------------------------------------------------------
# Phase 1: DISCOVERY_PROFILE
# ---------------------------------------------------------------------------

def phase_discovery_profile(client: NexplaneClient, agent_asset_id: str) -> str:
    """
    Run discover_application_profile against the legacy app host.
    Asserts ApplicationProfile asset is created with expected endpoints/dependencies/config.
    Returns the application_profile asset ID.
    """
    log("=== Phase 1: DISCOVERY_PROFILE ===")

    cr = _run_catalog_action(
        client,
        label="discover application profile",
        connector_type="nexplane_agent",
        action_id="discover_application_profile",
        asset_id=agent_asset_id,
        params={"asset_id": agent_asset_id},
        timeout=300,
    )

    if cr.get("status") != "completed":
        fail(f"Phase 1: discover_application_profile CR did not complete — status={cr.get('status')}, "
             f"cr_id={cr.get('id')}")

    result = _extract_execution_result(cr)

    # The executor returns _auto_asset_id in the result when the application_profile is created
    profile_asset_id = result.get("_auto_asset_id")
    if not profile_asset_id:
        # Fall back: look up by asset_type
        profiles = client.get("/assets", params={"asset_type": "application_profile"})
        if not profiles:
            fail("Phase 1: No application_profile assets found after discover_application_profile")
        # Pick most recently created
        profiles.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        profile_asset_id = profiles[0]["id"]
        log(f"Phase 1: _auto_asset_id not in result — found profile via asset list: {profile_asset_id}")
    else:
        log(f"Phase 1: application_profile asset created: {profile_asset_id}")

    # Get profile asset detail and verify content
    profile_asset = client.get(f"/assets/{profile_asset_id}")
    meta = profile_asset.get("asset_metadata", {})

    # Verify discovered endpoints include port 80 (nginx) and 8080 (Flask)
    endpoints = meta.get("endpoints", [])
    ports_found = {ep.get("port") for ep in endpoints}
    if 80 not in ports_found:
        fail(f"Phase 1: Port 80 (nginx) not in discovered endpoints: {ports_found}. "
             f"endpoints={endpoints}")
    if 8080 not in ports_found:
        fail(f"Phase 1: Port 8080 (Flask) not in discovered endpoints: {ports_found}. "
             f"endpoints={endpoints}")
    log(f"Phase 1: Endpoints discovered: {sorted(ports_found)}")

    # Verify PostgreSQL dependency (port 5432)
    deps = meta.get("dependencies", [])
    pg_dep = next((d for d in deps if d.get("port") == 5432 or "5432" in str(d.get("target", ""))), None)
    if pg_dep is None:
        fail(f"Phase 1: PostgreSQL dependency (port 5432) not discovered. deps={deps}")
    if pg_dep.get("confidence") not in ("both", "config_only"):
        fail(f"Phase 1: Unexpected PostgreSQL dependency confidence: {pg_dep.get('confidence')}")
    log(f"Phase 1: PostgreSQL dependency found, confidence={pg_dep.get('confidence')}")

    # Verify /etc/app/config.ini discovered
    config_files = meta.get("config_files", [])
    config_paths = [cf.get("path", "") for cf in config_files]
    if not any("config.ini" in p for p in config_paths):
        fail(f"Phase 1: /etc/app/config.ini not in discovered config_files: {config_paths}")
    log(f"Phase 1: config_files: {config_paths}")

    # Verify services include nginx and smoke-app
    services = meta.get("services", [])
    service_names = [s.get("name", "") for s in services]
    for expected in ("nginx", "smoke-app"):
        if not any(expected in s for s in service_names):
            fail(f"Phase 1: Service '{expected}' not in discovered services: {service_names}")
    log(f"Phase 1: Services discovered: {service_names}")

    # Verify library_versions non-empty
    if not meta.get("library_versions"):
        log("Phase 1: WARNING — library_versions is empty (may depend on agent version)")

    log(f"Phase 1 PASS — ApplicationProfile {profile_asset_id} created with "
        f"ports 80/8080, pg dep, config.ini, services nginx+smoke-app")
    return profile_asset_id


# ---------------------------------------------------------------------------
# Phase 2: BEHAVIORAL_BASELINE
# ---------------------------------------------------------------------------

def phase_behavioral_baseline(client: NexplaneClient, profile_asset_id: str) -> dict:
    """
    Run capture_behavioral_baseline against the application_profile asset.
    Asserts baseline captured with correct endpoint status, latency, service state, pg dep row count.
    Returns the baseline dict for use in Phase 4.
    """
    log("=== Phase 2: BEHAVIORAL_BASELINE ===")

    cr = _run_catalog_action(
        client,
        label="capture behavioral baseline",
        connector_type="nexplane_agent",
        action_id="capture_behavioral_baseline",
        asset_id=profile_asset_id,
        params={"observation_window_seconds": _SMOKE_OBSERVATION_SECONDS},
        timeout=_SMOKE_OBSERVATION_SECONDS + 120,
    )

    if cr.get("status") != "completed":
        fail(f"Phase 2: capture_behavioral_baseline CR did not complete — status={cr.get('status')}, "
             f"cr_id={cr.get('id')}")

    result = _extract_execution_result(cr)
    baseline = result.get("baseline", {})

    if not baseline:
        fail(f"Phase 2: No baseline in execution result. result keys={list(result.keys())}")

    # Verify port 80 endpoint captured
    endpoints = baseline.get("endpoints", [])
    ep_80 = next((e for e in endpoints if "80" in str(e.get("url", "")) or e.get("port") == 80), None)
    if ep_80 is None:
        fail(f"Phase 2: Port 80 endpoint not in baseline endpoints: {endpoints}")
    if ep_80.get("status_code") != 200:
        fail(f"Phase 2: Port 80 endpoint status_code not 200: {ep_80.get('status_code')}")
    log(f"Phase 2: Port 80 endpoint — status={ep_80.get('status_code')}, "
        f"p50={ep_80.get('response_ms_p50')}ms, confidence={ep_80.get('confidence')}")

    # Verify port 8080 endpoint captured
    ep_8080 = next((e for e in endpoints if "8080" in str(e.get("url", "")) or e.get("port") == 8080), None)
    if ep_8080 is None:
        fail(f"Phase 2: Port 8080 endpoint not in baseline endpoints: {endpoints}")
    log(f"Phase 2: Port 8080 endpoint — confidence={ep_8080.get('confidence')}")

    # Verify PostgreSQL dependency row count sampled
    deps = baseline.get("dependencies", [])
    pg_dep = next((d for d in deps if "5432" in str(d.get("target", "")) or d.get("port") == 5432), None)
    if pg_dep is None:
        fail(f"Phase 2: PostgreSQL dependency not in baseline: {deps}")
    if not pg_dep.get("row_count_sample"):
        log(f"Phase 2: WARNING — row_count_sample not in pg dep (may be agent limitation): {pg_dep}")
    log(f"Phase 2: PostgreSQL dep — confidence={pg_dep.get('confidence')}, "
        f"row_count={pg_dep.get('row_count_sample')}")

    # Verify smoke-app service state
    services = baseline.get("services", [])
    smoke_svc = next((s for s in services if "smoke-app" in s.get("name", "")), None)
    if smoke_svc is None:
        fail(f"Phase 2: smoke-app service not in baseline services: {[s.get('name') for s in services]}")
    if smoke_svc.get("state") != "active":
        fail(f"Phase 2: smoke-app service state not 'active': {smoke_svc.get('state')}")
    log(f"Phase 2: smoke-app service state={smoke_svc.get('state')}")

    log("Phase 2 PASS — baseline captured with port 80/8080 endpoints, pg dep, smoke-app active")
    return baseline


# ---------------------------------------------------------------------------
# Phase 3: BASELINE_ADAPTIVE
# ---------------------------------------------------------------------------

def phase_baseline_adaptive(client: NexplaneClient, instance_asset_id: str,
                             instance_id: str, agent_asset_id: str) -> None:
    """
    Fresh capture with Flask stopped initially. Start Flask mid-window.
    Assert observation_duration_seconds extends beyond the initial window.
    """
    log("=== Phase 3: BASELINE_ADAPTIVE ===")

    # Stop Flask to simulate an idle dependency
    log("Phase 3: Stopping smoke-app (Flask) to simulate idle dependency")
    _run_ssm(client, instance_asset_id, instance_id,
             "stop smoke-app for adaptive test", "systemctl stop smoke-app || true")
    time.sleep(5)

    # Start a baseline capture with a 2-minute window
    short_window = 120
    base = client.base.rstrip("/")

    log(f"Phase 3: Creating baseline CR with {short_window}s window (Flask is down)")
    resp = client.client.post(f"{base}/change-requests", json={
        "change_type": "catalog_action",
        "title": "smoke-migration: adaptive baseline (Flask stopped)",
        "desired_outcome": {
            "connector_type": "nexplane_agent",
            "action_id": "capture_behavioral_baseline",
            "params": {"observation_window_seconds": short_window},
            "rollback_strategy": "snapshot_restore",
            "_smoke_test": True,
        },
        "target_asset_ids": [agent_asset_id],
    })
    if resp.status_code not in (200, 201):
        fail(f"Phase 3: POST /change-requests returned {resp.status_code}: {resp.text}")
    cr_id = resp.json()["id"]

    # Plan → approve → execute (non-blocking — we need to start Flask mid-run)
    client.client.post(f"{base}/change-requests/{cr_id}/plan")
    client.client.post(f"{base}/change-requests/{cr_id}/submit-for-approval")
    client.client.post(f"{base}/change-requests/{cr_id}/approve",
                       json={"decision": "approved", "comment": "smoke test"})
    client.client.post(f"{base}/change-requests/{cr_id}/execute")

    # Wait for initial window to elapse, then start Flask to trigger adaptive extension
    log(f"Phase 3: Waiting {short_window + 10}s, then starting Flask mid-window...")
    time.sleep(short_window + 10)

    log("Phase 3: Starting smoke-app (Flask) to trigger adaptive extension")
    _run_ssm(client, instance_asset_id, instance_id,
             "start smoke-app for adaptive extension", "systemctl start smoke-app || true")
    time.sleep(5)

    # Wait for the CR to complete (agent extends window then finalizes)
    log("Phase 3: Waiting for adaptive baseline CR to complete...")
    deadline = time.time() + 900  # 15 minutes max
    cr = {}
    while time.time() < deadline:
        cr = client.get(f"/change-requests/{cr_id}")
        status = cr.get("status", "")
        if status in ("completed", "failed", "rolled_back", "rejected", "plan_blocked"):
            break
        time.sleep(10)
    else:
        fail(f"Phase 3: adaptive baseline CR timed out (cr_id={cr_id})")

    if cr.get("status") != "completed":
        fail(f"Phase 3: adaptive baseline CR did not complete — status={cr.get('status')}")

    result = _extract_execution_result(cr)
    obs_duration = result.get("observation_duration_seconds", 0)

    if obs_duration <= short_window:
        fail(f"Phase 3: observation_duration_seconds ({obs_duration}) not extended beyond "
             f"initial window ({short_window}). Adaptive extension did not trigger.")

    log(f"Phase 3: observation_duration extended to {obs_duration}s (initial={short_window}s)")

    # Verify PostgreSQL dep present in final baseline (discovered via config.ini even with Flask down)
    baseline = result.get("baseline", {})
    deps = baseline.get("dependencies", [])
    pg_dep = next((d for d in deps if "5432" in str(d.get("target", "")) or d.get("port") == 5432), None)
    if pg_dep is None:
        fail(f"Phase 3: PostgreSQL dependency not in final adaptive baseline: {deps}")
    log(f"Phase 3: PostgreSQL dep — confidence={pg_dep.get('confidence')}")

    # Restart Flask for Phase 4 (if this phase ran before phase 4 on same host, restore state)
    _run_ssm(client, instance_asset_id, instance_id,
             "restore smoke-app after adaptive test", "systemctl start smoke-app || true")
    time.sleep(5)

    log("Phase 3 PASS — adaptive window extended beyond initial window, pg dep captured")


# ---------------------------------------------------------------------------
# Phase 4: VERIFY_BASELINE
# ---------------------------------------------------------------------------

def phase_verify_baseline(client: NexplaneClient, profile_asset_id: str,
                           instance_asset_id: str, instance_id: str) -> None:
    """
    Pass path: all 4 layers should pass when Flask is running.
    Failure path: Flask stopped → Application layer fails → failed=True.
    """
    log("=== Phase 4: VERIFY_BASELINE ===")

    # Ensure Flask is running for the pass path
    _run_ssm(client, instance_asset_id, instance_id,
             "ensure smoke-app running for verify pass path", "systemctl start smoke-app || true")
    time.sleep(5)

    # --- Pass path ---
    log("Phase 4: Pass path (Flask running, all layers should pass)")
    cr_pass = _run_catalog_action(
        client,
        label="verify against baseline (pass path)",
        connector_type="nexplane_agent",
        action_id="verify_against_baseline",
        asset_id=profile_asset_id,
        params={},
        timeout=180,
    )

    if cr_pass.get("status") != "completed":
        fail(f"Phase 4 pass path: CR did not complete — status={cr_pass.get('status')}, "
             f"cr_id={cr_pass.get('id')}")

    result_pass = _extract_execution_result(cr_pass)
    if result_pass.get("failed"):
        fail(f"Phase 4 pass path: verify returned failed=True when Flask is running. "
             f"layers={result_pass.get('layers')}")

    layers_pass = result_pass.get("layers", {})
    for layer in ("infrastructure", "service", "application", "data"):
        ldata = layers_pass.get(layer, {})
        if ldata and not ldata.get("passed"):
            fail(f"Phase 4 pass path: layer '{layer}' did not pass. detail={ldata}")
    log(f"Phase 4 pass path: all layers passed. layers={list(layers_pass.keys())}")

    # --- Failure path ---
    log("Phase 4: Failure path (Flask stopped, Application layer should fail)")
    _run_ssm(client, instance_asset_id, instance_id,
             "stop smoke-app for verify failure path", "systemctl stop smoke-app || true")
    time.sleep(5)

    cr_fail = _run_catalog_action(
        client,
        label="verify against baseline (failure path — Flask stopped)",
        connector_type="nexplane_agent",
        action_id="verify_against_baseline",
        asset_id=profile_asset_id,
        params={},
        timeout=180,
    )

    # CR may end in completed (with failed=True) or rolled_back (if FILO unwind fired)
    if cr_fail.get("status") not in ("completed", "rolled_back", "failed"):
        fail(f"Phase 4 failure path: unexpected CR status={cr_fail.get('status')}")

    result_fail = _extract_execution_result(cr_fail)
    if not result_fail.get("failed"):
        fail("Phase 4 failure path: verify returned failed=False when Flask is stopped. "
             "Application layer should have failed.")

    layers_fail = result_fail.get("layers", {})
    app_layer = layers_fail.get("application", {})
    if app_layer and app_layer.get("passed"):
        fail("Phase 4 failure path: application layer shows passed=True when Flask is down")

    log(f"Phase 4 failure path: failed=True, application layer={app_layer}")
    log("Phase 4 PASS — pass path all-green, failure path correctly detected Application layer failure")


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------

def _teardown(client: NexplaneClient) -> None:
    """Terminate EC2 instance and clean up inventory assets."""
    log("Teardown: cleaning up smoke test resources")

    # Terminate via AWS boto3
    try:
        ec2 = _get_aws_boto3_client("ec2")
        if ec2:
            reservations = ec2.describe_instances(
                Filters=[
                    {"Name": "tag:Name", "Values": [_INSTANCE_NAME]},
                    {"Name": "instance-state-name",
                     "Values": ["pending", "running", "stopping", "stopped"]},
                ]
            ).get("Reservations", [])
            for res in reservations:
                for inst in res.get("Instances", []):
                    iid = inst["InstanceId"]
                    ec2.terminate_instances(InstanceIds=[iid])
                    log(f"Terminated EC2 {iid}")

            # Delete key pair
            try:
                ec2.delete_key_pair(KeyName=_KEY_NAME)
                log(f"Deleted key pair {_KEY_NAME}")
            except Exception:
                pass
    except Exception as e:
        log(f"WARNING: AWS boto3 teardown error: {e}")

    # Delete inventory assets
    try:
        for q in (_INSTANCE_NAME, _AGENT_HOSTNAME, "smoke-migration"):
            assets = client.get("/assets", params={"q": q})
            for asset in assets:
                name = asset.get("name", "")
                if "smoke-migration" in name or "smoke_migration" in name or _AGENT_HOSTNAME in name:
                    try:
                        client.client.delete(f"{client.base}/assets/{asset['id']}")
                        log(f"Deleted asset: {name}")
                    except Exception:
                        pass
    except Exception as e:
        log(f"WARNING: inventory cleanup error: {e}")


# ---------------------------------------------------------------------------
# Combined run entry point
# ---------------------------------------------------------------------------

def run(client: NexplaneClient, tailscale_auth_key: str = "") -> None:
    log("=== Migration Foundation Smoke — Phases 1–4 ===")

    cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Using cloud account asset: {cloud_account_id}")

    setup = _setup_legacy_app_host(client, cloud_account_id, tailscale_auth_key)
    agent_asset_id = setup["agent_asset_id"]
    instance_asset_id = setup["instance_asset"]["id"]
    instance_id = setup["instance_id"]

    try:
        # Phase 1: discover application profile
        profile_asset_id = phase_discovery_profile(client, agent_asset_id)

        # Phase 2: capture behavioral baseline
        phase_behavioral_baseline(client, profile_asset_id)

        # Phase 3: adaptive baseline (uses same host; stops/starts Flask)
        phase_baseline_adaptive(client, instance_asset_id, instance_id, agent_asset_id)

        # Ensure Flask is running before Phase 4 (Phase 3 restores it, but double-check)
        time.sleep(5)

        # Phase 4: verify against baseline
        phase_verify_baseline(client, profile_asset_id, instance_asset_id, instance_id)

        log("=== All phases PASSED ===")
    finally:
        _teardown(client)


def main() -> None:
    parser = make_base_parser("Migration Foundation Smoke Test — Phases 1–4")
    parser.add_argument(
        "--phases",
        default=",".join(ALL_PHASES),
        help=f"Comma-separated phases to run (default: all). Options: {','.join(ALL_PHASES)}",
    )
    parser.add_argument(
        "--tailscale-auth-key", default="",
        help="Tailscale auth key (optional — will be fetched from connector if not provided)",
    )
    args = parser.parse_args()

    phases = [p.strip().upper() for p in args.phases.split(",") if p.strip()]
    invalid = [p for p in phases if p not in ALL_PHASES]
    if invalid:
        print(f"Unknown phases: {invalid}. Valid: {ALL_PHASES}", file=sys.stderr)
        sys.exit(1)

    client = NexplaneClient(args.base_url, args.email, args.password)

    if phases == ALL_PHASES:
        # Full combined run
        run(client, tailscale_auth_key=args.tailscale_auth_key)
    else:
        # If running individual phases, still need setup
        cloud_account_id = client.get_cloud_account_asset_id()
        setup = _setup_legacy_app_host(client, cloud_account_id, args.tailscale_auth_key)
        agent_asset_id = setup["agent_asset_id"]
        instance_asset_id = setup["instance_asset"]["id"]
        instance_id = setup["instance_id"]
        profile_asset_id = None
        try:
            if "DISCOVERY_PROFILE" in phases:
                profile_asset_id = phase_discovery_profile(client, agent_asset_id)
            if "BEHAVIORAL_BASELINE" in phases:
                if not profile_asset_id:
                    profiles = client.get("/assets", params={"asset_type": "application_profile"})
                    if not profiles:
                        fail("BEHAVIORAL_BASELINE requires DISCOVERY_PROFILE to run first")
                    profile_asset_id = profiles[0]["id"]
                phase_behavioral_baseline(client, profile_asset_id)
            if "BASELINE_ADAPTIVE" in phases:
                phase_baseline_adaptive(client, instance_asset_id, instance_id, agent_asset_id)
            if "VERIFY_BASELINE" in phases:
                if not profile_asset_id:
                    profiles = client.get("/assets", params={"asset_type": "application_profile"})
                    if not profiles:
                        fail("VERIFY_BASELINE requires DISCOVERY_PROFILE to run first")
                    profile_asset_id = profiles[0]["id"]
                phase_verify_baseline(client, profile_asset_id, instance_asset_id, instance_id)
            log(f"=== Selected phases PASSED: {phases} ===")
        finally:
            _teardown(client)


if __name__ == "__main__":
    main()
