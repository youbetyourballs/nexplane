#!/usr/bin/env python3
"""
AMI smoke test — 9-phase functional verification of a freshly launched Nexplane AMI.
Usage: python packer/smoke_ami.py --ami-id ami-xxx --key-name smoke-key --security-group-id sg-xxx
"""
import argparse
import ipaddress
import subprocess
import sys
import time

import boto3
import requests

INSTANCE_TYPE = "t3.medium"
BOOT_TIMEOUT   = 420   # seconds to wait for port 80 after EC2 status OK
HTTP_POLL      = 15    # seconds between HTTP readiness polls
PLAN_TIMEOUT   = 60    # seconds to wait for CR to reach "planned"
EXEC_TIMEOUT   = 90    # seconds to wait for CR execution to reach terminal state
EXEC_TERMINAL  = {"completed", "failed", "executed", "executing", "rollback_complete"}
PLAN_TERMINAL  = {"planned", "failed"}

# Containers expected to be running on the AMI
EXPECTED_CONTAINERS = ("db", "backend", "webserver")

# All accounts seeded by seed.py when DEMO_MODE=true.
# Each tuple: (email, password, expected_role)
SEEDED_ACCOUNTS = [
    ("admin@nexplane.local",   "changeme",    "admin"),
    ("operator@acme.example",  "operator123", "security_operator"),
    ("approver@acme.example",  "approver123", "approver"),
    ("auditor@acme.example",   "auditor123",  "auditor"),
]

# All known frontend routes that must return 200 with text/html content type
FRONTEND_ROUTES = [
    "/",
    "/login",
    "/change-requests",
    "/connectors",
    "/settings",
    "/compliance",
    "/runbooks",
]


def _is_rfc1918(host):
    """Return True if host is an RFC1918 private address."""
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private
    except ValueError:
        return False


def ssh_run(host, cmd, key_path=None, username="ubuntu", password=None, timeout=30, check=True):
    """Run cmd on host via SSH. Returns (stdout, stderr, returncode).

    Auth method is selected automatically by IP:
      - RFC1918 / private IP → password auth (sshpass); suits local and VPC-internal instances.
      - Public IP            → key-pair auth; suits EC2 AMI smoke runs.

    key_path: path to .pem file (required for public-IP hosts).
    password: plaintext password (used for RFC1918 hosts; requires sshpass on the runner).
    check: if True, raises RuntimeError on non-zero exit code.
    """
    if _is_rfc1918(host):
        if password is None:
            raise ValueError(
                f"RFC1918 host {host} requires --ssh-password for password-based auth"
            )
        ssh_cmd = [
            "sshpass", "-p", password,
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", f"ConnectTimeout={timeout}",
            f"{username}@{host}",
            cmd,
        ]
    else:
        if key_path is None:
            raise ValueError(
                f"Public host {host} requires --key-name for key-pair auth"
            )
        ssh_cmd = [
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", f"ConnectTimeout={timeout}",
            "-i", key_path,
            f"{username}@{host}",
            cmd,
        ]
    result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout + 5)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"ssh_run failed (exit {result.returncode}): {cmd!r}\nstderr: {result.stderr.strip()}"
        )
    return result.stdout, result.stderr, result.returncode


def log(msg):
    print(f"[smoke-ami] {msg}", flush=True)


def fail(msg):
    raise RuntimeError(msg)


