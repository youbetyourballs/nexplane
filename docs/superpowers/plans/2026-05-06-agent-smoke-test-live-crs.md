# Agent Smoke Test — Live CR Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace SSM-based agent command verification with Nexplane agent CRs across all six tracks (AWS/GCP/Azure × Linux/Windows), run each OS group in parallel via ThreadPoolExecutor, and make agent registration mandatory (no SSM fallback).

**Architecture:** Two sub-projects sharing executor files. Sub-project 1 (Linux): extend `gcp/launch_instance.py` and `azure/launch_vm.py` to support Tailscale in startup scripts, add parallel worker functions for all three Linux tracks, rewrite `main()` for parallel Linux dispatch. Sub-project 2 (Windows): extend same executor files for Windows images + PowerShell scripts, add parallel worker functions for all three Windows tracks, extend `main()` for parallel Windows dispatch.

**Tech Stack:** Python 3.12, concurrent.futures.ThreadPoolExecutor, boto3, google-cloud-compute, azure-mgmt-compute, existing smoke test infrastructure in `backend/tests/smoke/`

---

## Files

**Sub-project 1 — Modify:**
- `backend/app/connectors/executors/gcp/launch_instance.py` — add `tailscale_auth_key` param to startup script
- `backend/app/connectors/executors/azure/launch_vm.py` — add `tailscale_auth_key` param to startup script
- `backend/tests/smoke/test_agent_live.py` — add parallel workers + helpers, rewrite main()

**Sub-project 2 — Modify (same files):**
- `backend/app/connectors/executors/gcp/launch_instance.py` — add Windows image + PowerShell startup
- `backend/app/connectors/executors/azure/launch_vm.py` — add Windows image + PowerShell script extension
- `backend/tests/smoke/test_agent_live.py` — add Windows workers, extend main()

---

## Sub-project 1: Linux Tracks

---

### Task 1: Extend GCP executor to embed Tailscale in Linux startup script

**Files:**
- Modify: `backend/app/connectors/executors/gcp/launch_instance.py`

The current executor builds a startup script from `_AGENT_STARTUP_TEMPLATE` when `connection_mode == "agent_startup"`. When `tailscale_auth_key` is provided, prepend Tailscale installation so the agent can reach the backend through the tailnet.

- [ ] **Step 1: Read the file**

Read `backend/app/connectors/executors/gcp/launch_instance.py` lines 1-100 to see `_AGENT_STARTUP_TEMPLATE` and the startup script construction at line 90-95.

- [ ] **Step 2: Add `tailscale_auth_key` parameter and modify startup script construction**

In `execute()`, add `tailscale_auth_key = parameters.get("tailscale_auth_key", "")` alongside the other parameter reads.

Replace lines 91-94 (the startup script construction block inside the `if connection_mode == "agent_startup":` block):

```python
    if connection_mode == "agent_startup":
        tailscale_section = ""
        if tailscale_auth_key:
            tailscale_section = f"""# Install and join Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey="{tailscale_auth_key}" --hostname="{name}" --accept-routes
"""
        startup_script = parameters.get(
            "startup_script",
            tailscale_section + _AGENT_STARTUP_TEMPLATE.format(
                nexplane_url=nexplane_url, nexplane_secret=nexplane_secret
            ),
        )
        metadata_items = [compute_v1.Items(key="startup-script", value=startup_script)]
        tags_list = list(network_tags)
```

- [ ] **Step 3: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/gcp/launch_instance.py
git commit -m "feat(agent-smoke): add tailscale_auth_key support to GCP launch_instance startup script"
```

---

### Task 2: Extend Azure executor to embed Tailscale in Linux startup script

**Files:**
- Modify: `backend/app/connectors/executors/azure/launch_vm.py`

The `_AGENT_STARTUP_SCRIPT` is a bash script template used when `connection_mode == "agent_extension"`. When `tailscale_auth_key` is provided, prepend Tailscale installation.

- [ ] **Step 1: Read the file**

Read `backend/app/connectors/executors/azure/launch_vm.py` lines 1-50 to see `_AGENT_STARTUP_SCRIPT` and how it's used.

- [ ] **Step 2: Add `tailscale_auth_key` to `execute()` and modify script construction**

In `execute()`, add `tailscale_auth_key = parameters.get("tailscale_auth_key", "")` alongside other parameter reads.

Find where `startup_script = _AGENT_STARTUP_SCRIPT.format(...)` is called and replace it:

```python
        startup_script = _AGENT_STARTUP_SCRIPT.format(
            nexplane_url=nexplane_url,
            nexplane_secret=nexplane_secret,
        )
        if tailscale_auth_key and connection_mode == "agent_extension":
            tailscale_prepend = f"""#!/bin/bash
set -e
# Install and join Tailscale first so agent can reach control plane
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey="{tailscale_auth_key}" --hostname="{vm_name}" --accept-routes
"""
            # Remove the leading #!/bin/bash from agent script to avoid duplicate shebang
            agent_body = startup_script.lstrip()
            if agent_body.startswith("#!/bin/bash"):
                agent_body = agent_body[len("#!/bin/bash"):].lstrip()
            startup_script = tailscale_prepend + agent_body
```

- [ ] **Step 3: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/azure/launch_vm.py
git commit -m "feat(agent-smoke): add tailscale_auth_key support to Azure launch_vm agent_extension script"
```

---

### Task 3: Add shared Linux helpers to test_agent_live.py

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

Add `_poll_for_endpoint()` (mandatory registration, fails on timeout) and `_run_all_linux_agent_crs()` (runs all 12 Linux agent CR groups). These replace the SSM fallback pattern.

- [ ] **Step 1: Add imports**

Read `backend/tests/smoke/test_agent_live.py` lines 1-55 to see current imports.

Add `import concurrent.futures` to the imports block.

- [ ] **Step 2: Add `_poll_for_endpoint()` helper**

After the existing `_agent_cr()` helper function (around line 75), add:

```python
def _poll_for_endpoint(client: NexplaneClient, hostname: str, timeout: int) -> dict:
    """Poll for endpoint asset registration. Fails if agent does not register within timeout."""
    print(f"  Waiting up to {timeout}s for agent '{hostname}' to register...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        candidates = client.get("/assets", params={"q": hostname, "asset_type": "endpoint"})
        if candidates:
            log(f"Agent registered: {candidates[0]['id']}")
            return candidates[0]
        time.sleep(15)
    fail(f"Agent '{hostname}' did not register within {timeout}s — aborting (no SSM fallback)")
    return {}  # unreachable
```

