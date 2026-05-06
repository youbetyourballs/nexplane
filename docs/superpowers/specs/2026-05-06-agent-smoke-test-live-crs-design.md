# Agent Smoke Test — Live CR Expansion Design

**Date:** 2026-05-06
**Status:** Approved for implementation

---

## Goals

1. Replace SSM-based verification in the AWS Linux agent track with Nexplane agent CRs.
2. Make agent registration mandatory — fail immediately if the agent doesn't register within the polling window.
3. Add GCP Linux and Azure Linux agent tracks running in parallel with AWS Linux.
4. Add AWS Windows, GCP Windows, and Azure Windows agent tracks running in parallel with each other.

---

## Current State

- `test_agent_live.py` has an AWS Linux track that deploys the Nexplane agent via SSM, then verifies all 12 command groups by running shell commands via `ssm_command` CRs — not via agent CRs.
- `_agent_cr()` helper exists but is unused in the main flow.
- Agent registration failure prints a warning and continues with SSM fallback — the CR path is never exercised.
- GCP Linux, Azure Linux, GCP Windows, Azure Windows tracks are stubs.
- AWS Windows track exists but uses SSM PowerShell, not agent CRs.
- `gcp/launch_instance.py` has `_AGENT_STARTUP_TEMPLATE` for Linux only.
- `azure/launch_vm.py` has `_AGENT_STARTUP_SCRIPT` for Linux only.

---

## Architecture

Two independent sub-projects, each producing a working parallel test:

**Sub-project 1 — Linux tracks (all 3 clouds in parallel):**
- Rewrite AWS Linux track: SSM → agent CRs; fail on registration miss
- Add GCP Linux track: `gce_instance_create` with Tailscale embedded in startup script
- Add Azure Linux track: `azure_vm_create` with Tailscale embedded in Custom Script Extension
- Run all 3 in parallel via `ThreadPoolExecutor(max_workers=3)`

**Sub-project 2 — Windows tracks (all 3 clouds in parallel):**
- Rewrite AWS Windows track: SSM PowerShell → agent CRs (win_patch, winharden + 6 cross-platform)
- Add GCP Windows track: `gce_instance_create` with Windows image + PowerShell startup script
- Add Azure Windows track: `azure_vm_create` with Windows image + PowerShell Custom Script Extension
- Run all 3 in parallel via `ThreadPoolExecutor(max_workers=3)`

**Shared setup/teardown:**
- `setup_backend_tailscale()` called once in `main()` before launching threads
- `teardown_backend_tailscale()` called once in `main()` after all threads complete
- Each worker creates its own `NexplaneClient`

---

## Sub-project 1: Linux Tracks

### Executor changes

**`gcp/launch_instance.py`**

Add optional `tailscale_auth_key` parameter. When non-empty, prepend Tailscale installation before the agent install in `_AGENT_STARTUP_TEMPLATE`:

```python
_TAILSCALE_INSTALL = """
# Install and join Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey="{tailscale_auth_key}" --hostname="{name}" --accept-routes
"""

# Modified template construction:
if tailscale_auth_key and connection_mode == "agent_startup":
    script = _TAILSCALE_INSTALL.format(
        tailscale_auth_key=tailscale_auth_key, name=name
    ) + _AGENT_STARTUP_TEMPLATE.format(
        nexplane_url=nexplane_url, nexplane_secret=nexplane_secret
    )
else:
    script = _AGENT_STARTUP_TEMPLATE.format(
        nexplane_url=nexplane_url, nexplane_secret=nexplane_secret
    )
```

No change to existing behavior when `tailscale_auth_key` is empty or absent.

**`azure/launch_vm.py`**

Add optional `tailscale_auth_key` parameter. When non-empty and `connection_mode == "agent_extension"`, prepend Tailscale installation to `_AGENT_STARTUP_SCRIPT`:

```python
_TAILSCALE_PREPEND = """
# Install and join Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey="{tailscale_auth_key}" --hostname="{vm_name}" --accept-routes
"""

# Modified script construction:
startup_script = _AGENT_STARTUP_SCRIPT.format(
    nexplane_url=nexplane_url, nexplane_secret=nexplane_secret
)
if tailscale_auth_key and connection_mode == "agent_extension":
    startup_script = _TAILSCALE_PREPEND.format(
        tailscale_auth_key=tailscale_auth_key, vm_name=vm_name
    ) + startup_script
```