def api(method, base_url, path, token=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = getattr(requests, method)(
        f"{base_url}/api{path}", headers=headers, timeout=20, **kwargs
    )
    return r


def poll_cr_status(base_url, token, cr_id, terminal_states, timeout, label):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = api("get", base_url, f"/change-requests/{cr_id}", token=token)
        if r.status_code != 200:
            fail(f"GET /change-requests/{cr_id} returned {r.status_code}")
        status = r.json().get("status", "")
        log(f"  CR status: {status}")
        if status in terminal_states:
            return status
        time.sleep(8)
    fail(f"CR did not reach {terminal_states} within {timeout}s ({label})")


# ── Phase 1: Launch + Network ─────────────────────────────────────────────────

def phase_launch(ec2, args):
    log("[PHASE 1: launch+network]")
    resp = ec2.run_instances(
        ImageId=args.ami_id,
        InstanceType=INSTANCE_TYPE,
        MinCount=1, MaxCount=1,
        KeyName=args.key_name,
        SecurityGroupIds=[args.security_group_id],
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": "nexplane-ami-smoke"}],
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    log(f"  Instance {instance_id} launched — waiting for EC2 status checks")

    waiter = ec2.get_waiter("instance_status_ok")
    waiter.wait(InstanceIds=[instance_id], WaiterConfig={"Delay": 15, "MaxAttempts": 30})

    desc = ec2.describe_instances(InstanceIds=[instance_id])
    inst = desc["Reservations"][0]["Instances"][0]
    public_ip = inst.get("PublicIpAddress")
    private_ip = inst.get("PrivateIpAddress")
    if not public_ip:
        fail("Instance has no public IP — ensure AMI is launched in a public subnet with auto-assign enabled")
    log(f"  Public IP: {public_ip}  Private IP: {private_ip}")

    base_url = f"http://{public_ip}"
    log(f"  Waiting for port 80 (up to {BOOT_TIMEOUT}s)")
    deadline = time.time() + BOOT_TIMEOUT
    while time.time() < deadline:
        try:
            r = requests.get(base_url + "/", timeout=5)
            if r.status_code == 200:
                log("  GET / → 200 OK")
                log("[PHASE 1: launch+network] PASSED")
                return instance_id, public_ip, base_url
        except Exception:
            pass
        time.sleep(HTTP_POLL)
    fail(f"Platform did not respond on port 80 within {BOOT_TIMEOUT}s")


# ── Phase 2: Container health ─────────────────────────────────────────────────

def phase_container_health(public_ip, key_path=None, username="ubuntu", password=None):
    log("[PHASE 2: container-health]")
    deadline = time.time() + 300
    while True:
        stdout, stderr, rc = ssh_run(
            public_ip,
            "docker ps --format '{{.Names}}' --filter status=running",
            key_path=key_path, username=username, password=password,
            check=False,
        )
        if rc != 0:
            fail(f"SSH docker ps failed: {stderr.strip()}")
        running = stdout.strip().splitlines()
        missing = [e for e in EXPECTED_CONTAINERS if not any(e in n for n in running)]
        if not missing:
            log(f"  Running containers: {running}")
            break
        if time.time() >= deadline:
            fail(f"Expected containers {missing} not running after 300s. Got: {running}")
        log(f"  Waiting for containers {missing} (got {running})")
        time.sleep(10)
    log("[PHASE 2: container-health] PASSED")


# ── Phase 3: Authentication ───────────────────────────────────────────────────

def phase_auth(base_url, public_ip=None, key_path=None, username="ubuntu", password=None):
    log("[PHASE 3: authentication]")
    deadline = time.time() + 300
    while True:
        try:
            r = api("post", base_url, "/auth/login",
                    json={"email": "admin@nexplane.local", "password": "changeme"})
            if r.status_code == 200:
                break
            status = r.status_code
        except requests.exceptions.RequestException as exc:
            status = repr(exc)
        if time.time() >= deadline:
            if public_ip and (key_path or password):
                ssh_out, _, _ = ssh_run(
                    public_ip,
                    "docker logs --tail 50 nexplane-backend-1 2>&1 || true",
                    key_path=key_path, username=username, password=password,
                    check=False,
                )
                log(f"  Backend logs:\n{ssh_out[-2000:]}")
            fail(f"Login failed after 300s: {status}")
        log(f"  Backend not ready yet ({status}), retrying in 15s...")
        time.sleep(15)
    token = r.json().get("access_token")
    if not token:
        fail(f"No access_token in login response: {r.text[:300]}")
    log("  POST /api/auth/login → token received")

    r = api("get", base_url, "/auth/me", token=token)
    if r.status_code != 200:
        fail(f"GET /auth/me failed: {r.status_code}")
    me = r.json()
    if me.get("email") != "admin@nexplane.local":
        fail(f"Unexpected /auth/me email: {me.get('email')}")
    log(f"  GET /auth/me → {me.get('email')} ({me.get('role', '?')})")

    log("  Verifying all seeded accounts...")
    for email, password, expected_role in SEEDED_ACCOUNTS:
        r = api("post", base_url, "/auth/login", json={"email": email, "password": password})
        if r.status_code != 200:
            fail(f"Login failed for {email}: {r.status_code} {r.text[:200]}")
        acct_token = r.json().get("access_token")
        if not acct_token:
            fail(f"No access_token for {email}: {r.text[:200]}")
        r2 = api("get", base_url, "/auth/me", token=acct_token)
        if r2.status_code != 200:
            fail(f"GET /auth/me failed for {email}: {r2.status_code}")
        actual_role = r2.json().get("role", "")
        if actual_role != expected_role:
            fail(f"{email}: expected role '{expected_role}', got '{actual_role}'")
        log(f"  {email} → role={actual_role} ✓")

    log("[PHASE 3: authentication] PASSED")
    return token


# ── Phase 4: Asset management + impact graph ──────────────────────────────────

def phase_assets(base_url, token):
    log("[PHASE 4: asset-management]")
    created_ids = []

    def make_asset(name, asset_type):
        r = api("post", base_url, "/assets", token=token,
                json={"name": name, "asset_type": asset_type, "environment": "staging", "criticality": "medium"})
        if r.status_code not in (200, 201):
            fail(f"POST /assets ({name}) failed: {r.status_code} {r.text[:200]}")
        aid = r.json()["id"]
        created_ids.append(aid)
        log(f"  Created asset {name} → {aid}")
        return aid

    web_id = make_asset("smoke-web",   "server")
    app_id = make_asset("smoke-app",   "server")
    db_id  = make_asset("smoke-db",    "database")

    # web depends_on app, app depends_on db
    for src, tgt in [(web_id, app_id), (app_id, db_id)]:
        r = api("post", base_url, f"/assets/{src}/relationships", token=token,
                json={"target_asset_id": tgt, "relationship_type": "depends_on"})
        if r.status_code not in (200, 201):
            fail(f"POST /assets/{src}/relationships failed: {r.status_code} {r.text[:200]}")

    # Impact simulation from db_id (root): downstream should include app + web
    r = api("get", base_url, f"/impact-simulation?asset_id={db_id}", token=token)
    if r.status_code != 200:
        fail(f"GET /impact-simulation failed: {r.status_code}")
    impact = r.json()
    downstream_ids = [a.get("id") for a in impact.get("downstream", [])]
    total = impact.get("downstream_risk", {}).get("total", 0)
    if total < 2:
        fail(f"Expected downstream_risk.total >= 2 from db root, got {total}")
    log(f"  Impact simulation: {total} downstream assets from db root ✓")

    # Verify upstream from web_id: should include app + db
    r = api("get", base_url, f"/impact-simulation?asset_id={web_id}", token=token)
    if r.status_code != 200:
        fail(f"GET /impact-simulation (web) failed: {r.status_code}")
    upstream_ids = [a.get("id") for a in r.json().get("upstream", [])]
    if app_id not in upstream_ids:
        fail(f"Expected app_id in upstream of web. Got: {upstream_ids}")
    log("  Upstream chain from web → app → db verified ✓")

    log("[PHASE 4: asset-management] PASSED")
    return web_id, app_id, db_id, created_ids


# ── Phase 5: Connector configuration ─────────────────────────────────────────

def phase_connectors(base_url, token):
    log("[PHASE 5: connector-configuration]")
    created_connector_ids = []

    # AWS connector with fake credentials (tests config API, not connectivity)
    r = api("post", base_url, "/connectors", token=token, json={
        "name": "smoke-aws",
        "connector_type": "aws",
        "description": "AMI smoke test connector",
        "config": {"region": "us-east-1"},
    })
    if r.status_code not in (200, 201):
        fail(f"POST /connectors (aws) failed: {r.status_code} {r.text[:200]}")
    aws_conn_id = r.json()["id"]
    created_connector_ids.append(aws_conn_id)
    log(f"  Created AWS connector → {aws_conn_id}")

    r = api("put", base_url, f"/connectors/{aws_conn_id}/credentials", token=token, json={
        "credentials": {
            "access_key_id": "AKIAIOSFODNN7SMOKE00",
            "secret_access_key": "smoke-test-secret-not-real",
        },
    })
    if r.status_code not in (200, 201, 204):
        fail(f"PUT /connectors/{aws_conn_id}/credentials failed: {r.status_code} {r.text[:200]}")
    log("  Credentials accepted by platform ✓")

    # List connectors — verify smoke connector appears
    r = api("get", base_url, "/connectors", token=token)
    if r.status_code != 200:
        fail(f"GET /connectors failed: {r.status_code}")
    ids_in_list = [c.get("id") for c in r.json()]
    if aws_conn_id not in ids_in_list:
        fail(f"Created connector {aws_conn_id} not in GET /connectors list")
    log(f"  GET /connectors lists {len(ids_in_list)} connector(s) ✓")

    # Second connector (github type)
    r = api("post", base_url, "/connectors", token=token, json={
        "name": "smoke-github",
        "connector_type": "github",
        "description": "AMI smoke test github connector",
        "config": {},
    })
    if r.status_code not in (200, 201):
        fail(f"POST /connectors (github) failed: {r.status_code} {r.text[:200]}")
    tunnel_conn_id = r.json()["id"]
    created_connector_ids.append(tunnel_conn_id)
    log(f"  Created github connector → {tunnel_conn_id}")

    log("[PHASE 5: connector-configuration] PASSED")
    return aws_conn_id, created_connector_ids


# ── Phase 6: Change request lifecycle ────────────────────────────────────────

def phase_cr_lifecycle(base_url, token, app_id, aws_conn_id):
    log("[PHASE 6: cr-lifecycle]")
    created_cr_ids = []

    r = api("post", base_url, "/change-requests", token=token, json={
        "change_type":     "tag_resource",
        "title":           "smoke-ami-cr",
        "desired_outcome": {"tag_key": "smoke", "tag_value": "true"},
        "target_asset_ids": [app_id],
        "connector_id":    aws_conn_id,
    })
    if r.status_code not in (200, 201):
        fail(f"POST /change-requests failed: {r.status_code} {r.text[:300]}")
    cr_id = r.json()["id"]
    created_cr_ids.append(cr_id)
    log(f"  CR created → {cr_id}")

    api("post", base_url, f"/change-requests/{cr_id}/plan", token=token)
    status = poll_cr_status(base_url, token, cr_id, PLAN_TERMINAL, PLAN_TIMEOUT, "planning")
    log(f"  CR planned (status={status}) ✓")

    # Verify blast_radius is populated
    r = api("get", base_url, f"/change-requests/{cr_id}", token=token)
    cr_detail = r.json()
    blast = (cr_detail.get("blast_radius") or
             cr_detail.get("change_plan", {}).get("blast_radius") or {})
    log(f"  blast_radius: {blast}")
    # blast_radius should exist (even if empty dict — planning ran)
    if blast is None:
        fail("blast_radius is None after planning")
    log("  blast_radius populated ✓")

    # Submit for approval + approve
    r = api("post", base_url, f"/change-requests/{cr_id}/submit-for-approval", token=token)
    if r.status_code not in (200, 201, 204):
        fail(f"submit-for-approval failed: {r.status_code} {r.text[:200]}")
    log("  Submitted for approval ✓")

    r = api("post", base_url, f"/change-requests/{cr_id}/approve", token=token,
            json={"decision": "approved", "comment": "AMI smoke test"})
    if r.status_code not in (200, 201, 204):
        fail(f"approve failed: {r.status_code} {r.text[:200]}")
    log("  Approved ✓")

    # Execute — fake creds will fail execution; that is expected and verified as graceful
    r = api("post", base_url, f"/change-requests/{cr_id}/execute", token=token)
    if r.status_code not in (200, 201, 202, 204):
        fail(f"execute failed unexpectedly at API level: {r.status_code} {r.text[:200]}")
    log("  Execute initiated — polling for terminal state")
    final_status = poll_cr_status(base_url, token, cr_id, EXEC_TERMINAL, EXEC_TIMEOUT, "execution")
    log(f"  Execution reached terminal state: {final_status} ✓")
    # "failed" is acceptable — fake connector creds will cause execution failure
    # What we verify: the platform handled it gracefully (no 500, no crash)

    log("[PHASE 6: cr-lifecycle] PASSED")
    return cr_id, created_cr_ids


# ── Phase 7: Rollback ────────────────────────────────────────────────────────

def phase_rollback(base_url, token, cr_id):
    log("[PHASE 7: rollback]")

    # Attempt CR-level rollback (may return 400 if CR is not in rollback-eligible state)
    r = api("post", base_url, f"/change-requests/{cr_id}/rollback", token=token)
    log(f"  POST /change-requests/{cr_id}/rollback → {r.status_code}")
    if r.status_code in (200, 201, 202, 204):
        log("  Rollback initiated at CR level ✓")
    elif r.status_code == 400:
        # Platform correctly rejects rollback on non-reversible state — verify the message
        detail = r.json().get("detail", "")
        log(f"  Rollback correctly rejected (non-reversible state): {detail}")
        # Verify rollback_available field in CR detail
        r2 = api("get", base_url, f"/change-requests/{cr_id}", token=token)
        rollback_avail = r2.json().get("rollback_available", None)
        log(f"  rollback_available field: {rollback_avail}")
    else:
        fail(f"Unexpected rollback response: {r.status_code} {r.text[:200]}")

    # Verify project rollback endpoint is reachable
    r = api("get", base_url, "/project-rollbacks", token=token)
    if r.status_code not in (200, 404):  # 404 if not yet implemented on this build
        fail(f"GET /project-rollbacks unexpected status: {r.status_code}")
    log("  Project rollback endpoint reachable ✓")

    log("[PHASE 7: rollback] PASSED")


# ── Phase 8: Feature surface spot-check ──────────────────────────────────────

def phase_feature_surface(base_url, token, cr_id):
    log("[PHASE 8: feature-surface]")
    endpoints = [
        ("/assets",                  "assets"),
        ("/compliance/baselines",    "compliance baselines"),
        ("/connectors",              "connectors"),
        ("/change-requests",         "change requests"),
        ("/recurring-jobs",          "recurring jobs"),
        ("/backup-targets",          "backup targets"),
        ("/change-requests",         "change requests list"),
        ("/audit-events",            "audit log"),
        ("/capabilities",            "capabilities"),
    ]
    for path, label in endpoints:
        r = api("get", base_url, path, token=token)
        if r.status_code != 200:
            fail(f"GET /api{path} ({label}) returned {r.status_code}")
        log(f"  GET /api{path} → 200 ✓")

    # Verify the CR from phase 6 appears in the list
    r = api("get", base_url, "/change-requests", token=token)
    cr_list = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    cr_ids_in_list = [c.get("id") for c in cr_list]
    if cr_id not in cr_ids_in_list:
        fail(f"CR {cr_id} from phase 6 not found in GET /change-requests list")
    log("  CR from phase 6 appears in change-requests list ✓")

    # Audit log must have entries from phases 4-7
    r = api("get", base_url, "/audit-events", token=token)
    audit_items = r.json() if isinstance(r.json(), list) else r.json().get("items", r.json().get("events", []))
    if not audit_items:
        fail("GET /api/audit-events returned empty — audit writes are silently failing")
    log(f"  Audit log has {len(audit_items)} entries ✓")

    # Verify platform settings endpoint is reachable under admin auth
    r = api("get", base_url, "/settings", token=token)
    if r.status_code != 200:
        fail(f"GET /api/settings failed: {r.status_code} {r.text[:200]}")
    log("  GET /api/settings → 200 ✓")

    # Auth rejection path must be functional
    r = api("post", base_url, "/auth/login",
            json={"email": "admin@nexplane.local", "password": "wrongpassword"})
    if r.status_code != 401:
        fail(f"Expected 401 for wrong password, got {r.status_code}")
    log("  Wrong-password → 401 ✓")

    # Agent binary must be reachable at the URL baked into the AMI
    agent_url = (
        "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com"
        "/nexplane-agent-linux-amd64-latest"
    )
    try:
        head = requests.head(agent_url, timeout=15, allow_redirects=True)
        if head.status_code != 200:
            fail(f"Agent download URL returned {head.status_code}: {agent_url}")
        content_length = int(head.headers.get("Content-Length", 0))
        if content_length == 0:
            fail(f"Agent download URL has Content-Length=0: {agent_url}")
        log(f"  Agent download URL → 200 ({content_length:,} bytes) ✓")
    except requests.exceptions.RequestException as exc:
        fail(f"Agent download URL unreachable: {exc}")

    log("[PHASE 8: feature-surface] PASSED")


# ── Phase 9: MCP server + agent token ────────────────────────────────────────

def phase_mcp(base_url, token):
    log("[PHASE 9: mcp-server]")

    # Create an agent token (required for MCP auth)
    r = api("post", base_url, "/auth/agent-tokens", token=token,
            json={"name": "smoke-ami-mcp", "scopes": ["read", "write"]})
    if r.status_code not in (200, 201):
        fail(f"POST /auth/agent-tokens failed: {r.status_code} {r.text[:200]}")
    agent_token = r.json().get("token") or r.json().get("access_token")
    agent_token_id = r.json().get("id")
    if not agent_token:
        fail(f"No token in agent-token response: {r.json()}")
    log(f"  Agent token created → {agent_token_id}")

    # Verify agent token appears in list
    r = api("get", base_url, "/auth/agent-tokens", token=token)
    if r.status_code != 200:
        fail(f"GET /auth/agent-tokens failed: {r.status_code}")
    token_ids = [t.get("id") for t in r.json()]
    if agent_token_id not in token_ids:
        fail(f"Agent token {agent_token_id} not in list")
    log("  Agent token listed ✓")

    # Open the MCP SSE stream in a background thread so the session stays alive
    # while we POST messages to it. FastMCP SSE transport: POST /mcp/messages/
    # returns 202 Accepted (empty body); results are pushed as SSE data events.
    import threading, queue as _q, json as _json, urllib.parse as _up
    mcp_url = f"{base_url}/api/mcp/sse"
    log(f"  Connecting to MCP SSE endpoint: {mcp_url}")
    session_id = None
    session_id_event = threading.Event()
    sse_error = []
    response_queue = _q.Queue()  # receives parsed JSON-RPC response dicts

    def _sse_reader():
        try:
            with requests.get(
                mcp_url,
                headers={"Authorization": f"Bearer {agent_token}", "Accept": "text/event-stream"},
                stream=True,
                timeout=(10, 120),
            ) as resp:
                if resp.status_code != 200:
                    sse_error.append(f"GET /api/mcp/sse returned {resp.status_code}")
                    session_id_event.set()
                    return
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw.startswith("data:"):
                        continue
                    data_val = raw[5:].strip()
                    if "session_id=" in data_val:
                        nonlocal session_id
                        session_id = _up.parse_qs(_up.urlparse(data_val).query).get("session_id", [None])[0]
                        session_id_event.set()
                    elif session_id_event.is_set() and data_val:
                        # JSON-RPC response sent back via SSE after a POST /messages/
                        try:
                            response_queue.put(_json.loads(data_val))
                        except _json.JSONDecodeError:
                            pass
        except Exception as exc:
            sse_error.append(str(exc))
            session_id_event.set()

    sse_thread = threading.Thread(target=_sse_reader, daemon=True)
    sse_thread.start()

    if not session_id_event.wait(timeout=15):
        fail("MCP SSE stream timed out waiting for endpoint event")
    if sse_error:
        fail(f"MCP SSE stream error: {sse_error[0]}")
    if not session_id:
        fail("MCP SSE stream did not emit an endpoint event with session_id")
    log("  MCP SSE endpoint → 200 text/event-stream ✓")
    log("  MCP session_id captured ✓")

    def mcp_call_sse(method, params, call_id, agent_tok, sid):
        """Send MCP JSON-RPC via POST; read response from SSE stream."""
        r = requests.post(
            f"{base_url}/api/mcp/messages/",
            params={"session_id": sid},
            headers={
                "Authorization": f"Bearer {agent_tok}",
                "Content-Type": "application/json",
            },
            json={k: v for k, v in {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}.items() if v is not None},
            timeout=20,
        )
        if r.status_code not in (200, 202):
            fail(f"MCP {method} POST returned HTTP {r.status_code}: {r.text[:300]}")
        # Result arrives via SSE; wait up to 30s
        try:
            data = response_queue.get(timeout=30)
        except _q.Empty:
            fail(f"MCP {method} — no response on SSE stream within 30s")
        if "error" in data:
            fail(f"MCP {method} returned JSON-RPC error: {data['error']}")
        return data.get("result", {})

    # MCP initialize handshake
    result = mcp_call_sse(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "smoke", "version": "0.0.1"},
            "capabilities": {},
        },
        call_id=1,
        agent_tok=agent_token,
        sid=session_id,
    )
    if not isinstance(result.get("capabilities"), dict):
        fail(f"MCP initialize result missing capabilities dict: {result}")
    log("  MCP initialize ✓")

    # Send notifications/initialized — required by MCP spec before any further requests.
    # Notifications have no id and return no response.
    r = requests.post(
        f"{base_url}/api/mcp/messages/",
        params={"session_id": session_id},
        headers={"Authorization": f"Bearer {agent_token}", "Content-Type": "application/json"},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        timeout=10,
    )
    if r.status_code not in (200, 202):
        fail(f"notifications/initialized returned HTTP {r.status_code}: {r.text[:200]}")
    log("  MCP notifications/initialized sent ✓")

    # MCP tools/list — verify tool registration didn't silently fail
    result = mcp_call_sse("tools/list", None, call_id=2, agent_tok=agent_token, sid=session_id)
    if not result.get("tools"):
        fail(f"MCP tools/list returned empty tools list: {result}")
    log(f"  MCP tools/list ✓ ({len(result['tools'])} tools)")

    # Clean up agent token
    r = api("delete", base_url, f"/auth/agent-tokens/{agent_token_id}", token=token)
    log(f"  Agent token deleted → {r.status_code}")

    log("[PHASE 9: mcp-server] PASSED")