- [ ] **Step 3: Add `_run_all_linux_agent_crs()` helper**

After `_poll_for_endpoint()`, add:

```python
_LINUX_AGENT_CRS = [
    "agent_linux_patch", "agent_ossecurity", "agent_linuxauth",
    "agent_crossplatform", "agent_compliance", "agent_forensics",
    "agent_fleet", "agent_backup", "agent_reboot", "agent_credrotation",
    "agent_iac", "agent_linuxupgrade",
]


def _run_all_linux_agent_crs(client: NexplaneClient, endpoint_asset_id: str,
                              label: str) -> None:
    """Run all 12 Linux agent command groups via Nexplane CRs against the endpoint asset."""
    for change_type in _LINUX_AGENT_CRS:
        short = change_type.replace("agent_", "")
        _agent_cr(client, endpoint_asset_id, f"{short}-{label}", change_type)
```

- [ ] **Step 4: Add `_collect_results()` helper**

After `_run_all_linux_agent_crs()`, add:

```python
def _collect_results(futures: dict) -> list:
    """Collect results from a dict of {future: label}. Returns list of result dicts."""
    results = []
    for future in concurrent.futures.as_completed(futures):
        label = futures[future]
        try:
            result = future.result()
        except Exception as e:
            result = {"track": label, "passed": False, "error": str(e)}
        results.append(result)
    return results
```

- [ ] **Step 5: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 6: Verify parse**

```bash
docker exec nexplane-backend-1 python -c "
import sys; sys.path.insert(0, '/app/tests/smoke')
import test_agent_live
print('_poll_for_endpoint:', test_agent_live._poll_for_endpoint)
print('_run_all_linux_agent_crs:', test_agent_live._run_all_linux_agent_crs)
print('OK')
"
```

- [ ] **Step 7: Commit**

```bash
git add backend/tests/smoke/test_agent_live.py
git commit -m "feat(agent-smoke): add _poll_for_endpoint, _run_all_linux_agent_crs, _collect_results helpers"
```

---

### Task 4: Add AWS Linux worker function

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

Add `run_aws_linux_worker()` — CR-only, mandatory registration, per-provider rollback. This replaces `run_aws_linux_track()` in the parallel path (the old function stays for backward compat).

- [ ] **Step 1: Add `run_aws_linux_worker()` after the existing `run_aws_linux_track()` function (around line 499)**

```python
def run_aws_linux_worker(base_url: str, email: str, password: str,
                          backend_ip: str, tailscale_auth_key: str) -> dict:
    """AWS Linux agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "aws-linux", "passed": False, "error": None}
    instance_name = "nexplane-agent-smoke-linux-aws"
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n[aws-linux] Starting worker")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("aws")
        agent_secret = client.get_agent_secret()

        client.run_cr(
            "[aws-linux] create key pair", "key_pair_create", cloud_account_id,
            {"key_name": "nexplane-agent-smoke-key"},
        )
        client.run_cr(
            "[aws-linux] launch EC2", "ec2_launch", cloud_account_id,
            {"mode": "quick", "name": instance_name, "os": "amazon_linux",
             "iam_instance_profile": "NexplaneEC2TestProfile",
             "key_name": "nexplane-agent-smoke-key",
             "rollback_strategy": "terminate_instance"},
        )
        time.sleep(10)

        instance_asset = client.get_asset_by_name(instance_name)
        if not instance_asset:
            fail("[aws-linux] EC2 instance not in inventory")
        instance_id = instance_asset["asset_metadata"]["instance_id"]
        log(f"[aws-linux] EC2: {instance_id}")

        print("  [aws-linux] Waiting 3 min for SSM...")
        time.sleep(180)

        client.run_cr(
            "[aws-linux] SSM whoami", "ssm_command", instance_asset["id"],
            {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
             "command": "whoami", "rollback_strategy": "rollback_unavailable"},
        )
        client.run_cr(
            "[aws-linux] tailscale join", "tailscale_join", instance_asset["id"],
            {"instance_id": instance_id, "auth_key": tailscale_auth_key,
             "hostname": "nexplane-agent-smoke-aws-linux"},
        )
        client.run_cr(
            "[aws-linux] deploy agent", "deploy_nexplane_agent", instance_asset["id"],
            {"instance_id": instance_id, "nexplane_url": nexplane_url,
             "nexplane_secret": agent_secret},
        )

        # MANDATORY — fails if agent doesn't register (no SSM fallback)
        endpoint_asset = _poll_for_endpoint(
            client, "nexplane-agent-smoke-aws-linux", timeout=180)

        _run_all_linux_agent_crs(client, endpoint_asset["id"], "aws-linux")
        result["passed"] = True
        log("[aws-linux] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [aws-linux] Failed: {e}")
    finally:
        _teardown_aws_linux_instance(client)

    return result
```

- [ ] **Step 2: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_agent_live.py
git commit -m "feat(agent-smoke): add run_aws_linux_worker — CR-only, mandatory registration"
```

---

### Task 5: Add GCP Linux worker function

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

Add `run_gcp_linux_worker()` and `_teardown_gcp_linux()`. Replaces `run_gcp_linux_track()` stub.

- [ ] **Step 1: Add `_teardown_gcp_linux()` helper**

After `_teardown_aws_linux_instance()` (around line 199), add:

```python
def _teardown_gcp_linux(client: NexplaneClient, instance_name: str, gcp_project: str) -> None:
    """Delete GCE Linux instance and clean up inventory."""
    print(f"\n  [gcp-linux teardown] {instance_name}")
    try:
        from smoke_helpers import _get_gcp_compute_client, GCE_ZONE
        compute = _get_gcp_compute_client()
        if compute and gcp_project:
            compute.delete(project=gcp_project, zone=GCE_ZONE, instance=instance_name)
            print(f"  Safety net: deleted GCE {instance_name}")
    except Exception as e:
        print(f"  ⚠️  GCP teardown error: {e}")
    try:
        assets = client.get("/assets", params={"q": instance_name})
        for asset in assets:
            if instance_name in asset.get("name", ""):
                client.client.delete(f"{client.base}/assets/{asset['id']}")
                print(f"  Deleted inventory asset {asset['name']}")
    except Exception:
        pass