### `test_agent_live.py` changes

**Rewrite 12 Linux command group functions to use `_agent_cr()`**

Replace all SSM-based functions with single-CR equivalents:

```python
def _run_all_linux_agent_crs(client: NexplaneClient, endpoint_asset_id: str, label: str) -> None:
    """Run all 12 Linux agent command groups via Nexplane CRs."""
    for change_type in [
        "agent_linux_patch", "agent_ossecurity", "agent_linuxauth",
        "agent_crossplatform", "agent_compliance", "agent_forensics",
        "agent_fleet", "agent_backup", "agent_reboot", "agent_credrotation",
        "agent_iac", "agent_linuxupgrade",
    ]:
        _agent_cr(client, endpoint_asset_id, f"{change_type.replace('agent_', '')}-{label}", change_type)
```

**Helper: `_poll_for_endpoint(client, hostname, timeout)`**

```python
def _poll_for_endpoint(client: NexplaneClient, hostname: str, timeout: int) -> dict:
    """Poll for endpoint asset registration. Returns asset dict or raises on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        candidates = client.get("/assets", params={"q": hostname, "asset_type": "endpoint"})
        if candidates:
            log(f"Agent registered: {candidates[0]['id']}")
            return candidates[0]
        time.sleep(15)
    fail(f"Agent '{hostname}' did not register within {timeout}s")
    return {}  # unreachable
```

**AWS Linux worker**

```python
def run_aws_linux_worker(base_url: str, email: str, password: str,
                          backend_ip: str, tailscale_auth_key: str) -> dict:
    client = NexplaneClient(base_url, email, password)
    result = {"track": "aws-linux", "passed": False, "error": None}
    instance_name = "nexplane-agent-smoke-linux-aws"
    nexplane_url = f"http://{backend_ip}:8000"

    try:
        cloud_account_id = client.get_connector_cloud_account_id("aws")
        agent_secret = client.get_agent_secret()

        # Key pair + EC2 launch
        client.run_cr("[aws-linux] create key pair", "key_pair_create", cloud_account_id,
                      {"key_name": "nexplane-agent-smoke-key"})
        client.run_cr("[aws-linux] launch EC2", "ec2_launch", cloud_account_id,
                      {"mode": "quick", "name": instance_name, "os": "amazon_linux",
                       "iam_instance_profile": "NexplaneEC2TestProfile",
                       "key_name": "nexplane-agent-smoke-key",
                       "rollback_strategy": "terminate_instance"})
        time.sleep(10)

        instance_asset = client.get_asset_by_name(instance_name)
        if not instance_asset:
            fail("EC2 instance not in inventory")
        instance_id = instance_asset["asset_metadata"]["instance_id"]

        # Wait for SSM, join Tailscale, deploy agent
        time.sleep(180)
        client.run_cr("[aws-linux] SSM whoami", "ssm_command", instance_asset["id"],
                      {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
                       "command": "whoami", "rollback_strategy": "rollback_unavailable"})
        client.run_cr("[aws-linux] tailscale join", "tailscale_join", instance_asset["id"],
                      {"instance_id": instance_id, "auth_key": tailscale_auth_key,
                       "hostname": "nexplane-agent-smoke-aws-linux"})
        client.run_cr("[aws-linux] deploy agent", "deploy_nexplane_agent", instance_asset["id"],
                      {"instance_id": instance_id, "nexplane_url": nexplane_url,
                       "nexplane_secret": agent_secret})

        # MANDATORY: fail if agent doesn't register (no SSM fallback)
        endpoint_asset = _poll_for_endpoint(client, "nexplane-agent-smoke-aws-linux", timeout=180)

        # Run all 12 Linux agent CR groups
        _run_all_linux_agent_crs(client, endpoint_asset["id"], "aws-linux")

        result["passed"] = True
        log("aws-linux track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [aws-linux] Failed: {e}")
    finally:
        _teardown_aws_linux(client)

    return result
```

**GCP Linux worker**