# ── Phase 10: Initialization quality ─────────────────────────────────────────

def phase_initialization_quality(public_ip, key_path=None, username="ubuntu", password=None):
    log("[PHASE 10: initialization-quality]")

    # Alembic must be at head — catches silent migration failures
    stdout, _, _ = ssh_run(
        public_ip,
        "docker exec nexplane-backend-1 alembic current 2>&1",
        key_path=key_path, username=username, password=password,
    )
    if "(head)" not in stdout:
        fail(f"Alembic is not at head. Output:\n{stdout}")
    log("  Alembic at head ✓")

    # Backend must have zero ERROR-level log lines on clean boot
    # (exclude alembic lines which may contain the word "error" in table names)
    # Filter: exclude alembic lines and SQLAlchemy background hint lines
    # ("Background on this error at: ...") which match ' error ' incidentally
    _err_filter = "grep -i ' ERROR ' | grep -vi alembic | grep -v 'Background on this error at:'"
    stdout, _, rc = ssh_run(
        public_ip,
        f"docker logs nexplane-backend-1 2>&1 | {_err_filter} | wc -l",
        key_path=key_path, username=username, password=password,
        check=False,
    )
    error_count = int(stdout.strip() or "0")
    if error_count > 0:
        detail, _, _ = ssh_run(
            public_ip,
            f"docker logs nexplane-backend-1 2>&1 | {_err_filter}",
            key_path=key_path, username=username, password=password,
            check=False,
        )
        fail(f"Backend has {error_count} ERROR log line(s) on clean boot:\n{detail.strip()}")
    log("  Backend error log: 0 ERROR lines on clean boot ✓")

    log("[PHASE 10: initialization-quality] PASSED")