```

- [ ] **Step 2: Add `run_gcp_linux_worker()` after `run_aws_linux_worker()`**

```python
def run_gcp_linux_worker(base_url: str, email: str, password: str,
                          backend_ip: str, tailscale_auth_key: str,
                          gcp_project: str) -> dict:
    """GCP Linux agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "gcp-linux", "passed": False, "error": None}
    instance_name = f"nexplane-agent-smoke-lx-gcp-{secrets.token_hex(3)}"
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n[gcp-linux] Starting worker: {instance_name}")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("gcp")
        agent_secret = client.get_agent_secret()

        # Launch GCE — startup script installs Tailscale then agent
        client._run_cr_with_timeout(
            "[gcp-linux] launch GCE instance", "gce_instance_create", cloud_account_id,
            {"name": instance_name, "machine_type": "e2-micro", "zone": "us-central1-a",
             "image_family": "ubuntu-2204-lts", "image_project": "ubuntu-os-cloud",
             "connection_mode": "agent_startup", "nexplane_url": nexplane_url,
             "nexplane_secret": agent_secret, "tailscale_auth_key": tailscale_auth_key},
            timeout=300,
        )
        log(f"[gcp-linux] GCE instance launched: {instance_name}")

        # 6 min — startup script runs during boot
        endpoint_asset = _poll_for_endpoint(client, instance_name, timeout=360)

        _run_all_linux_agent_crs(client, endpoint_asset["id"], "gcp-linux")
        result["passed"] = True
        log("[gcp-linux] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [gcp-linux] Failed: {e}")
    finally:
        _teardown_gcp_linux(client, instance_name, gcp_project)

    return result
```

- [ ] **Step 3: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_agent_live.py
git commit -m "feat(agent-smoke): add run_gcp_linux_worker and _teardown_gcp_linux"
```

---

### Task 6: Add Azure Linux worker function

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

Add `run_azure_linux_worker()` and `_teardown_azure_linux()`. Replaces `run_azure_linux_track()` stub.

- [ ] **Step 1: Add `_teardown_azure_linux()` helper after `_teardown_gcp_linux()`**

```python
def _teardown_azure_linux(client: NexplaneClient, vm_name: str,
                           azure_resource_group: str) -> None:
    """Delete Azure Linux VM and clean up inventory."""
    print(f"\n  [azure-linux teardown] {vm_name}")
    try:
        from smoke_helpers import _get_azure_compute_client, _azure_creds_cache
        _get_azure_compute_client()  # populate cache
        creds = _azure_creds_cache
        if creds:
            from azure.identity import ClientSecretCredential
            from azure.mgmt.compute import ComputeManagementClient
            credential = ClientSecretCredential(
                tenant_id=creds["tenant_id"], client_id=creds["client_id"],
                client_secret=creds["client_secret"],
            )
            compute = ComputeManagementClient(credential, creds["subscription_id"])
            compute.virtual_machines.begin_delete(azure_resource_group, vm_name).result()
            print(f"  Safety net: deleted Azure VM {vm_name}")
    except Exception as e:
        print(f"  ⚠️  Azure teardown error: {e}")
    try:
        assets = client.get("/assets", params={"q": vm_name})
        for asset in assets:
            if vm_name in asset.get("name", ""):
                client.client.delete(f"{client.base}/assets/{asset['id']}")
                print(f"  Deleted inventory asset {asset['name']}")
    except Exception:
        pass
```

- [ ] **Step 2: Add `run_azure_linux_worker()` after `run_gcp_linux_worker()`**

```python
def run_azure_linux_worker(base_url: str, email: str, password: str,
                            backend_ip: str, tailscale_auth_key: str,
                            azure_resource_group: str) -> dict:
    """Azure Linux agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "azure-linux", "passed": False, "error": None}
    vm_name = f"nexplane-agent-smoke-lx-az-{secrets.token_hex(3)}"
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n[azure-linux] Starting worker: {vm_name}")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("azure")
        agent_secret = client.get_agent_secret()

        # Launch Azure VM — Custom Script Extension installs Tailscale then agent
        client._run_cr_with_timeout(
            "[azure-linux] launch Azure VM", "azure_vm_create", cloud_account_id,
            {"vm_name": vm_name, "resource_group": azure_resource_group,
             "location": "eastus", "vm_size": "Standard_B1s",
             "connection_mode": "agent_extension", "nexplane_url": nexplane_url,
             "nexplane_secret": agent_secret, "tailscale_auth_key": tailscale_auth_key},
            timeout=600,
        )
        log(f"[azure-linux] Azure VM launched: {vm_name}")

        # 8 min — Custom Script Extension can be slow
        endpoint_asset = _poll_for_endpoint(client, vm_name, timeout=480)

        _run_all_linux_agent_crs(client, endpoint_asset["id"], "azure-linux")
        result["passed"] = True
        log("[azure-linux] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [azure-linux] Failed: {e}")
    finally:
        _teardown_azure_linux(client, vm_name, azure_resource_group)

    return result
```

- [ ] **Step 3: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_agent_live.py
git commit -m "feat(agent-smoke): add run_azure_linux_worker and _teardown_azure_linux"
```

---

### Task 7: Rewrite main() for parallel Linux dispatch

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

Replace the sequential Linux track calls in `main()` with a parallel `ThreadPoolExecutor` dispatch. `setup_backend_tailscale()` is called once before the workers start, `teardown_backend_tailscale()` once after all complete.

- [ ] **Step 1: Replace the Linux dispatch block in `main()`**

Read the current `main()` (lines 750-839). Find this block:

```python
    try:
        if run_linux:
            if run_aws:
                run_aws_linux_track(client, cloud_account_id, args.tailscale_auth_key, phases)
            if run_gcp:
                run_gcp_linux_track(client, cloud_account_id, args.tailscale_auth_key,
                                     args.gcp_project, phases)
            if run_azure:
                run_azure_linux_track(client, cloud_account_id, args.tailscale_auth_key,
                                       args.azure_resource_group, phases)
```

Replace the entire `try/except/finally` block with:

