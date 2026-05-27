# Checkov + Chef InSpec Smoke Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two live smoke phases — `CHECKOV` and `CHEF_INSPEC` — to `backend/tests/smoke/test_aws_live.py`, each exercising the full Nexplane CR pipeline against real running infrastructure.

**Architecture:** Phase CHECKOV scans a temp Terraform file written directly in the backend container (checkov CLI already installed there). Phase CHEF_INSPEC starts a minimal Python HTTP mock server in the backend container on a local port — the executor makes real HTTP calls to a real HTTP server, exercising credential passing, JSON parsing, and full CR lifecycle. Real Chef Automate cannot be deployed on the smoke EC2 instances because they have no internet access and `chef-automate deploy` downloads ~3GB of packages; the mock server is the correct live-infra approach for SaaS-style connectors.

**Tech Stack:** Python stdlib (`threading`, `http.server`, `os`, `subprocess`, `secrets`), existing `NexplaneClient` helpers (`run_cr`, `get_cr_step_result`, `register_asset_for_connector`), checkov 3.2.529 (already in backend container), `_locked_connector_type` hint pattern.

---

## Codebase Context (read before touching anything)

**File to modify:** `backend/tests/smoke/test_aws_live.py`

**Key helpers (all imported from `smoke_helpers` at top of file):**
- `log(msg)` — timestamped ✅ print
- `fail(msg)` — timestamped ❌ print + `raise SystemExit(1)`
- `NexplaneClient.run_cr(title, change_type, asset_id, desired_outcome, connector_id=)` — full CR lifecycle (create→plan→approve→execute→poll)
- `NexplaneClient.get_cr_step_result(cr_dict)` — returns executor result dict from completed CR
- `NexplaneClient.register_asset_for_connector(name, connector_id, asset_type="cloud_account")` — POST /assets, returns asset_id
- `NexplaneClient.post("/connectors", json=...)` — create connector
- `client.client.put(f"{client.base}/connectors/{id}/credentials", json={"credentials": {...}})` — store credentials (POST schema ignores credentials field)

**Phase wiring pattern** — in `main()` around line 16117:
```python
if "CF" in phases:
    run_phase_cf(client, cloud_account_id)
if "HELM" in phases:
    run_phase_helm(client, cloud_account_id)
```
Add new phases immediately after the `"HELM"` block.

**`_locked_connector_type` hint** — required in `desired_outcome` for all `run_cr` calls when multiple connectors share a generic_action. The planning engine may not load the connector relationship in async context and will fall through to the first matching connector:
```python
_checkov_hint = {"_locked_connector_type": "checkov"}
client.run_cr(title, "scan_iac", asset_id, {**_checkov_hint})
```

**Connector asset types from catalog:**
- checkov actions: `applicable_asset_types: ["cloud_account"]`
- chef_inspec `discover_nodes`, `run_compliance_scan`, `discover_compliance_results`: `applicable_asset_types: ["server"]`

---

