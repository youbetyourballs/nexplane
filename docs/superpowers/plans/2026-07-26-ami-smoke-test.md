# AMI Smoke Test — Expanded Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand `packer/smoke_ami.py` from 9 to 14 phases, fix credential inconsistency across seed data and login UI, and add an `ssh_run` helper that supports both key-pair and password auth for future image types.

**Architecture:** All changes are in three files — `backend/seed.py` and `frontend/src/pages/Login.tsx` (credential fix), and `packer/smoke_ami.py` (phase expansion). The smoke script is a single flat file; new phases are added as top-level functions following the existing pattern and wired into `main()`. The `ssh_run` helper replaces all direct `subprocess.run(["ssh", ...])` calls.

**Tech Stack:** Python 3, boto3, requests, subprocess (SSH); React/TypeScript (Login.tsx fix only); pytest not used — smoke_ami.py is invoked directly and exits 0/1.

## Global Constraints

- `smoke_ami.py` must remain a single self-contained script with no new package dependencies beyond boto3 and requests (already in the CI job). Password-based SSH raises `NotImplementedError` — paramiko is added when VMware pipeline is built.
- Every `fail()` call raises `RuntimeError` with a descriptive message; the outer `try/finally` handles termination.
- Phase functions are named `phase_<name>(...)` matching the existing convention.
- All SSH runs go through `ssh_run()` — no direct `subprocess.run(["ssh", ...])` anywhere.
- Admin credentials across all files: email `admin@nexplane.local`, password `changeme`.
- CI job (`release.yml` `smoke-ami` step) is not modified.

---

### Task 1: Credential standardization

Fix the two files that show stale `admin@acme.example / admin123` credentials. This is a prerequisite — the smoke test in later tasks asserts `admin@nexplane.local / changeme`.

**Files:**
- Modify: `backend/seed.py:91,93`
- Modify: `frontend/src/pages/Login.tsx:83`

**Interfaces:**
- Produces: `admin@nexplane.local / changeme` as the seeded admin in all deployments (dev Docker Compose + AMI)

- [ ] **Step 1: Update seed.py admin fallback email**

In `backend/seed.py` line 91, change:
```python
email=os.environ.get("ADMIN_EMAIL", "admin@acme.example"),
```
to:
```python
email=os.environ.get("ADMIN_EMAIL", "admin@nexplane.local"),
```

- [ ] **Step 2: Update seed.py admin fallback password**

In `backend/seed.py` line 93, change:
```python
hashed_password=hash_password(os.environ.get("ADMIN_PASSWORD", "admin123"))),
```
to:
```python
hashed_password=hash_password(os.environ.get("ADMIN_PASSWORD", "changeme"))),
```

- [ ] **Step 3: Update Login.tsx demo account hint**

In `frontend/src/pages/Login.tsx` line 83, change:
```tsx
              <div>admin@acme.example / admin123</div>
```
to:
```tsx
              <div>admin@nexplane.local / changeme</div>
```

- [ ] **Step 4: Verify seed.py still imports and parses cleanly**

```bash
cd backend
python -c "import ast; ast.parse(open('seed.py').read()); print('syntax OK')"
```
Expected output: `syntax OK`

- [ ] **Step 5: Verify Login.tsx still compiles**