```python
    backend_ip = setup_backend_tailscale(args.tailscale_auth_key) if args.tailscale_auth_key else ""
    all_results = []

    try:
        if run_linux:
            print("\n" + "=" * 60)
            print("Running Linux tracks in parallel")
            print("=" * 60)
            linux_futures: dict = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                if run_aws:
                    linux_futures[executor.submit(
                        run_aws_linux_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, args.tailscale_auth_key,
                    )] = "aws-linux"
                if run_gcp:
                    if not args.gcp_project:
                        fail("--gcp-project required for GCP Linux track")
                    linux_futures[executor.submit(
                        run_gcp_linux_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, args.tailscale_auth_key, args.gcp_project,
                    )] = "gcp-linux"
                if run_azure:
                    if not args.azure_resource_group:
                        fail("--azure-resource-group required for Azure Linux track")
                    linux_futures[executor.submit(
                        run_azure_linux_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, args.tailscale_auth_key, args.azure_resource_group,
                    )] = "azure-linux"
                all_results.extend(_collect_results(linux_futures))

        if run_windows:
            # Windows workers added in Sub-project 2
            print("\n  ⚠️  Windows parallel workers not yet implemented (Sub-project 2)")
            if run_aws:
                run_aws_windows_track(client, cloud_account_id, args.tailscale_auth_key, phases)
            if run_gcp:
                run_gcp_windows_track(client, cloud_account_id, args.tailscale_auth_key,
                                       args.gcp_project, phases)
            if run_azure:
                run_azure_windows_track(client, cloud_account_id, args.tailscale_auth_key,
                                         args.azure_resource_group, phases)

    finally:
        if args.tailscale_auth_key:
            teardown_backend_tailscale()

    # Print consolidated report
    print("\n" + "=" * 60)
    print("AGENT SMOKE TEST RESULTS")
    print("=" * 60)
    passed = True
    for result in sorted(all_results, key=lambda r: r["track"]):
        if result["passed"]:
            print(f"  ✅ {result['track'].upper()}: PASSED")
        else:
            print(f"  ❌ {result['track'].upper()}: FAILED — {result['error']}")
            passed = False

    if not all_results:
        # Windows-only or sequential run — use old behavior
        passed = True

    print("=" * 60)
    if not passed:
        print("\n❌ AGENT SMOKE TEST FAILED")
        import sys as _sys
        _sys.exit(1)
    else:
        print("✅ ALL TRACKS PASSED")
```

- [ ] **Step 2: Remove the duplicate `client = NexplaneClient(...)` and `cloud_account_id` lines from main() that are now unused by the Linux parallel path**

Keep the `client` and `cloud_account_id` for now since Windows tracks still use them.

- [ ] **Step 3: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

- [ ] **Step 4: Verify parse**

```bash
docker exec nexplane-backend-1 python -c "
import sys; sys.path.insert(0, '/app/tests/smoke')
import test_agent_live
print('run_aws_linux_worker:', test_agent_live.run_aws_linux_worker)
print('run_gcp_linux_worker:', test_agent_live.run_gcp_linux_worker)
print('run_azure_linux_worker:', test_agent_live.run_azure_linux_worker)
print('OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_agent_live.py
git commit -m "feat(agent-smoke): rewrite main() for parallel Linux track dispatch via ThreadPoolExecutor"
```

---

## Sub-project 2: Windows Tracks

---

### Task 8: Extend GCP executor for Windows instances

**Files:**
- Modify: `backend/app/connectors/executors/gcp/launch_instance.py`

Add `os` parameter support. When `os == "windows"`, switch to Windows Server 2022 image and inject the PowerShell startup script via `windows-startup-script-ps1` metadata key.

- [ ] **Step 1: Add Windows PowerShell startup script template at the top of the file**

After `_AGENT_STARTUP_TEMPLATE`, add:

```python
_WINDOWS_AGENT_PS1 = r"""# Install Tailscale for Windows
$tsInstaller = "$env:TEMP\tailscale-setup.exe"
Invoke-WebRequest -Uri "https://pkgs.tailscale.com/stable/tailscale-setup.exe" `
  -OutFile $tsInstaller -UseBasicParsing
Start-Process $tsInstaller -Args "/S" -Wait
& "C:\Program Files\Tailscale\tailscale.exe" up `
  --authkey="{tailscale_auth_key}" --hostname="{hostname}" --accept-routes

# Download and install Nexplane agent
$version = (Invoke-WebRequest `
  "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version" `
  -UseBasicParsing).Content.Trim()
$agentUrl = "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-windows-amd64-$version.exe"
Invoke-WebRequest $agentUrl -OutFile "C:\nexplane-agent.exe" -UseBasicParsing

New-Service -Name "NexplaneAgent" `
  -BinaryPathName "C:\nexplane-agent.exe --control-plane {nexplane_url} --secret {nexplane_secret} --mode service" `
  -StartupType Automatic -Description "Nexplane Agent" -ErrorAction SilentlyContinue
Start-Service "NexplaneAgent"
"""
```

- [ ] **Step 2: Add `os_type` parameter and Windows branch in `execute()`**

In `execute()`, add `os_type = parameters.get("os", "linux")` after the existing parameter reads.

Then add the Windows branch before the `if not creds:` block:

```python
    # Override image family/project and machine type for Windows
    if os_type == "windows":
        image_family = parameters.get("image_family", "windows-server-2022-dc")
        image_project = parameters.get("image_project", "windows-cloud")
        machine_type = parameters.get("machine_type", "e2-medium")  # Windows needs ≥4GB RAM
```

In the `if connection_mode == "agent_startup":` block, add a Windows-specific branch:

```python
    if connection_mode == "agent_startup":
        if os_type == "windows":
            # Windows uses windows-startup-script-ps1 metadata key
            ps1_script = _WINDOWS_AGENT_PS1.format(
                tailscale_auth_key=tailscale_auth_key,
                hostname=name,
                nexplane_url=nexplane_url,
                nexplane_secret=nexplane_secret,
            )
            metadata_items = [
                compute_v1.Items(key="windows-startup-script-ps1", value=ps1_script)
            ]
            tags_list = list(network_tags)
        else:
            tailscale_section = ""
            if tailscale_auth_key:
                tailscale_section = f"""# Install and join Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey="{tailscale_auth_key}" --hostname="{name}" --accept-routes
"""
            startup_script = parameters.get(
                "startup_script",
                tailscale_section + _AGENT_STARTUP_TEMPLATE.format(
                    nexplane_url=nexplane_url, nexplane_secret=nexplane_secret
                ),
            )
            metadata_items = [compute_v1.Items(key="startup-script", value=startup_script)]
            tags_list = list(network_tags)
```