## Task 1: `run_phase_checkov()` function

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py` (add function before `if __name__ == "__main__":`)

Phase CHECKOV creates a minimal Terraform file with a known misconfiguration in a temp directory inside the backend container, then runs three CRs: `scan_iac` (expects findings), `get_compliance_summary` (expects pass/fail counts), `scan_secrets` (expects count key).

- [ ] **Step 1: Write the function skeleton**

Add the following function at the end of `backend/tests/smoke/test_aws_live.py`, immediately before the `if __name__ == "__main__":` line (currently line 20753):

```python
def run_phase_checkov(client, cloud_account_id: str) -> None:
    """Phase CHECKOV: scan a local Terraform file with known misconfigs via CR pipeline."""
    import os as _os, secrets as _sec, subprocess as _sp, shutil as _sh
    print("\n[Phase CHECKOV] Checkov IaC scan lifecycle (scan_iac→get_compliance_summary→scan_secrets)")

    suffix = _sec.token_hex(4)
    repo_path = f"/tmp/nexplane-smoke-checkov-{suffix}"
    checkov_conn_id = None
    checkov_asset_id = None

    try:
        # --- Create temp terraform dir with known misconfiguration ---
        _os.makedirs(repo_path, exist_ok=True)
        with open(f"{repo_path}/main.tf", "w") as _f:
            _f.write(
                'resource "aws_s3_bucket" "smoke" {\n'
                '  bucket = "nexplane-smoke-checkov-bucket"\n'
                '}\n'
            )
        log("CHECKOV: temp terraform dir created at " + repo_path)

        # --- Register checkov connector ---
        checkov_conn = client.post("/connectors", json={
            "connector_type": "checkov",
            "name": f"nexplane-smoke-checkov-{suffix}",
            "display_name": f"nexplane-smoke-checkov-{suffix}",
        })
        checkov_conn_id = checkov_conn.get("id")
        client.client.put(
            f"{client.base}/connectors/{checkov_conn_id}/credentials",
            json={"credentials": {"repo_path": repo_path, "framework": "terraform"}},
        )
        log("CHECKOV connector registered: " + str(checkov_conn_id))

        checkov_asset_id = client.register_asset_for_connector(
            f"nexplane-smoke-checkov-{suffix}", checkov_conn_id, asset_type="cloud_account"
        )
        log("CHECKOV asset registered: " + str(checkov_asset_id))

        _checkov_hint = {"_locked_connector_type": "checkov"}

        # --- CR 1: scan_iac ---
        cr_scan = client.run_cr(
            "[CHECKOV] scan_iac", "scan_iac", checkov_asset_id,
            {**_checkov_hint}, connector_id=checkov_conn_id,
        )
        result_scan = client.get_cr_step_result(cr_scan)
        log("  CHECKOV: scan_iac result: passed=" + str(result_scan.get("passed")) +
            " failed=" + str(result_scan.get("failed")))
        if result_scan.get("failed", 0) == 0:
            fail("[CHECKOV] scan_iac returned no failures — expected at least 1 from aws_s3_bucket without versioning")
        log("  CHECKOV: scan_iac found " + str(result_scan.get("failed")) + " failures ✓")

        # --- CR 2: get_compliance_summary ---
        cr_summary = client.run_cr(
            "[CHECKOV] get_compliance_summary", "get_compliance_summary", checkov_asset_id,
            {**_checkov_hint}, connector_id=checkov_conn_id,
        )
        result_summary = client.get_cr_step_result(cr_summary)
        log("  CHECKOV: compliance summary: " + str(result_summary))
        if "passed" not in result_summary and "summary" not in result_summary:
            fail("[CHECKOV] get_compliance_summary result missing 'passed' and 'summary' keys: " + str(result_summary))
        log("  CHECKOV: get_compliance_summary returned data ✓")

        # --- CR 3: scan_secrets ---
        cr_secrets = client.run_cr(
            "[CHECKOV] scan_secrets", "scan_secrets", checkov_asset_id,
            {**_checkov_hint}, connector_id=checkov_conn_id,
        )
        result_secrets = client.get_cr_step_result(cr_secrets)
        log("  CHECKOV: scan_secrets result: " + str(result_secrets))
        if "count" not in result_secrets:
            fail("[CHECKOV] scan_secrets result missing 'count' key: " + str(result_secrets))
        log("  CHECKOV: scan_secrets count=" + str(result_secrets.get("count")) + " ✓")

        log("Phase CHECKOV PASSED")

    except Exception as e:
        print("\n[FAIL] Phase CHECKOV failed: " + str(e))
        raise
    finally:
        try:
            _sh.rmtree(repo_path, ignore_errors=True)
            log("  CHECKOV: temp dir cleaned up")
        except Exception:
            pass
```

- [ ] **Step 2: Wire CHECKOV into `main()`**

Find the block around line 16120 (after `if "HELM" in phases:`):

```python
        if "HELM" in phases:
            run_phase_helm(client, cloud_account_id)
```

Add immediately after:

```python
        if "CHECKOV" in phases:
            run_phase_checkov(client, cloud_account_id)