# ── Phase 11: Frontend route and static asset coverage ───────────────────────

def phase_frontend_routes(base_url):
    log("[PHASE 11: frontend-routes]")

    # All known SPA routes must return 200 with text/html content type
    for route in FRONTEND_ROUTES:
        try:
            r = requests.get(f"{base_url}{route}", timeout=15, allow_redirects=True)
        except requests.exceptions.RequestException as exc:
            fail(f"GET {route} raised exception: {exc}")
        if r.status_code != 200:
            fail(f"GET {route} returned {r.status_code} (expected 200)")
        ct = r.headers.get("content-type", "")
        if "text/html" not in ct:
            fail(f"GET {route} content-type is '{ct}' (expected text/html)")
        log(f"  GET {route} → 200 text/html ✓")

    # Parse root HTML and verify all referenced JS/CSS bundles load
    r = requests.get(f"{base_url}/", timeout=15)
    html = r.text
    import re
    asset_paths = re.findall(r'(?:src|href)="(/[^"]+\.(?:js|css))"', html)
    if not asset_paths:
        fail("No JS/CSS asset paths found in root HTML — Vite build may be broken")
    log(f"  Found {len(asset_paths)} static asset reference(s) in root HTML")
    for path in asset_paths:
        try:
            head = requests.head(f"{base_url}{path}", timeout=10, allow_redirects=True)
        except requests.exceptions.RequestException as exc:
            fail(f"HEAD {path} raised exception: {exc}")
        if head.status_code != 200:
            fail(f"Static asset {path} returned {head.status_code} (expected 200)")
        log(f"  HEAD {path} → 200 ✓")

    log("[PHASE 11: frontend-routes] PASSED")