- [ ] **Step 3: Update `auto_asset` to include os_type in metadata**

In the `auto_asset` dict, add `"os_type": os_type` to `asset_metadata`.

- [ ] **Step 4: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/gcp/launch_instance.py
git commit -m "feat(agent-smoke): add Windows image + PowerShell startup script support to GCP launch_instance"
```

---

### Task 9: Extend Azure executor for Windows instances

**Files:**
- Modify: `backend/app/connectors/executors/azure/launch_vm.py`

Add `os` parameter support. When `os == "windows"`, switch to Windows Server 2022 image, use admin_password (no SSH), and inject the PowerShell script via Custom Script Extension.

- [ ] **Step 1: Add `_WINDOWS_AGENT_PS1` template at the top of the file**

After `_AGENT_STARTUP_SCRIPT`, add:

```python
_WINDOWS_AGENT_PS1_AZURE = r"""param()
# Install Tailscale
$tsInstaller = "$env:TEMP\tailscale-setup.exe"
Invoke-WebRequest -Uri "https://pkgs.tailscale.com/stable/tailscale-setup.exe" `
  -OutFile $tsInstaller -UseBasicParsing
Start-Process $tsInstaller -Args "/S" -Wait
Start-Sleep -Seconds 10
& "C:\Program Files\Tailscale\tailscale.exe" up `
  --authkey="{tailscale_auth_key}" --hostname="{vm_name}" --accept-routes

# Download and install Nexplane agent
$version = (Invoke-WebRequest `
  "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version" `
  -UseBasicParsing).Content.Trim()
$agentUrl = "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-windows-amd64-$version.exe"
Invoke-WebRequest $agentUrl -OutFile "C:\nexplane-agent.exe" -UseBasicParsing

New-Service -Name "NexplaneAgent" `
  -BinaryPathName "C:\nexplane-agent.exe --control-plane {nexplane_url} --secret {nexplane_secret} --mode service" `
  -StartupType Automatic -Description "Nexplane Agent" -ErrorAction SilentlyContinue
Start-Service "NexplaneAgent"
"""
```

- [ ] **Step 2: Add `os_type` parameter and Windows branch in `execute()`**

In `execute()`, add:
```python
    os_type = parameters.get("os", "linux")
    admin_password = parameters.get("admin_password", "")
```

In the VM creation logic, when `os_type == "windows"`:
- Use `ImageReference(publisher="MicrosoftWindowsServer", offer="WindowsServer", sku="2022-datacenter-smalldisk", version="latest")`
- Use `OsProfile(computer_name=vm_name[:15], admin_username=admin_username, admin_password=admin_password)` (no SSH config)
- Use `vm_size = parameters.get("vm_size", "Standard_B2s")` for Windows

Add the Windows Custom Script Extension branch:

```python
    # Deploy agent via extension (linux or windows)
    if connection_mode == "agent_extension":
        if os_type == "windows":
            import base64 as _base64
            ps1_script = _WINDOWS_AGENT_PS1_AZURE.format(
                tailscale_auth_key=tailscale_auth_key,
                vm_name=vm_name,
                nexplane_url=nexplane_url,
                nexplane_secret=nexplane_secret,
            )
            script_b64 = _base64.b64encode(ps1_script.encode("utf-8")).decode()
            from azure.mgmt.compute.models import VirtualMachineExtension
            await loop.run_in_executor(
                None,
                lambda: compute.virtual_machine_extensions.begin_create_or_update(
                    resource_group, vm_name, "NexplaneAgentInstall",
                    VirtualMachineExtension(
                        location=location,
                        publisher="Microsoft.Compute",
                        type_properties_type="CustomScriptExtension",
                        type_handler_version="1.10",
                        auto_upgrade_minor_version=True,
                        settings={"commandToExecute": f"powershell -EncodedCommand {script_b64}"},
                    ),
                ).result(),
            )
        else:
            # Existing Linux Custom Script Extension path
            ...
```

- [ ] **Step 3: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/azure/launch_vm.py
git commit -m "feat(agent-smoke): add Windows image + PowerShell Custom Script Extension support to Azure launch_vm"
```

---

### Task 10: Add Windows helpers and AWS Windows worker

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

Add `_run_all_windows_agent_crs()`, `_teardown_aws_windows()`, and `run_aws_windows_worker()`.

- [ ] **Step 1: Add `_run_all_windows_agent_crs()` after `_run_all_linux_agent_crs()`**

```python
_WINDOWS_AGENT_CRS = [
    "agent_win_patch", "agent_winharden",
    "agent_crossplatform", "agent_fleet", "agent_reboot",
    "agent_credrotation", "agent_forensics", "agent_backup",
]


def _run_all_windows_agent_crs(client: NexplaneClient, endpoint_asset_id: str,
                                label: str) -> None:
    """Run all 8 Windows-compatible agent command groups via Nexplane CRs."""
    for change_type in _WINDOWS_AGENT_CRS:
        short = change_type.replace("agent_", "")
        _agent_cr(client, endpoint_asset_id, f"{short}-{label}", change_type)
```

- [ ] **Step 2: Add `_teardown_aws_windows()` after the existing Windows teardown code**

```python
def _teardown_aws_windows(client: NexplaneClient) -> None:
    """Terminate Windows EC2 instances and clean up inventory."""
    print("\n  [aws-windows teardown]")
    try:
        ec2_client = _get_aws_boto3_client("ec2")
        if ec2_client:
            reservations = ec2_client.describe_instances(
                Filters=[{"Name": "tag:Name", "Values": ["nexplane-agent-smoke-win*"]},
                         {"Name": "instance-state-name",
                          "Values": ["pending", "running", "stopping", "stopped"]}]
            ).get("Reservations", [])
            for res in reservations:
                for inst in res.get("Instances", []):
                    try:
                        ec2_client.terminate_instances(InstanceIds=[inst["InstanceId"]])
                        print(f"  Terminated {inst['InstanceId']}")
                    except Exception:
                        pass
            kps = ec2_client.describe_key_pairs(
                Filters=[{"Name": "key-name", "Values": ["nexplane-agent-smoke-win*"]}]
            ).get("KeyPairs", [])
            for kp in kps:
                try:
                    ec2_client.delete_key_pair(KeyName=kp["KeyName"])
                except Exception:
                    pass
    except Exception as e:
        print(f"  ⚠️  AWS Windows teardown error: {e}")
    try:
        assets = client.get("/assets", params={"q": "nexplane-agent-smoke-win"})
        for asset in assets:
            if "agent-smoke-win" in asset.get("name", ""):
                try:
                    client.client.delete(f"{client.base}/assets/{asset['id']}")
                    print(f"  Deleted inventory asset {asset['name']}")
                except Exception:
                    pass
    except Exception:
        pass
```