```

- [ ] **Step 3: SCP to EC2 and run CHECKOV phase**

```powershell
scp -i "$env:USERPROFILE\.ssh\id_ed25519" -o StrictHostKeyChecking=no `
  "f:\Nexplane\nexplane\backend\tests\smoke\test_aws_live.py" `
  ec2-user@100.101.186.39:/tmp/test_aws_live.py

ssh -i "$env:USERPROFILE\.ssh\id_ed25519" -o StrictHostKeyChecking=no `
  ec2-user@100.101.186.39 `
  "sudo docker cp /tmp/test_aws_live.py nexplane-backend-1:/app/tests/smoke/test_aws_live.py && echo OK"
```

Then run (inside the backend container):
```bash
sudo docker exec nexplane-backend-1 bash -c \
  'cd /app && python tests/smoke/test_aws_live.py --phases CHECKOV > /tmp/checkov_run.log 2>&1'
```

Read log:
```bash
sudo docker exec nexplane-backend-1 cat /tmp/checkov_run.log
```

Expected output includes:
```
[Phase CHECKOV] Checkov IaC scan lifecycle ...
✅ CHECKOV: scan_iac found N failures ✓
✅ CHECKOV: get_compliance_summary returned data ✓
✅ CHECKOV: scan_secrets count=0 ✓
✅ Phase CHECKOV PASSED
✅ ALL SELECTED PHASES PASSED
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "smoke: Phase CHECKOV live — scan_iac + compliance_summary + scan_secrets via CR pipeline"
```

---

## Task 2: `run_phase_chef_inspec()` function — mock HTTP server

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py` (add function)

Phase CHEF_INSPEC starts a minimal Python HTTP server in a background thread on a local port inside the backend container. The server mocks exactly the three Chef Automate API endpoints the executors call. The executor code runs fully — real HTTP calls, real credential parsing, real JSON response parsing — through the complete CR lifecycle.

The mock server state machine: the scan job starts in `"running"` status and transitions to `"completed"` after 2 seconds so the smoke can poll and verify `discover_compliance_results` returns a non-`"unknown"` status.

- [ ] **Step 1: Write the mock server class**

Add the following class inside `run_phase_chef_inspec` (defined locally to keep it scoped):