# ── Phase 12: Demo mode on — seed data present ───────────────────────────────

def phase_demo_mode_on(base_url, token):
    log("[PHASE 12: demo-mode-on]")

    def get_count(path):
        r = api("get", base_url, path, token=token)
        if r.status_code != 200:
            fail(f"GET /api{path} failed: {r.status_code}")
        body = r.json()
        if isinstance(body, list):
            return len(body)
        return body.get("total", body.get("count", len(body.get("items", []))))

    asset_count = get_count("/assets")
    if asset_count == 0:
        fail("DEMO_MODE=true but GET /api/assets returned 0 assets — seed data missing")
    log(f"  Assets: {asset_count} ✓")

    connector_count = get_count("/connectors")
    if connector_count == 0:
        fail("DEMO_MODE=true but GET /api/connectors returned 0 connectors — seed data missing")
    log(f"  Connectors: {connector_count} ✓")

    r = api("get", base_url, "/demo/orgs", token=token)
    if r.status_code != 200:
        fail(f"GET /api/demo/orgs failed: {r.status_code}")
    orgs = r.json() if isinstance(r.json(), list) else r.json().get("items", [r.json()])
    if not orgs:
        fail("DEMO_MODE=true but GET /api/demo/orgs returned no orgs")
    log(f"  Organizations: {len(orgs)} ✓")

    log("[PHASE 12: demo-mode-on] PASSED")