```python
def run_gcp_linux_worker(base_url: str, email: str, password: str,
                          backend_ip: str, tailscale_auth_key: str, gcp_project: str) -> dict:
    client = NexplaneClient(base_url, email, password)
    result = {"track": "gcp-linux", "passed": False, "error": None}
    instance_name = f"nexplane-agent-smoke-linux-gcp-{secrets.token_hex(3)}"
    nexplane_url = f"http://{backend_ip}:8000"

    try:
        cloud_account_id = client.get_connector_cloud_account_id("gcp")
        agent_secret = client.get_agent_secret()

        # Launch GCE with Tailscale + agent embedded in startup script
        cr = client._run_cr_with_timeout(
            "[gcp-linux] launch GCE instance", "gce_instance_create", cloud_account_id,
            {"name": instance_name, "machine_type": "e2-micro", "zone": "us-central1-a",
             "image_family": "ubuntu-2204-lts", "image_project": "ubuntu-os-cloud",
             "connection_mode": "agent_startup", "nexplane_url": nexplane_url,
             "nexplane_secret": agent_secret, "tailscale_auth_key": tailscale_auth_key},
            timeout=300,
        )

        # MANDATORY registration (6 min — startup script runs during boot)
        endpoint_asset = _poll_for_endpoint(client, instance_name, timeout=360)

        # Run all 12 Linux agent CR groups
        _run_all_linux_agent_crs(client, endpoint_asset["id"], "gcp-linux")

        result["passed"] = True
        log("gcp-linux track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [gcp-linux] Failed: {e}")
    finally:
        _teardown_gcp_linux(client, instance_name, gcp_project)

    return result
```

**Azure Linux worker**

```python
def run_azure_linux_worker(base_url: str, email: str, password: str,
                            backend_ip: str, tailscale_auth_key: str,
                            azure_resource_group: str) -> dict:
    client = NexplaneClient(base_url, email, password)
    result = {"track": "azure-linux", "passed": False, "error": None}
    vm_name = f"nexplane-agent-smoke-lx-az-{secrets.token_hex(3)}"
    nexplane_url = f"http://{backend_ip}:8000"

    try:
        cloud_account_id = client.get_connector_cloud_account_id("azure")
        agent_secret = client.get_agent_secret()

        # Launch Azure VM with Tailscale + agent embedded in Custom Script Extension
        cr = client._run_cr_with_timeout(
            "[azure-linux] launch Azure VM", "azure_vm_create", cloud_account_id,
            {"vm_name": vm_name, "resource_group": azure_resource_group,
             "location": "eastus", "vm_size": "Standard_B1s",
             "connection_mode": "agent_extension", "nexplane_url": nexplane_url,
             "nexplane_secret": agent_secret, "tailscale_auth_key": tailscale_auth_key},
            timeout=600,
        )

        # MANDATORY registration (8 min — Custom Script Extension can be slow)
        endpoint_asset = _poll_for_endpoint(client, vm_name, timeout=480)

        # Run all 12 Linux agent CR groups
        _run_all_linux_agent_crs(client, endpoint_asset["id"], "azure-linux")

        result["passed"] = True
        log("azure-linux track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [azure-linux] Failed: {e}")
    finally:
        _teardown_azure_linux(client, vm_name, azure_resource_group)

    return result
```

**Parallel dispatch in `main()` for `--os linux`:**

```python
backend_ip = setup_backend_tailscale(args.tailscale_auth_key)
try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}
        if "aws" in clouds:
            futures[executor.submit(run_aws_linux_worker,
                args.base_url, args.email, args.password,
                backend_ip, args.tailscale_auth_key)] = "aws-linux"
        if "gcp" in clouds:
            futures[executor.submit(run_gcp_linux_worker,
                args.base_url, args.email, args.password,
                backend_ip, args.tailscale_auth_key, args.gcp_project)] = "gcp-linux"
        if "azure" in clouds:
            futures[executor.submit(run_azure_linux_worker,
                args.base_url, args.email, args.password,
                backend_ip, args.tailscale_auth_key, args.azure_resource_group)] = "azure-linux"
        results = _collect_results(futures)
finally:
    teardown_backend_tailscale()
```

---

## Sub-project 2: Windows Tracks

### Windows agent packages (8 — full Windows parity)

```python
_WINDOWS_AGENT_CRS = [
    "agent_win_patch", "agent_winharden",
    "agent_crossplatform", "agent_fleet", "agent_reboot",
    "agent_credrotation", "agent_forensics", "agent_backup",
]

def _run_all_windows_agent_crs(client: NexplaneClient, endpoint_asset_id: str, label: str) -> None:
    for change_type in _WINDOWS_AGENT_CRS:
        _agent_cr(client, endpoint_asset_id,
                  f"{change_type.replace('agent_', '')}-{label}", change_type)
```