- [ ] **Step 3: Add `run_aws_windows_worker()` — CR-only, mandatory registration**

```python
def run_aws_windows_worker(base_url: str, email: str, password: str,
                            backend_ip: str, tailscale_auth_key: str) -> dict:
    """AWS Windows agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "aws-windows", "passed": False, "error": None}
    instance_name = "nexplane-agent-smoke-win-aws"
    win_key_name = "nexplane-agent-smoke-win-key"
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n[aws-windows] Starting worker")

    ec2_client = _get_aws_boto3_client("ec2")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("aws")
        agent_secret = client.get_agent_secret()

        # Resolve latest Windows Server 2022 AMI
        if not ec2_client:
            fail("[aws-windows] AWS credentials required")
        images = ec2_client.describe_images(
            Owners=["amazon"],
            Filters=[{"Name": "name", "Values": ["Windows_Server-2022-English-Full-Base-*"]},
                     {"Name": "state", "Values": ["available"]}],
        )["Images"]
        if not images:
            fail("[aws-windows] No Windows Server 2022 AMI found")
        win_ami = sorted(images, key=lambda x: x["CreationDate"], reverse=True)[0]["ImageId"]
        log(f"[aws-windows] AMI: {win_ami}")

        client.run_cr(
            "[aws-windows] create key pair", "key_pair_create", cloud_account_id,
            {"key_name": win_key_name},
        )
        client._run_cr_with_timeout(
            "[aws-windows] launch Windows EC2", "ec2_launch", cloud_account_id,
            {"mode": "quick", "name": instance_name, "os": "windows",
             "ami_id": win_ami, "instance_type": "t3.medium",
             "iam_instance_profile": "NexplaneEC2TestProfile",
             "key_name": win_key_name, "rollback_strategy": "terminate_instance"},
            timeout=600,
        )
        time.sleep(10)

        win_asset = client.get_asset_by_name(instance_name)
        if not win_asset:
            fail("[aws-windows] Windows EC2 not in inventory")
        win_id = win_asset["asset_metadata"]["instance_id"]
        log(f"[aws-windows] EC2: {win_id}")

        print("  [aws-windows] Waiting 5 min for Windows SSM agent...")
        time.sleep(300)

        asset_id = win_asset["id"]

        # Install Tailscale via SSM PowerShell
        client._run_cr_with_timeout(
            "[aws-windows] install Tailscale", "ssm_command", asset_id,
            {"instance_id": win_id, "document_name": "AWS-RunPowerShellScript",
             "command": (
                 f"$ts = '$env:TEMP\\ts-setup.exe'; "
                 f"Invoke-WebRequest 'https://pkgs.tailscale.com/stable/tailscale-setup.exe' -OutFile $ts -UseBasicParsing; "
                 f"Start-Process $ts -Args '/S' -Wait; Start-Sleep 10; "
                 f"& 'C:\\Program Files\\Tailscale\\tailscale.exe' up "
                 f"--authkey='{tailscale_auth_key}' --hostname='nexplane-agent-smoke-aws-windows' --accept-routes"
             ),
             "rollback_strategy": "rollback_unavailable"},
            timeout=300,
        )

        # Install Nexplane agent as Windows Service via SSM PowerShell
        client._run_cr_with_timeout(
            "[aws-windows] install agent", "ssm_command", asset_id,
            {"instance_id": win_id, "document_name": "AWS-RunPowerShellScript",
             "command": (
                 f"$v = (Invoke-WebRequest 'https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version' -UseBasicParsing).Content.Trim(); "
                 f"Invoke-WebRequest \"https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-windows-amd64-$v.exe\" -OutFile 'C:\\nexplane-agent.exe' -UseBasicParsing; "
                 f"New-Service -Name 'NexplaneAgent' "
                 f"-BinaryPathName 'C:\\nexplane-agent.exe --control-plane {nexplane_url} --secret {agent_secret} --mode service' "
                 f"-StartupType Automatic -ErrorAction SilentlyContinue; "
                 f"Start-Service 'NexplaneAgent'"
             ),
             "rollback_strategy": "rollback_unavailable"},
            timeout=300,
        )

        # MANDATORY — fails if Windows agent doesn't register (10 min for Windows)
        endpoint_asset = _poll_for_endpoint(
            client, "nexplane-agent-smoke-aws-windows", timeout=600)

        _run_all_windows_agent_crs(client, endpoint_asset["id"], "aws-windows")
        result["passed"] = True
        log("[aws-windows] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [aws-windows] Failed: {e}")
    finally:
        _teardown_aws_windows(client)

    return result
```

- [ ] **Step 4: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_agent_live.py
git commit -m "feat(agent-smoke): add _run_all_windows_agent_crs, run_aws_windows_worker, _teardown_aws_windows"
```

---

### Task 11: Add GCP and Azure Windows workers

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

Add `_teardown_gcp_windows()`, `run_gcp_windows_worker()`, `_teardown_azure_windows()`, `run_azure_windows_worker()`.

- [ ] **Step 1: Add `_teardown_gcp_windows()` and `run_gcp_windows_worker()`**

```python
def _teardown_gcp_windows(client: NexplaneClient, instance_name: str,
                           gcp_project: str) -> None:
    """Delete GCE Windows instance and clean up inventory."""
    print(f"\n  [gcp-windows teardown] {instance_name}")
    try:
        from smoke_helpers import _get_gcp_compute_client, GCE_ZONE
        compute = _get_gcp_compute_client()
        if compute and gcp_project:
            compute.delete(project=gcp_project, zone=GCE_ZONE, instance=instance_name)
            print(f"  Safety net: deleted GCE {instance_name}")
    except Exception as e:
        print(f"  ⚠️  GCP Windows teardown error: {e}")
    try:
        assets = client.get("/assets", params={"q": instance_name})
        for asset in assets:
            if instance_name in asset.get("name", ""):
                client.client.delete(f"{client.base}/assets/{asset['id']}")
                print(f"  Deleted inventory asset {asset['name']}")
    except Exception:
        pass