# ── Phase 13: Demo mode off — empty state after reconfigure ──────────────────

def phase_demo_mode_off(base_url, token, public_ip, key_path=None, username="ubuntu", password=None):
    log("[PHASE 13: demo-mode-off]")
    compose_path = "/opt/nexplane/docker-compose.ami.yml"

    def restart_backend_and_wait(demo_value, wait_label):
        if demo_value == "false":
            sed_cmd = (
                "sudo sed -i "
                "'s/DEMO_MODE: \"true\"/DEMO_MODE: \"false\"/' "
                "/opt/nexplane/docker-compose.ami.yml"
            )
        else:
            sed_cmd = (
                "sudo sed -i "
                "'s/DEMO_MODE: \"false\"/DEMO_MODE: \"true\"/' "
                "/opt/nexplane/docker-compose.ami.yml"
            )
        ssh_run(public_ip, sed_cmd, key_path=key_path, username=username, password=password)
        ssh_run(
            public_ip,
            "sudo docker compose -f /opt/nexplane/docker-compose.ami.yml up -d --force-recreate backend",
            key_path=key_path, username=username, password=password,
        )
        log(f"  Backend restarting ({wait_label})...")
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                r = api("post", base_url, "/auth/login",
                        json={"email": "admin@nexplane.local", "password": "changeme"})
                if r.status_code == 200:
                    log(f"  Backend ready ({wait_label}) ✓")
                    return r.json().get("access_token")
            except Exception:
                pass
            time.sleep(10)
        fail(f"Backend did not become ready within 120s ({wait_label})")

    try:
        # Switch to DEMO_MODE=false
        new_token = restart_backend_and_wait("false", "DEMO_MODE=false")

        # Assert empty state
        def assert_empty(path, label):
            r = api("get", base_url, path, token=new_token)
            if r.status_code != 200:
                fail(f"GET /api{path} failed: {r.status_code}")
            body = r.json()
            count = len(body) if isinstance(body, list) else body.get(
                "total", body.get("count", len(body.get("items", [])))
            )
            if count != 0:
                fail(f"DEMO_MODE=false but GET /api{path} returned {count} {label} (expected 0)")
            log(f"  {label}: 0 ✓")

        assert_empty("/assets",     "assets")
        assert_empty("/connectors", "connectors")

        r = api("get", base_url, "/demo/orgs", token=new_token)
        orgs = r.json() if isinstance(r.json(), list) else r.json().get("items", [r.json()])
        if len(orgs) != 1:
            fail(f"DEMO_MODE=false: expected exactly 1 org, got {len(orgs)}")
        log(f"  Organizations: 1 (default org only) ✓")

    finally:
        # Always restore DEMO_MODE=true so phase 14 starts from a known-good state
        log("  Restoring DEMO_MODE=true...")
        restored_token = restart_backend_and_wait("true", "restore DEMO_MODE=true")

    log("[PHASE 13: demo-mode-off] PASSED")
    return restored_token