### Shared PowerShell startup script template

```powershell
# _WINDOWS_AGENT_PS1 template — parameterized with nexplane_url, nexplane_secret,
# tailscale_auth_key, hostname

# Install Tailscale
Invoke-WebRequest -Uri "https://pkgs.tailscale.com/stable/tailscale-setup.exe" `
  -OutFile "$env:TEMP\ts-setup.exe" -UseBasicParsing
Start-Process "$env:TEMP\ts-setup.exe" -Args "/S" -Wait
& "C:\Program Files\Tailscale\tailscale.exe" up `
  --authkey="{tailscale_auth_key}" --hostname="{hostname}" --accept-routes

# Download and install Nexplane agent
$version = (Invoke-WebRequest `
  "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version" `
  -UseBasicParsing).Content.Trim()
Invoke-WebRequest `
  "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-windows-amd64-$version.exe" `
  -OutFile "C:\nexplane-agent.exe" -UseBasicParsing

New-Service -Name "NexplaneAgent" `
  -BinaryPathName "C:\nexplane-agent.exe --control-plane {nexplane_url} --secret {nexplane_secret} --mode service" `
  -StartupType Automatic -Description "Nexplane Agent"
Start-Service "NexplaneAgent"
```

### Executor changes

**`gcp/launch_instance.py`**

Add `os` parameter (default `"linux"`). When `os == "windows"`, switch image and inject PowerShell startup script via `windows-startup-script-ps1` metadata key:

```python
if os_type == "windows":
    image_family = parameters.get("image_family", "windows-server-2022-dc")
    image_project = parameters.get("image_project", "windows-cloud")
    # Use windows-startup-script-ps1 metadata key instead of startup-script
    metadata_key = "windows-startup-script-ps1"
    script = _WINDOWS_AGENT_PS1.format(
        nexplane_url=nexplane_url,
        nexplane_secret=nexplane_secret,
        tailscale_auth_key=tailscale_auth_key,
        hostname=name,
    )
    machine_type = parameters.get("machine_type", "e2-medium")  # Windows needs more RAM
```

**`azure/launch_vm.py`**

Add `os` parameter (default `"linux"`). When `os == "windows"`, use Windows Server image and PowerShell Custom Script Extension instead of the Linux shell script:

```python
if os_type == "windows":
    image_reference = ImageReference(
        publisher="MicrosoftWindowsServer",
        offer="WindowsServer",
        sku="2022-datacenter-smalldisk",
        version="latest",
    )
    # Use PowerShell Custom Script Extension
    script_b64 = base64.b64encode(
        _WINDOWS_AGENT_PS1.format(
            nexplane_url=nexplane_url,
            nexplane_secret=nexplane_secret,
            tailscale_auth_key=tailscale_auth_key,
            hostname=vm_name,
        ).encode()
    ).decode()
    vm_size = parameters.get("vm_size", "Standard_B2s")  # Windows needs more RAM
    # OsProfile: use admin_username + admin_password (no SSH for Windows)
```

### AWS Windows worker

AWS Windows uses a different launch flow: `ec2_launch` with Windows AMI, wait for SSM (~5 min for Windows), then two SSM PowerShell CRs to install Tailscale and agent, then poll for registration.