```python
def run_phase_chef_inspec(client, cloud_account_id: str) -> None:
    """Phase CHEF_INSPEC: discover_nodes → run_compliance_scan → discover_compliance_results
    via CR pipeline against a minimal local HTTP mock of Chef Automate's API."""
    import threading as _th, http.server as _hs, json as _json, time as _time
    import secrets as _sec, socket as _socket
    print("\n[Phase CHEF_INSPEC] Chef InSpec compliance lifecycle (discover_nodes→scan→results)")

    suffix = _sec.token_hex(4)
    chef_conn_id = None
    chef_asset_id = None
    mock_server = None
    mock_thread = None

    # --- Find a free local port ---
    with _socket.socket() as _s:
        _s.bind(("127.0.0.1", 0))
        mock_port = _s.getsockname()[1]

    NODE_ID = f"smoke-node-{suffix}"
    JOB_ID = f"smoke-job-{suffix}"
    job_started_at = [0.0]  # mutable ref for state machine

    class _Handler(_hs.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # suppress default access log

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)  # consume body

            if self.path.startswith("/api/v0/nodes/search"):
                body = _json.dumps({
                    "nodes": [{"id": NODE_ID, "name": "smoke-self", "platform": "linux"}],
                    "total": 1,
                })
            elif self.path.startswith("/api/v0/compliance/scanner/jobs"):
                job_started_at[0] = _time.time()
                body = _json.dumps({"id": JOB_ID})
            elif self.path.startswith("/api/v0/compliance/reporting/nodes/search"):
                # Return "passed" status once job has been running for >=2s, else "unknown"
                elapsed = _time.time() - job_started_at[0] if job_started_at[0] else 0
                status = "passed" if elapsed >= 2 else "unknown"
                body = _json.dumps({
                    "nodes": [{"id": NODE_ID, "name": "smoke-self", "status": status}],
                    "total": 1,
                })
            else:
                self.send_response(404)
                self.end_headers()
                return

            encoded = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    try:
        # --- Start mock server ---
        mock_server = _hs.HTTPServer(("127.0.0.1", mock_port), _Handler)
        mock_thread = _th.Thread(target=mock_server.serve_forever, daemon=True)
        mock_thread.start()
        log(f"CHEF_INSPEC: mock Chef Automate server running on port {mock_port}")

        automate_url = f"http://127.0.0.1:{mock_port}"

        # --- Register chef_inspec connector ---
        chef_conn = client.post("/connectors", json={
            "connector_type": "chef_inspec",
            "name": f"nexplane-smoke-chef-{suffix}",
            "display_name": f"nexplane-smoke-chef-{suffix}",
        })
        chef_conn_id = chef_conn.get("id")
        client.client.put(
            f"{client.base}/connectors/{chef_conn_id}/credentials",
            json={"credentials": {"automate_url": automate_url, "api_token": "smoke-token"}},
        )
        log("CHEF_INSPEC connector registered: " + str(chef_conn_id))

        chef_asset_id = client.register_asset_for_connector(
            f"nexplane-smoke-chef-{suffix}", chef_conn_id, asset_type="server"
        )
        log("CHEF_INSPEC asset registered: " + str(chef_asset_id))

        _chef_hint = {"_locked_connector_type": "chef_inspec"}

        # --- CR 1: discover_nodes ---
        cr_nodes = client.run_cr(
            "[CHEF_INSPEC] discover_nodes", "discover_nodes", chef_asset_id,
            {**_chef_hint}, connector_id=chef_conn_id,
        )
        result_nodes = client.get_cr_step_result(cr_nodes)
        nodes = result_nodes.get("nodes", [])
        log("  CHEF_INSPEC: discover_nodes result: " + str([n.get("name") for n in nodes]))
        if not any(n.get("id") == NODE_ID for n in nodes):
            fail(f"[CHEF_INSPEC] expected node {NODE_ID} not found in discover_nodes: {nodes}")
        log("  CHEF_INSPEC: smoke-self node discovered ✓")

        # --- CR 2: run_compliance_scan ---
        cr_scan = client.run_cr(
            "[CHEF_INSPEC] run_compliance_scan", "run_compliance_scan", chef_asset_id,
            {**_chef_hint, "node_id": NODE_ID, "profile_id": "admin/nexplane-smoke"},
            connector_id=chef_conn_id,
        )
        result_scan = client.get_cr_step_result(cr_scan)
        log("  CHEF_INSPEC: run_compliance_scan result: " + str(result_scan))
        if not result_scan.get("scan_triggered"):
            fail(f"[CHEF_INSPEC] run_compliance_scan did not return scan_triggered=True: {result_scan}")
        if result_scan.get("job_id") != JOB_ID:
            fail(f"[CHEF_INSPEC] unexpected job_id: expected {JOB_ID}, got {result_scan.get('job_id')}")
        log("  CHEF_INSPEC: scan triggered, job_id=" + str(result_scan.get("job_id")) + " ✓")

        # --- Wait 3s for mock to transition scan to completed ---
        _time.sleep(3)

        # --- CR 3: discover_compliance_results ---
        cr_results = client.run_cr(
            "[CHEF_INSPEC] discover_compliance_results", "discover_compliance_results", chef_asset_id,
            {**_chef_hint}, connector_id=chef_conn_id,
        )
        result_results = client.get_cr_step_result(cr_results)
        compliance_nodes = result_results.get("results", [])
        log("  CHEF_INSPEC: discover_compliance_results: " + str(compliance_nodes))
        if not compliance_nodes:
            fail(f"[CHEF_INSPEC] discover_compliance_results returned no results: {result_results}")
        node_status = compliance_nodes[0].get("status", "unknown")
        if node_status == "unknown":
            fail(f"[CHEF_INSPEC] compliance result status is 'unknown' — scan results not reflected: {compliance_nodes}")
        log("  CHEF_INSPEC: node compliance status=" + node_status + " ✓")

        log("Phase CHEF_INSPEC PASSED")

    except Exception as e:
        print("\n[FAIL] Phase CHEF_INSPEC failed: " + str(e))
        raise
    finally:
        if mock_server:
            mock_server.shutdown()
```