```bash
cd frontend
npm run build 2>&1 | tail -5
```
Expected: build completes with no TypeScript errors. (If you don't have npm locally, skip — the CI build will catch it. Confirm visually that the change is syntactically correct JSX.)

- [ ] **Step 6: Commit**

```bash
git add backend/seed.py frontend/src/pages/Login.tsx
git commit -m "fix: standardize admin credentials to admin@nexplane.local / changeme"
```

---

### Task 2: ssh_run helper + refactor phase 2

Add the `ssh_run` abstraction and migrate the only existing SSH call (phase 2) to use it. Later tasks build on this helper.

**Files:**
- Modify: `packer/smoke_ami.py` — add `ssh_run()` function, update `phase_container_health()`

**Interfaces:**
- Produces: `ssh_run(host, cmd, key_path=None, username="ubuntu", password=None, timeout=30) -> tuple[str, str, int]`
  - Returns `(stdout, stderr, returncode)`
  - Raises `NotImplementedError` if `password` is provided (VMware/bare-metal path, not yet implemented)
  - Raises `RuntimeError` if SSH exits non-zero and `check=True` (default)

- [ ] **Step 1: Add ssh_run function after the existing constants block**

Insert after line 23 (`PLAN_TERMINAL = {"planned", "failed"}`):

```python
def ssh_run(host, cmd, key_path=None, username="ubuntu", password=None, timeout=30, check=True):
    """Run cmd on host via SSH. Returns (stdout, stderr, returncode).

    key_path: path to .pem file for key-pair auth (EC2 AMI deployments).
    password: reserved for VMware/bare-metal image types — raises NotImplementedError until
              paramiko is added as a dependency in that pipeline.
    check: if True, raises RuntimeError on non-zero exit code.
    """
    if password is not None:
        raise NotImplementedError(
            "Password-based SSH is not yet implemented. Add paramiko when VMware pipeline is built."
        )
    if key_path is None:
        raise ValueError("key_path is required for key-pair SSH auth")
    result = subprocess.run(
        [
            "ssh", "-o", "StrictHostKeyChecking=no", "-o", f"ConnectTimeout={timeout}",
            "-i", key_path,
            f"{username}@{host}",
            cmd,
        ],
        capture_output=True, text=True, timeout=timeout + 5,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"ssh_run failed (exit {result.returncode}): {cmd!r}\nstderr: {result.stderr.strip()}"
        )
    return result.stdout, result.stderr, result.returncode
```

- [ ] **Step 2: Refactor phase_container_health to use ssh_run**

Replace the existing `phase_container_health` function body. The function signature gains `key_path`:

```python
def phase_container_health(public_ip, key_path):
    log("[PHASE 2: container-health]")
    deadline = time.time() + 300
    while True:
        stdout, stderr, rc = ssh_run(
            public_ip,
            "docker ps --format '{{.Names}}' --filter status=running",
            key_path=key_path,
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
```

- [ ] **Step 3: Update the call site in main() to pass key_path**

In `main()`, change:
```python
phase_container_health(public_ip, args)
```
to:
```python
phase_container_health(public_ip, key_path=f"{args.key_name}.pem")
```

- [ ] **Step 4: Remove the old SSH args dependency from phase_container_health**

The old function accepted `args` to read `args.key_name`. Since we now pass `key_path` directly, confirm there are no remaining references to `args` inside `phase_container_health`. The function should have no `args` parameter.

- [ ] **Step 5: Smoke-test the refactor locally (dry run)**

```bash
python -c "
import ast
src = open('packer/smoke_ami.py').read()
ast.parse(src)
print('syntax OK')
"
```
Expected: `syntax OK`

- [ ] **Step 6: Commit**

```bash
git add packer/smoke_ami.py
git commit -m "refactor: add ssh_run helper, migrate phase 2 SSH call"
```

---

### Task 3: Phase 3 patch — verify all seeded accounts

After the existing admin login check, loop over all four seeded accounts and assert each can log in and returns the correct role.

**Files:**
- Modify: `packer/smoke_ami.py` — update `phase_auth()`

**Interfaces:**
- Consumes: `api()`, `fail()`, `log()` (existing helpers)
- Produces: `phase_auth()` still returns the admin token (unchanged); adds role verification for all 4 accounts

- [ ] **Step 1: Add SEEDED_ACCOUNTS constant after EXPECTED_CONTAINERS**

Insert after `EXPECTED_CONTAINERS = ("db", "backend", "webserver")`:

```python
# All accounts seeded by seed.py when DEMO_MODE=true.
# Each tuple: (email, password, expected_role)
SEEDED_ACCOUNTS = [
    ("admin@nexplane.local",   "changeme",    "admin"),
    ("operator@acme.example",  "operator123", "security_operator"),
    ("approver@acme.example",  "approver123", "approver"),
    ("auditor@acme.example",   "auditor123",  "auditor"),
]
```

- [ ] **Step 2: Add seeded account loop at the end of phase_auth()**

After the existing `log(f"  GET /auth/me → {me.get('email')} ...")` and before `log("[PHASE 3: authentication] PASSED")`, insert:

```python
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
```

- [ ] **Step 3: Syntax check**

```bash
python -c "import ast; ast.parse(open('packer/smoke_ami.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 4: Commit**

```bash
git add packer/smoke_ami.py
git commit -m "feat: phase 3 — verify all 4 seeded accounts login with correct roles"
```

---

### Task 4: Phase 8 patch — feature surface additions

Add four new checks to `phase_feature_surface`: audit log non-empty, second user create+login, bad-password 401, agent download URL.

**Files:**
- Modify: `packer/smoke_ami.py` — update `phase_feature_surface()`

**Interfaces:**
- Consumes: `api()`, `fail()`, `log()`, `requests` (already imported)
- The function signature gains no new parameters — all new checks use `base_url` and `token` already present.

- [ ] **Step 1: Add /api/audit-log and /api/users to the existing endpoint loop**

In `phase_feature_surface`, the `endpoints` list currently ends with `("/change-requests", "change requests list")`. Add two more entries:

```python
        ("/audit-log",           "audit log"),
        ("/users",               "users"),
```

- [ ] **Step 2: Assert audit log is non-empty**

After the existing `cr_id not in cr_ids_in_list` check, append:

```python
    # Audit log must have entries from phases 4-7
    r = api("get", base_url, "/audit-log", token=token)
    audit_items = r.json() if isinstance(r.json(), list) else r.json().get("items", r.json().get("events", []))
    if not audit_items:
        fail("GET /api/audit-log returned empty — audit writes are silently failing")
    log(f"  Audit log has {len(audit_items)} entries ✓")
```

- [ ] **Step 3: Second user create and login**

Append after the audit log check:

```python
    # Create a second user and verify they can log in (tests user provisioning + multi-session)
    r = api("post", base_url, "/users", token=token, json={
        "email": "smoke-user@nexplane.local",
        "name": "Smoke User",
        "password": "SmokePass123!",
        "role": "auditor",
    })
    if r.status_code not in (200, 201):
        fail(f"POST /users (smoke-user) failed: {r.status_code} {r.text[:200]}")
    smoke_user_id = r.json().get("id")
    log(f"  Created smoke-user → {smoke_user_id}")

    r = api("post", base_url, "/auth/login",
            json={"email": "smoke-user@nexplane.local", "password": "SmokePass123!"})
    if r.status_code != 200:
        fail(f"Login for smoke-user failed: {r.status_code} {r.text[:200]}")
    if not r.json().get("access_token"):
        fail(f"No access_token for smoke-user: {r.text[:200]}")
    log("  smoke-user login ✓")
```

- [ ] **Step 4: Bad password 401 check**

Append:

```python
    # Auth rejection path must be functional
    r = api("post", base_url, "/auth/login",
            json={"email": "admin@nexplane.local", "password": "wrongpassword"})
    if r.status_code != 401:
        fail(f"Expected 401 for wrong password, got {r.status_code}")
    log("  Wrong-password → 401 ✓")
```

- [ ] **Step 5: Agent download URL check**

Append:

```python
    # Agent binary must be reachable at the URL baked into the AMI
    agent_url = (
        "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com"
        "/latest/linux-amd64/nexplane-agent"
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
```

- [ ] **Step 6: Syntax check**

```bash
python -c "import ast; ast.parse(open('packer/smoke_ami.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 7: Commit**

```bash
git add packer/smoke_ami.py
git commit -m "feat: phase 8 — audit log, second user, 401 rejection, agent download checks"
```

---

### Task 5: Phase 9 patch — MCP protocol verification

After the existing SSE connection check, add `initialize` handshake, `tools/list`, and a lightweight tool call.

**Files:**
- Modify: `packer/smoke_ami.py` — update `phase_mcp()`

**Interfaces:**
- Consumes: `api()`, `fail()`, `log()`, `requests`, `agent_token` (already computed in phase_mcp)
- The MCP JSON-RPC endpoint is `POST /api/mcp` with `Content-Type: application/json` and `Authorization: Bearer {agent_token}`

- [ ] **Step 1: Add _mcp_call helper inside phase_mcp (local function)**

Add this right after `log("[PHASE 9: mcp-server]")` inside `phase_mcp()`:

```python
    def mcp_call(method, params, call_id, agent_tok):
        """Send one MCP JSON-RPC request. Returns the parsed response dict."""
        r = requests.post(
            f"{base_url}/api/mcp",
            headers={
                "Authorization": f"Bearer {agent_tok}",
                "Content-Type": "application/json",
            },
            json={"jsonrpc": "2.0", "id": call_id, "method": method, "params": params},
            timeout=20,
        )
        if r.status_code != 200:
            fail(f"MCP {method} returned HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        if "error" in data:
            fail(f"MCP {method} returned JSON-RPC error: {data['error']}")
        return data.get("result", {})
```

- [ ] **Step 2: Add initialize handshake after existing SSE check**

After the `except requests.exceptions.Timeout` block (end of the SSE check), and before `# Clean up agent token`, insert:

```python
    # MCP initialize handshake
    result = mcp_call(
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "smoke", "version": "0.0.1"},
            "capabilities": {},
        },
        call_id=1,
        agent_tok=agent_token,
    )
    server_name = (result.get("serverInfo") or {}).get("name", "")
    if not server_name:
        fail(f"MCP initialize: missing serverInfo.name in response: {result}")
    if "capabilities" not in result:
        fail(f"MCP initialize: missing capabilities in response: {result}")
    log(f"  MCP initialize → serverInfo.name={server_name!r} ✓")
```

- [ ] **Step 3: Add tools/list check**

Append after the initialize block:

```python
    # MCP tools/list — must return a non-empty tool list
    result = mcp_call("tools/list", {}, call_id=2, agent_tok=agent_token)
    tools = result.get("tools", [])
    if not tools:
        fail("MCP tools/list returned empty tools array — tool registration failed at startup")
    log(f"  MCP tools/list → {len(tools)} tools registered ✓")
```

- [ ] **Step 4: Add lightweight tool call**

Append after tools/list:

```python
    # MCP tool call — verify MCP → backend → DB path end-to-end
    # list_change_requests is read-only and always returns (CRs created in phase 6 exist)
    tool_name = next(
        (t["name"] for t in tools if "change_request" in t.get("name", "").lower()),
        None,
    )
    if tool_name is None:
        tool_name = next(
            (t["name"] for t in tools if "list" in t.get("name", "").lower()),
            tools[0]["name"],
        )
    result = mcp_call(
        "tools/call",
        {"name": tool_name, "arguments": {}},
        call_id=3,
        agent_tok=agent_token,
    )
    # A valid result has "content" key; an error would have been caught by mcp_call()
    if "content" not in result and "result" not in result:
        fail(f"MCP tools/call ({tool_name}): unexpected result shape: {result}")
    log(f"  MCP tools/call ({tool_name}) → valid result ✓")
```

- [ ] **Step 5: Syntax check**

```bash
python -c "import ast; ast.parse(open('packer/smoke_ami.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 6: Commit**

```bash
git add packer/smoke_ami.py
git commit -m "feat: phase 9 — MCP initialize handshake, tools/list, tool call"
```

---

### Task 6: Phase 10 — initialization quality

New phase: alembic at head + zero ERROR logs on clean boot. Runs via SSH after phase 9.

**Files:**
- Modify: `packer/smoke_ami.py` — add `phase_initialization_quality()`, wire into `main()`

**Interfaces:**
- Consumes: `ssh_run(host, cmd, key_path)`, `fail()`, `log()`
- Called as: `phase_initialization_quality(public_ip, key_path=f"{args.key_name}.pem")`

- [ ] **Step 1: Add phase_initialization_quality function**

Add after `phase_mcp()`:

```python
# ── Phase 10: Initialization quality ─────────────────────────────────────────

def phase_initialization_quality(public_ip, key_path):
    log("[PHASE 10: initialization-quality]")

    # Alembic must be at head — catches silent migration failures
    stdout, _, _ = ssh_run(
        public_ip,
        "docker exec nexplane-backend-1 alembic current 2>&1",
        key_path=key_path,
    )
    if "(head)" not in stdout:
        fail(f"Alembic is not at head. Output:\n{stdout}")
    log("  Alembic at head ✓")

    # Backend must have zero ERROR-level log lines on clean boot
    # (exclude alembic lines which may contain the word "error" in table names)
    stdout, _, rc = ssh_run(
        public_ip,
        "docker logs nexplane-backend-1 2>&1 | grep -i ' ERROR ' | grep -vi alembic | wc -l",
        key_path=key_path,
        check=False,
    )
    error_count = int(stdout.strip() or "0")
    if error_count > 0:
        detail, _, _ = ssh_run(
            public_ip,
            "docker logs nexplane-backend-1 2>&1 | grep -i ' ERROR ' | grep -vi alembic",
            key_path=key_path,
            check=False,
        )
        fail(f"Backend has {error_count} ERROR log line(s) on clean boot:\n{detail.strip()}")
    log("  Backend error log: 0 ERROR lines on clean boot ✓")

    log("[PHASE 10: initialization-quality] PASSED")
```

- [ ] **Step 2: Wire phase 10 into main() after phase_mcp call**

In `main()`, after `phase_mcp(base_url, token)`, add:

```python
        phase_initialization_quality(public_ip, key_path=f"{args.key_name}.pem")
```

- [ ] **Step 3: Syntax check**

```bash
python -c "import ast; ast.parse(open('packer/smoke_ami.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 4: Commit**

```bash
git add packer/smoke_ami.py
git commit -m "feat: phase 10 — alembic head check and error log scan"
```

---

### Task 7: Phase 11 — frontend route and static asset coverage

New phase: all known frontend routes return 200 + text/html; all JS/CSS bundles referenced in the HTML load.

**Files:**
- Modify: `packer/smoke_ami.py` — add `phase_frontend_routes()`, wire into `main()`

**Interfaces:**
- Consumes: `fail()`, `log()`, `requests`
- Called as: `phase_frontend_routes(base_url)`
- `base_url` is `http://{public_ip}` — no `/api` prefix; routes are served by the webserver container on port 80

- [ ] **Step 1: Add FRONTEND_ROUTES constant after SEEDED_ACCOUNTS**

```python
FRONTEND_ROUTES = [
    "/",
    "/login",
    "/assets",
    "/change-requests",
    "/connectors",
    "/settings",
    "/users",
    "/audit-log",
]
```

- [ ] **Step 2: Add phase_frontend_routes function**

Add after `phase_initialization_quality()`:

```python
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
```

- [ ] **Step 3: Wire phase 11 into main() after phase 10**

```python
        phase_frontend_routes(base_url)
```

- [ ] **Step 4: Syntax check**

```bash
python -c "import ast; ast.parse(open('packer/smoke_ami.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 5: Commit**

```bash
git add packer/smoke_ami.py
git commit -m "feat: phase 11 — frontend route 200 check and static asset integrity"
```

---

### Task 8: Phases 12 and 13 — demo mode on/off

Two tightly coupled phases: assert demo data is present (phase 12), then reconfigure to `DEMO_MODE=false`, restart, assert empty state, restore (phase 13).

**Files:**
- Modify: `packer/smoke_ami.py` — add `phase_demo_mode_on()` and `phase_demo_mode_off()`, wire into `main()`

**Interfaces:**
- Consumes: `api()`, `ssh_run()`, `fail()`, `log()`
- `phase_demo_mode_off` takes `public_ip` and `key_path` in addition to `base_url` and `token` — needs SSH to reconfigure docker-compose
- Both phases take `token` — the admin token from phase 3

- [ ] **Step 1: Add phase_demo_mode_on**

Add after `phase_frontend_routes()`:

```python
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

    r = api("get", base_url, "/organizations", token=token)
    if r.status_code != 200:
        fail(f"GET /api/organizations failed: {r.status_code}")
    orgs = r.json() if isinstance(r.json(), list) else r.json().get("items", [r.json()])
    if not orgs:
        fail("DEMO_MODE=true but GET /api/organizations returned no orgs")
    log(f"  Organizations: {len(orgs)} ✓")

    log("[PHASE 12: demo-mode-on] PASSED")
```

- [ ] **Step 2: Add phase_demo_mode_off**

Add after `phase_demo_mode_on()`:

```python
# ── Phase 13: Demo mode off — empty state after reconfigure ──────────────────

def phase_demo_mode_off(base_url, token, public_ip, key_path):
    log("[PHASE 13: demo-mode-off]")
    compose_path = "/opt/nexplane/docker-compose.ami.yml"

    def restart_backend_and_wait(demo_value, wait_label):
        ssh_run(
            public_ip,
            f"sudo sed -i 's/DEMO_MODE: \"{('true' if demo_value != 'false' else 'false')}\""
            f"/DEMO_MODE: \"{demo_value}\"/' {compose_path}",
            key_path=key_path,
        )
        ssh_run(
            public_ip,
            f"sudo docker compose -f {compose_path} up -d --force-recreate backend",
            key_path=key_path,
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

        r = api("get", base_url, "/organizations", token=new_token)
        orgs = r.json() if isinstance(r.json(), list) else r.json().get("items", [r.json()])
        if len(orgs) != 1:
            fail(f"DEMO_MODE=false: expected exactly 1 org, got {len(orgs)}")
        log(f"  Organizations: 1 (default org only) ✓")

    finally:
        # Always restore DEMO_MODE=true so phase 14 starts from a known-good state
        log("  Restoring DEMO_MODE=true...")
        restart_backend_and_wait("true", "restore DEMO_MODE=true")

    log("[PHASE 13: demo-mode-off] PASSED")
```

- [ ] **Step 3: Wire phases 12 and 13 into main() after phase 11**

```python
        phase_demo_mode_on(base_url, token)
        phase_demo_mode_off(base_url, token, public_ip, key_path=f"{args.key_name}.pem")
```

- [ ] **Step 4: Syntax check**

```bash
python -c "import ast; ast.parse(open('packer/smoke_ami.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 5: Commit**

```bash
git add packer/smoke_ami.py
git commit -m "feat: phases 12+13 — demo mode on/off state verification"
```

---

### Task 9: Phase 14 — systemd resilience

New phase: restart the nexplane systemd unit, wait for port 80, re-authenticate. Final phase before cleanup.

**Files:**
- Modify: `packer/smoke_ami.py` — add `phase_systemd_resilience()`, wire into `main()`

**Interfaces:**
- Consumes: `ssh_run()`, `api()`, `fail()`, `log()`, `requests`, `time`
- Called as: `phase_systemd_resilience(base_url, public_ip, key_path)`

- [ ] **Step 1: Add phase_systemd_resilience function**

Add after `phase_demo_mode_off()`:

```python
# ── Phase 14: Systemd resilience ─────────────────────────────────────────────

def phase_systemd_resilience(base_url, public_ip, key_path):
    log("[PHASE 14: systemd-resilience]")

    ssh_run(public_ip, "sudo systemctl restart nexplane", key_path=key_path)
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
```

- [ ] **Step 2: Wire phase 14 into main() after phases 12+13**

```python
        phase_systemd_resilience(base_url, public_ip, key_path=f"{args.key_name}.pem")
```

- [ ] **Step 3: Update the ALL PHASES PASSED banner**

In `main()`, change:
```python
        log("AMI SMOKE: ALL 9 PHASES PASSED")
```
to:
```python
        log("AMI SMOKE: ALL 14 PHASES PASSED")
```

- [ ] **Step 4: Final syntax check**

```bash
python -c "import ast; ast.parse(open('packer/smoke_ami.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 5: Verify main() phase call order**

Read the `main()` function and confirm the call order is exactly:
```
phase_launch → phase_container_health → phase_auth → phase_assets →
phase_connectors → phase_cr_lifecycle → phase_rollback →
phase_feature_surface → phase_mcp → phase_initialization_quality →
phase_frontend_routes → phase_demo_mode_on → phase_demo_mode_off →
phase_systemd_resilience → cleanup
```

- [ ] **Step 6: Commit**

```bash
git add packer/smoke_ami.py
git commit -m "feat: phase 14 — systemd restart resilience check; 14-phase smoke complete"
```