```python
def run_aws_windows_worker(base_url: str, email: str, password: str,
                            backend_ip: str, tailscale_auth_key: str) -> dict:
    client = NexplaneClient(base_url, email, password)
    result = {"track": "aws-windows", "passed": False, "error": None}
    instance_name = "nexplane-agent-smoke-win-aws"
    nexplane_url = f"http://{backend_ip}:8000"

    try:
        cloud_account_id = client.get_connector_cloud_account_id("aws")
        agent_secret = client.get_agent_secret()

        # Launch Windows EC2 (t3.medium, Windows Server 2022)
        client._run_cr_with_timeout(
            "[aws-windows] launch Windows EC2", "ec2_launch", cloud_account_id,
            {"mode": "quick", "name": instance_name, "os": "windows_2022",
             "iam_instance_profile": "NexplaneEC2TestProfile",
             "rollback_strategy": "terminate_instance"},
            timeout=600,
        )
        time.sleep(10)
        instance_asset = client.get_asset_by_name(instance_name)
        if not instance_asset:
            fail("Windows EC2 instance not in inventory")
        instance_id = instance_asset["asset_metadata"]["instance_id"]

        # Windows SSM takes longer — wait 5 min
        print("  Waiting 5 min for Windows SSM agent...")
        time.sleep(300)

        # Install Tailscale via SSM PowerShell
        client._run_cr_with_timeout(
            "[aws-windows] install Tailscale", "ssm_command", instance_asset["id"],
            {"instance_id": instance_id,
             "document_name": "AWS-RunPowerShellScript",
             "command": (
                 f"Invoke-WebRequest -Uri 'https://pkgs.tailscale.com/stable/tailscale-setup.exe' "
                 f"-OutFile '$env:TEMP\\ts-setup.exe' -UseBasicParsing; "
                 f"Start-Process '$env:TEMP\\ts-setup.exe' -Args '/S' -Wait; "
                 f"& 'C:\\Program Files\\Tailscale\\tailscale.exe' up "
                 f"--authkey='{tailscale_auth_key}' --hostname='nexplane-agent-smoke-aws-windows' "
                 f"--accept-routes"
             ),
             "rollback_strategy": "rollback_unavailable"},
            timeout=300,
        )

        # Install Nexplane agent as Windows Service via SSM PowerShell
        client._run_cr_with_timeout(
            "[aws-windows] install agent", "ssm_command", instance_asset["id"],
            {"instance_id": instance_id,
             "document_name": "AWS-RunPowerShellScript",
             "command": (
                 f"$v = (Invoke-WebRequest 'https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version' "
                 f"-UseBasicParsing).Content.Trim(); "
                 f"Invoke-WebRequest \"https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-windows-amd64-$v.exe\" "
                 f"-OutFile 'C:\\nexplane-agent.exe' -UseBasicParsing; "
                 f"New-Service -Name 'NexplaneAgent' "
                 f"-BinaryPathName 'C:\\nexplane-agent.exe --control-plane {nexplane_url} --secret {agent_secret} --mode service' "
                 f"-StartupType Automatic; Start-Service 'NexplaneAgent'"
             ),
             "rollback_strategy": "rollback_unavailable"},
            timeout=300,
        )

        # MANDATORY registration (10 min polling window for Windows)
        endpoint_asset = _poll_for_endpoint(
            client, "nexplane-agent-smoke-aws-windows", timeout=600)

        # Run all 8 Windows-compatible agent CR groups
        _run_all_windows_agent_crs(client, endpoint_asset["id"], "aws-windows")

        result["passed"] = True
        log("aws-windows track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [aws-windows] Failed: {e}")
    finally:
        _teardown_aws_windows(client)

    return result
```

### GCP Windows worker

```python
def run_gcp_windows_worker(base_url: str, email: str, password: str,
                            backend_ip: str, tailscale_auth_key: str,
                            gcp_project: str) -> dict:
    client = NexplaneClient(base_url, email, password)
    result = {"track": "gcp-windows", "passed": False, "error": None}
    instance_name = f"nexplane-agent-smoke-win-gcp-{secrets.token_hex(3)}"
    nexplane_url = f"http://{backend_ip}:8000"

    try:
        cloud_account_id = client.get_connector_cloud_account_id("gcp")
        agent_secret = client.get_agent_secret()

        # Launch Windows GCE with PowerShell startup script
        client._run_cr_with_timeout(
            "[gcp-windows] launch Windows GCE", "gce_instance_create", cloud_account_id,
            {"name": instance_name, "machine_type": "e2-medium", "zone": "us-central1-a",
             "image_family": "windows-server-2022-dc", "image_project": "windows-cloud",
             "os": "windows",
             "connection_mode": "agent_startup", "nexplane_url": nexplane_url,
             "nexplane_secret": agent_secret, "tailscale_auth_key": tailscale_auth_key},
            timeout=600,
        )

        # MANDATORY registration (10 min — Windows boots slower)
        endpoint_asset = _poll_for_endpoint(client, instance_name, timeout=600)

        _run_all_windows_agent_crs(client, endpoint_asset["id"], "gcp-windows")

        result["passed"] = True
        log("gcp-windows track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [gcp-windows] Failed: {e}")
    finally:
        _teardown_gcp_windows(client, instance_name, gcp_project)

    return result
```

### Azure Windows worker