def run_gcp_windows_worker(base_url: str, email: str, password: str,
                            backend_ip: str, tailscale_auth_key: str,
                            gcp_project: str) -> dict:
    """GCP Windows agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "gcp-windows", "passed": False, "error": None}
    instance_name = f"nexplane-agent-smoke-win-gcp-{secrets.token_hex(3)}"
    nexplane_url = f"http://{backend_ip}:8000"
    print(f"\n[gcp-windows] Starting worker: {instance_name}")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("gcp")
        agent_secret = client.get_agent_secret()

        # Launch Windows GCE — PowerShell startup script installs Tailscale + agent
        client._run_cr_with_timeout(
            "[gcp-windows] launch Windows GCE", "gce_instance_create", cloud_account_id,
            {"name": instance_name, "machine_type": "e2-medium", "zone": "us-central1-a",
             "image_family": "windows-server-2022-dc", "image_project": "windows-cloud",
             "os": "windows", "connection_mode": "agent_startup",
             "nexplane_url": nexplane_url, "nexplane_secret": agent_secret,
             "tailscale_auth_key": tailscale_auth_key},
            timeout=600,
        )
        log(f"[gcp-windows] GCE Windows instance launched: {instance_name}")

        # 10 min — Windows boot + startup script
        endpoint_asset = _poll_for_endpoint(client, instance_name, timeout=600)

        _run_all_windows_agent_crs(client, endpoint_asset["id"], "gcp-windows")
        result["passed"] = True
        log("[gcp-windows] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [gcp-windows] Failed: {e}")
    finally:
        _teardown_gcp_windows(client, instance_name, gcp_project)

    return result
```

- [ ] **Step 2: Add `_teardown_azure_windows()` and `run_azure_windows_worker()`**

```python
def _teardown_azure_windows(client: NexplaneClient, vm_name: str,
                             azure_resource_group: str) -> None:
    """Delete Azure Windows VM and clean up inventory."""
    print(f"\n  [azure-windows teardown] {vm_name}")
    try:
        from smoke_helpers import _get_azure_compute_client, _azure_creds_cache
        _get_azure_compute_client()
        creds = _azure_creds_cache
        if creds:
            from azure.identity import ClientSecretCredential
            from azure.mgmt.compute import ComputeManagementClient
            credential = ClientSecretCredential(
                tenant_id=creds["tenant_id"], client_id=creds["client_id"],
                client_secret=creds["client_secret"],
            )
            compute = ComputeManagementClient(credential, creds["subscription_id"])
            compute.virtual_machines.begin_delete(azure_resource_group, vm_name).result()
            print(f"  Safety net: deleted Azure VM {vm_name}")
    except Exception as e:
        print(f"  ⚠️  Azure Windows teardown error: {e}")
    try:
        assets = client.get("/assets", params={"q": vm_name})
        for asset in assets:
            if vm_name in asset.get("name", ""):
                client.client.delete(f"{client.base}/assets/{asset['id']}")
                print(f"  Deleted inventory asset {asset['name']}")
    except Exception:
        pass


def run_azure_windows_worker(base_url: str, email: str, password: str,
                              backend_ip: str, tailscale_auth_key: str,
                              azure_resource_group: str) -> dict:
    """Azure Windows agent track worker — CR-only, runs in ThreadPoolExecutor."""
    client = NexplaneClient(base_url, email, password)
    result = {"track": "azure-windows", "passed": False, "error": None}
    # Azure Windows VM names max 15 chars
    vm_name = f"nxpsmkwinaz{secrets.token_hex(2)}"
    nexplane_url = f"http://{backend_ip}:8000"
    admin_password = f"NxP!{secrets.token_hex(8)}"
    print(f"\n[azure-windows] Starting worker: {vm_name}")

    try:
        cloud_account_id = client.get_connector_cloud_account_id("azure")
        agent_secret = client.get_agent_secret()

        # Launch Azure Windows VM — PowerShell Custom Script Extension installs Tailscale + agent
        client._run_cr_with_timeout(
            "[azure-windows] launch Windows VM", "azure_vm_create", cloud_account_id,
            {"vm_name": vm_name, "resource_group": azure_resource_group,
             "location": "eastus", "vm_size": "Standard_B2s",
             "os": "windows", "connection_mode": "agent_extension",
             "nexplane_url": nexplane_url, "nexplane_secret": agent_secret,
             "tailscale_auth_key": tailscale_auth_key,
             "admin_username": "nexplaneadmin", "admin_password": admin_password},
            timeout=900,
        )
        log(f"[azure-windows] Azure Windows VM launched: {vm_name}")

        # 12 min — Windows + Custom Script Extension is slowest
        endpoint_asset = _poll_for_endpoint(client, vm_name, timeout=720)

        _run_all_windows_agent_crs(client, endpoint_asset["id"], "azure-windows")
        result["passed"] = True
        log("[azure-windows] track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [azure-windows] Failed: {e}")
    finally:
        _teardown_azure_windows(client, vm_name, azure_resource_group)

    return result
```

- [ ] **Step 3: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_agent_live.py
git commit -m "feat(agent-smoke): add GCP/Azure Windows workers and teardown helpers"
```

---

### Task 12: Update main() for parallel Windows dispatch + final docstring

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

Replace the `run_windows` block in `main()` (currently calls old sequential tracks) with parallel `ThreadPoolExecutor` dispatch. Update the module docstring to reflect the new capabilities.

- [ ] **Step 1: Replace the `if run_windows:` block in `main()`**

Find:
```python
        if run_windows:
            # Windows workers added in Sub-project 2
            print("\n  ⚠️  Windows parallel workers not yet implemented (Sub-project 2)")
            if run_aws:
                run_aws_windows_track(client, cloud_account_id, args.tailscale_auth_key, phases)
            if run_gcp:
                run_gcp_windows_track(client, cloud_account_id, args.tailscale_auth_key,
                                       args.gcp_project, phases)
            if run_azure:
                run_azure_windows_track(client, cloud_account_id, args.tailscale_auth_key,
                                         args.azure_resource_group, phases)
```

Replace with:
```python
        if run_windows:
            print("\n" + "=" * 60)
            print("Running Windows tracks in parallel")
            print("=" * 60)
            windows_futures: dict = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                if run_aws:
                    windows_futures[executor.submit(
                        run_aws_windows_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, args.tailscale_auth_key,
                    )] = "aws-windows"
                if run_gcp:
                    if not args.gcp_project:
                        fail("--gcp-project required for GCP Windows track")
                    windows_futures[executor.submit(
                        run_gcp_windows_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, args.tailscale_auth_key, args.gcp_project,
                    )] = "gcp-windows"
                if run_azure:
                    if not args.azure_resource_group:
                        fail("--azure-resource-group required for Azure Windows track")
                    windows_futures[executor.submit(
                        run_azure_windows_worker,
                        args.base_url, args.email, args.password,
                        backend_ip, args.tailscale_auth_key, args.azure_resource_group,
                    )] = "azure-windows"
                all_results.extend(_collect_results(windows_futures))
```

- [ ] **Step 2: Update the module docstring**

Replace lines 1-37 (the module docstring) with:

```python
#!/usr/bin/env python3
"""
Nexplane Agent Live Smoke Test — All agent command groups × AWS/GCP/Azure × Linux/Windows.