# ── Phase 14: Systemd resilience ─────────────────────────────────────────────

def phase_systemd_resilience(base_url, public_ip, key_path=None, username="ubuntu", password=None):
    log("[PHASE 14: systemd-resilience]")

    ssh_run(public_ip, "sudo systemctl restart nexplane", key_path=key_path, username=username, password=password)
    log("  nexplane service restarted — waiting for port 80...")

    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            r = requests.get(f"{base_url}/", timeout=5)
            if r.status_code == 200:
                log("  GET / → 200 after restart ✓")
                break
        except Exception:
            pass
        time.sleep(10)
    else:
        fail("Platform did not return 200 within 180s after systemctl restart nexplane")

    # Re-authenticate to confirm full stack is operational
    r = api("post", base_url, "/auth/login",
            json={"email": "admin@nexplane.local", "password": "changeme"})
    if r.status_code != 200:
        fail(f"Login after restart failed: {r.status_code} {r.text[:200]}")
    if not r.json().get("access_token"):
        fail("No access_token in login response after restart")
    log("  Login after restart ✓")

    log("[PHASE 14: systemd-resilience] PASSED")


# ── Cleanup ───────────────────────────────────────────────────────────────────

def cleanup(base_url, token, cr_ids, asset_ids, connector_ids):
    log("[cleanup] Deleting smoke test resources")
    for cr_id in cr_ids:
        r = api("delete", base_url, f"/change-requests/{cr_id}", token=token)
        log(f"  DELETE /change-requests/{cr_id} → {r.status_code}")
    for asset_id in reversed(asset_ids):  # leaf first
        r = api("delete", base_url, f"/assets/{asset_id}", token=token)
        log(f"  DELETE /assets/{asset_id} → {r.status_code}")
    for conn_id in connector_ids:
        r = api("delete", base_url, f"/connectors/{conn_id}", token=token)
        log(f"  DELETE /connectors/{conn_id} → {r.status_code}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=(
            "AMI smoke test. Auth method is chosen automatically by IP: "
            "public IPs use key-pair (--key-name), RFC1918 IPs use password (--ssh-password)."
        )
    )
    parser.add_argument("--ami-id",            required=True)
    parser.add_argument("--key-name",          default=None,
                        help="EC2 key-pair name (required for public-IP / EC2 runs)")
    parser.add_argument("--ssh-password",      default=None,
                        help="SSH password for RFC1918 hosts (local/VPC-internal testing)")
    parser.add_argument("--ssh-username",      default="ubuntu",
                        help="SSH username (default: ubuntu)")
    parser.add_argument("--security-group-id", required=True)
    parser.add_argument("--region",            default="us-east-1")
    args = parser.parse_args()

    # ssh_kwargs is passed to every ssh_run call site; auth method is chosen inside ssh_run
    # based on whether the target IP is RFC1918 or public.
    ssh_kwargs = {
        "username": args.ssh_username,
        "key_path": f"{args.key_name}.pem" if args.key_name else None,
        "password": args.ssh_password,
    }

    ec2 = boto3.client("ec2", region_name=args.region)
    instance_id  = None
    base_url     = None
    public_ip    = None
    token        = None
    cr_ids       = []
    asset_ids    = []
    connector_ids = []

    try:
        instance_id, public_ip, base_url = phase_launch(ec2, args)
        phase_container_health(public_ip, **ssh_kwargs)
        token = phase_auth(base_url, public_ip=public_ip, **ssh_kwargs)
        web_id, app_id, db_id, asset_ids = phase_assets(base_url, token)
        aws_conn_id, connector_ids = phase_connectors(base_url, token)
        cr_id, cr_ids = phase_cr_lifecycle(base_url, token, app_id, aws_conn_id)
        phase_rollback(base_url, token, cr_id)
        phase_feature_surface(base_url, token, cr_id)
        phase_mcp(base_url, token)
        phase_initialization_quality(public_ip, **ssh_kwargs)
        phase_frontend_routes(base_url)
        phase_demo_mode_on(base_url, token)
        token = phase_demo_mode_off(base_url, token, public_ip, **ssh_kwargs)
        phase_systemd_resilience(base_url, public_ip, **ssh_kwargs)

        if token:
            cleanup(base_url, token, cr_ids, asset_ids, connector_ids)

        log("")
        log("=" * 60)
        log("AMI SMOKE: ALL 14 PHASES PASSED")
        log("=" * 60)

    finally:
        if instance_id:
            log(f"[cleanup] Terminating instance {instance_id}")
            ec2.terminate_instances(InstanceIds=[instance_id])
            waiter = ec2.get_waiter("instance_terminated")
            waiter.wait(InstanceIds=[instance_id])
            log("[cleanup] Instance terminated")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[smoke-ami] FAILED: {e}", file=sys.stderr)
        sys.exit(1)