```python
def run_azure_windows_worker(base_url: str, email: str, password: str,
                              backend_ip: str, tailscale_auth_key: str,
                              azure_resource_group: str) -> dict:
    client = NexplaneClient(base_url, email, password)
    result = {"track": "azure-windows", "passed": False, "error": None}
    vm_name = f"nexplane-agent-smoke-win-az-{secrets.token_hex(3)}"
    # Azure Windows VM names max 15 chars
    vm_name = f"nxpsmkwinaz{secrets.token_hex(2)}"
    nexplane_url = f"http://{backend_ip}:8000"
    admin_password = f"NxP!{secrets.token_hex(8)}"

    try:
        cloud_account_id = client.get_connector_cloud_account_id("azure")
        agent_secret = client.get_agent_secret()

        # Launch Azure Windows VM with PowerShell Custom Script Extension
        client._run_cr_with_timeout(
            "[azure-windows] launch Windows VM", "azure_vm_create", cloud_account_id,
            {"vm_name": vm_name, "resource_group": azure_resource_group,
             "location": "eastus", "vm_size": "Standard_B2s",
             "os": "windows",
             "connection_mode": "agent_extension", "nexplane_url": nexplane_url,
             "nexplane_secret": agent_secret, "tailscale_auth_key": tailscale_auth_key,
             "admin_username": "nexplaneadmin", "admin_password": admin_password},
            timeout=900,
        )

        # MANDATORY registration (12 min — Windows + Custom Script Extension is slowest)
        endpoint_asset = _poll_for_endpoint(client, vm_name, timeout=720)

        _run_all_windows_agent_crs(client, endpoint_asset["id"], "azure-windows")

        result["passed"] = True
        log("azure-windows track complete")

    except Exception as e:
        result["error"] = str(e)
        print(f"\n❌ [azure-windows] Failed: {e}")
    finally:
        _teardown_azure_windows(client, vm_name, azure_resource_group)

    return result
```

### Parallel dispatch for `--os windows`

Same `ThreadPoolExecutor(max_workers=3)` pattern as Linux. `setup_backend_tailscale()` called once in `main()` and shared.

---

## Teardown helpers

Each track has a cloud-specific teardown function that runs in `finally` regardless of success/failure:

- `_teardown_aws_linux(client)` — boto3 terminate instances + delete key pairs matching `nexplane-agent-smoke*`
- `_teardown_gcp_linux(client, instance_name, gcp_project)` — GCP SDK delete instance + inventory cleanup
- `_teardown_azure_linux(client, vm_name, resource_group)` — Azure SDK delete VM + inventory cleanup
- `_teardown_aws_windows(client)` — same as aws_linux teardown
- `_teardown_gcp_windows(client, instance_name, gcp_project)` — same as gcp_linux teardown
- `_teardown_azure_windows(client, vm_name, resource_group)` — same as azure_linux teardown

---

## CLI interface

```
python backend/tests/smoke/test_agent_live.py \
    --base-url http://localhost:8000 \
    --email admin@nexplane.local \
    --password changeme \
    --tailscale-auth-key tskey-auth-<key> \
    --cloud all \          # aws | gcp | azure | all  (default: all)
    --os linux \           # linux | windows | both   (default: linux)
    --gcp-project my-project \
    --azure-resource-group nexplane-smoke-rg
```

When `--os both`, Linux and Windows threads run simultaneously (up to 6 workers total).

---

## Expected runtimes

| Track | VM boot | Agent registration | 12/8 CRs | Total |
|-------|---------|-------------------|----------|-------|
| AWS Linux | 3 min (SSM) | 3 min | 10 min | ~25 min |
| GCP Linux | 2 min | 6 min | 10 min | ~25 min |
| Azure Linux | 5 min | 8 min | 10 min | ~30 min |
| AWS Windows | 5 min (SSM) | 5 min | 15 min | ~35 min |
| GCP Windows | 5 min | 10 min | 15 min | ~35 min |
| Azure Windows | 7 min | 12 min | 15 min | ~40 min |

With parallel execution: Linux tracks finish in ~30 min, Windows tracks in ~40 min.

---

## Out of Scope

- Windows tracks for agent packages not yet implemented for Windows (dbadmin, iac, linuxupgrade, ossecurity, linuxauth, linux_patch) — these are Linux-only
- Parallel Linux + Windows simultaneously on a single run (can be done with `--os both` but adds complexity; defer
- GCP/Azure Windows VM password management (hardcoded generated password is sufficient for smoke tests)