Deploys the Nexplane agent on real cloud instances and exercises all agent command groups
via Nexplane CRs (not SSM). Agent registration is mandatory — tests fail immediately if
the agent does not register within the polling window.

Linux tracks (12 command groups each): linux_patch, ossecurity, linuxauth, crossplatform,
  compliance, forensics, fleet, backup, reboot, credrotation, iac, linuxupgrade

Windows tracks (8 command groups each): win_patch, winharden, crossplatform, fleet, reboot,
  credrotation, forensics, backup

Tracks within each OS group run in parallel via ThreadPoolExecutor(max_workers=3).

Usage:
    # All Linux tracks in parallel:
    python backend/tests/smoke/test_agent_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --tailscale-auth-key tskey-auth-<key> \\
        --cloud all --os linux \\
        --gcp-project my-project \\
        --azure-resource-group nexplane-smoke-rg

    # All Windows tracks in parallel:
        --cloud all --os windows ...

    # All 6 tracks (Linux + Windows simultaneously):
        --cloud all --os both ...

    # Single cloud:
        --cloud aws --os linux

Requirements:
    AWS connector with credentials + NexplaneEC2TestProfile IAM role
    GCP connector with credentials + Compute Engine API enabled
    Azure connector with credentials + Contributor role on subscription
    Tailscale connector with reusable pre-authorized auth key
"""
```

- [ ] **Step 3: Update `--phases` help text in parser**

Find the `--phases` argument help string and update:
```python
        help=(
            f"Comma-separated agent command groups to run. "
            f"Linux: {', '.join(_LINUX_PHASES)}. "
            f"Windows: {', '.join(c.replace('agent_', '') for c in _WINDOWS_AGENT_CRS)}. "
            f"Default: all Linux phases."
        ),
```

- [ ] **Step 4: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 5: Verify all 6 worker functions parse**

```bash
docker exec nexplane-backend-1 python -c "
import sys; sys.path.insert(0, '/app/tests/smoke')
import test_agent_live
for fn in ['run_aws_linux_worker', 'run_gcp_linux_worker', 'run_azure_linux_worker',
           'run_aws_windows_worker', 'run_gcp_windows_worker', 'run_azure_windows_worker']:
    print(f'{fn}:', getattr(test_agent_live, fn))
print('OK')
"
```

Expected: all 6 printed, then OK.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/smoke/test_agent_live.py
git commit -m "feat(agent-smoke): complete parallel Windows dispatch in main(); update module docstring"
```

---

## Self-review

**Spec coverage:**
- ✅ GCP `launch_instance.py`: `tailscale_auth_key` → Tailscale prepend in Linux startup — Task 1
- ✅ Azure `launch_vm.py`: `tailscale_auth_key` → Tailscale prepend in agent_extension script — Task 2
- ✅ `_poll_for_endpoint()`: mandatory registration, fails on miss — Task 3
- ✅ `_run_all_linux_agent_crs()`: all 12 Linux agent CRs — Task 3
- ✅ `_collect_results()`: future aggregation — Task 3
- ✅ `run_aws_linux_worker()`: CR-only, no SSM fallback — Task 4
- ✅ `run_gcp_linux_worker()` + `_teardown_gcp_linux()` — Task 5
- ✅ `run_azure_linux_worker()` + `_teardown_azure_linux()` — Task 6
- ✅ `main()` parallel Linux dispatch via ThreadPoolExecutor — Task 7
- ✅ GCP `launch_instance.py`: Windows image + PowerShell startup script — Task 8
- ✅ Azure `launch_vm.py`: Windows image + PowerShell Custom Script Extension — Task 9
- ✅ `_run_all_windows_agent_crs()`: all 8 Windows agent CRs — Task 10
- ✅ `run_aws_windows_worker()`: SSM Tailscale+agent install → CR-only — Task 10
- ✅ `_teardown_aws_windows()` — Task 10
- ✅ `run_gcp_windows_worker()` + teardown — Task 11
- ✅ `run_azure_windows_worker()` + teardown — Task 11
- ✅ `main()` parallel Windows dispatch — Task 12
- ✅ Module docstring updated — Task 12

**Placeholder scan:** None. All steps have complete code.

**Type consistency:**
- `_poll_for_endpoint(client, hostname, timeout)` → returns `dict` with `["id"]` — consistent in Tasks 3, 4, 5, 6, 10, 11
- `_run_all_linux_agent_crs(client, endpoint_asset_id, label)` — used in Tasks 4, 5, 6 with same signature
- `_run_all_windows_agent_crs(client, endpoint_asset_id, label)` — used in Tasks 10, 11 with same signature
- All worker functions return `{"track": str, "passed": bool, "error": str|None}` — consistent with `_collect_results()`
- `get_connector_cloud_account_id(connector_type)` — from smoke_helpers, used in Tasks 4, 5, 6, 10, 11