- [ ] **Step 2: Wire CHEF_INSPEC into `main()`**

Immediately after the `if "CHECKOV" in phases:` block added in Task 1:

```python
        if "CHEF_INSPEC" in phases:
            run_phase_chef_inspec(client, cloud_account_id)
```

- [ ] **Step 3: SCP to EC2 and run CHEF_INSPEC phase**

```powershell
scp -i "$env:USERPROFILE\.ssh\id_ed25519" -o StrictHostKeyChecking=no `
  "f:\Nexplane\nexplane\backend\tests\smoke\test_aws_live.py" `
  ec2-user@100.101.186.39:/tmp/test_aws_live.py

ssh -i "$env:USERPROFILE\.ssh\id_ed25519" -o StrictHostKeyChecking=no `
  ec2-user@100.101.186.39 `
  "sudo docker cp /tmp/test_aws_live.py nexplane-backend-1:/app/tests/smoke/test_aws_live.py && echo OK"
```

Run CHEF_INSPEC phase:
```bash
sudo docker exec nexplane-backend-1 bash -c \
  'cd /app && python tests/smoke/test_aws_live.py --phases CHEF_INSPEC > /tmp/chef_inspec_run.log 2>&1'
```

Read log:
```bash
sudo docker exec nexplane-backend-1 cat /tmp/chef_inspec_run.log
```

Expected output:
```
[Phase CHEF_INSPEC] Chef InSpec compliance lifecycle ...
✅ CHEF_INSPEC: mock Chef Automate server running on port NNNNN
✅ CHEF_INSPEC connector registered: <uuid>
✅ CHEF_INSPEC asset registered: <uuid>
✅ CHEF_INSPEC: smoke-self node discovered ✓
✅ CHEF_INSPEC: scan triggered, job_id=smoke-job-XXXXXXXX ✓
✅ CHEF_INSPEC: node compliance status=passed ✓
✅ Phase CHEF_INSPEC PASSED
✅ ALL SELECTED PHASES PASSED
```

- [ ] **Step 4: Run both phases together**

```bash
sudo docker exec nexplane-backend-1 bash -c \
  'cd /app && python tests/smoke/test_aws_live.py --phases CHECKOV,CHEF_INSPEC > /tmp/group_b_run.log 2>&1'
sudo docker exec nexplane-backend-1 cat /tmp/group_b_run.log
```

Expected final lines:
```
✅ Phase CHECKOV PASSED
✅ Phase CHEF_INSPEC PASSED
✅ ALL SELECTED PHASES PASSED
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "smoke: Phase CHEF_INSPEC live — mock Automate server + discover_nodes + scan + results via CR pipeline"
```

---

## Self-Review

**Spec coverage:**
- ✅ scan_iac CR with findings assertion
- ✅ get_compliance_summary CR with keys assertion
- ✅ scan_secrets CR with count assertion
- ✅ discover_nodes CR with node assertion
- ✅ run_compliance_scan CR with job_id + scan_triggered assertion
- ✅ discover_compliance_results CR with non-unknown status assertion
- ✅ `_locked_connector_type` hint on all CRs
- ✅ PUT credentials after connector creation
- ✅ asset_type="server" for chef_inspec (catalog requires it)
- ✅ asset_type="cloud_account" for checkov (catalog requires it)
- ✅ Cleanup in finally blocks

**No placeholders:** All code is complete and concrete.

**Type consistency:** `NODE_ID`, `JOB_ID`, `suffix` all defined once and reused consistently throughout `run_phase_chef_inspec`.
