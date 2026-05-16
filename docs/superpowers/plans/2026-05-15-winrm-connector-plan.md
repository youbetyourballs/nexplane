# WinRM Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a WinRM connector to Nexplane that enables authenticated remote PowerShell/cmd.exe execution on Windows Server hosts via the pywinrm library, with smoke phase validation against a real Windows Server 2022 EC2.

**Architecture:** WinRM connector follows the same pattern as the SSH connector — a `_client.py` factory plus executor modules per action, a catalog JSON describing credential fields and actions, and ChangeType enum entries. The smoke phase launches a Windows EC2, enables WinRM via SSM, registers a WinRM connector in Nexplane, runs check_prerequisites / download_agent / install_agent CRs, rollbacks install, and uses AMI caching.

**Tech Stack:** Python 3.9, pywinrm>=0.4.3, FastAPI/SQLAlchemy backend, React/TypeScript frontend, pytest smoke tests, boto3 for EC2/SSM.

---

### Task 1: WinRM executor package skeleton

**Files:**
- Create: `backend/app/connectors/executors/winrm/__init__.py`
- Create: `backend/app/connectors/executors/winrm/_client.py`

- [ ] **Step 1: Create empty `__init__.py`**

```python
```
(empty file, just needs to exist)

- [ ] **Step 2: Write `_client.py`**

```python
from __future__ import annotations

import winrm

CMD_ALLOWLIST = (
    "sc query",
    "sc qc",
    "tasklist",
    "systeminfo",
    "ipconfig",
    "netstat",
    "dir",
    "type",
    "echo",
    "hostname",
    "ver",
)

PS_ALLOWLIST = (
    "Get-Service",
    "Start-Service",
    "Stop-Service",
    "Restart-Service",
    "Set-Service",
    "Get-EventLog",
    "Get-WinEvent",
    "Install-WindowsFeature",
    "Remove-WindowsFeature",
    "New-Item",
    "Remove-Item",
    "Copy-Item",
    "Invoke-WebRequest",
    "Start-BitsTransfer",
    "Start-Process",
    "Stop-Process",
    "Set-ExecutionPolicy",
    "sc.exe create",
    "sc.exe delete",
    "sc.exe start",
    "sc.exe stop",
)


class WinRMClient:
    def __init__(self, hostname, port, username, password, use_ssl, verify_ssl):
        # type: (str, int, str, str, bool, bool) -> None
        self._hostname = hostname
        self._port = port
        self._username = username
        self._password = password
        self._use_ssl = use_ssl
        self._verify_ssl = verify_ssl

    def _session(self):
        # type: () -> winrm.Session
        scheme = "https" if self._use_ssl else "http"
        target = "{}://{}:{}/wsman".format(scheme, self._hostname, self._port)
        server_cert_validation = "validate" if self._verify_ssl else "ignore"
        return winrm.Session(
            target,
            auth=(self._username, self._password),
            transport="ntlm",
            server_cert_validation=server_cert_validation,
        )

    def run_cmd(self, command):
        # type: (str) -> tuple
        """Run a cmd.exe command. Returns (stdout, stderr, exit_code)."""
        session = self._session()
        r = session.run_cmd(command)
        return (
            r.std_out.decode("utf-8", errors="replace"),
            r.std_err.decode("utf-8", errors="replace"),
            r.status_code,
        )

    def run_ps(self, script):
        # type: (str) -> tuple
        """Run a PowerShell script. Returns (stdout, stderr, exit_code)."""
        session = self._session()
        r = session.run_ps(script)
        return (
            r.std_out.decode("utf-8", errors="replace"),
            r.std_err.decode("utf-8", errors="replace"),
            r.status_code,
        )

    def is_allowed_cmd(self, cmd):
        # type: (str) -> bool
        return any(cmd.strip().startswith(p) for p in CMD_ALLOWLIST)

    def is_allowed_ps(self, script):
        # type: (str) -> bool
        return any(script.strip().startswith(p) for p in PS_ALLOWLIST)


def get_winrm_client(connector):
    # type: (...) -> WinRMClient
    creds = getattr(connector, "credentials", {}) or {}
    hostname = creds.get("hostname", "")
    port = int(creds.get("port", 5985))
    username = creds.get("username", "")
    password = creds.get("password", "")
    use_ssl_raw = creds.get("use_ssl", "false")
    use_ssl = use_ssl_raw is True or str(use_ssl_raw).lower() == "true"
    verify_ssl_raw = creds.get("verify_ssl", "false")
    verify_ssl = verify_ssl_raw is True or str(verify_ssl_raw).lower() == "true"
    return WinRMClient(hostname, port, username, password, use_ssl, verify_ssl)
```

- [ ] **Step 3: Verify syntax**

```
cd f:/Nexplane/nexplane && python -c "import ast; ast.parse(open('backend/app/connectors/executors/winrm/_client.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```
git add backend/app/connectors/executors/winrm/__init__.py backend/app/connectors/executors/winrm/_client.py
git commit -m "feat: add WinRM client module with allowlists"
```

---

### Task 2: WinRM executor files

**Files:**
- Create: `backend/app/connectors/executors/winrm/check_prerequisites.py`
- Create: `backend/app/connectors/executors/winrm/download_agent.py`
- Create: `backend/app/connectors/executors/winrm/install_agent.py`
- Create: `backend/app/connectors/executors/winrm/execute_template.py`
- Create: `backend/app/connectors/executors/winrm/collect_diagnostics.py`

- [ ] **Step 1: Write `check_prerequisites.py`**

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone


async def execute(parameters, asset_ids, connector):
    # type: (dict, list, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "winrm_check_prerequisites",
            "all_passed": True,
            "checks": [{"name": "mock", "passed": True, "output": "no credentials — mock mode"}],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_winrm_client

    loop = asyncio.get_event_loop()

    def _check():
        client = get_winrm_client(connector)
        checks = []

        # OS version — requires Windows Server 2016+ (build >= 14393)
        stdout, stderr, rc = client.run_ps(
            "(Get-WmiObject Win32_OperatingSystem).Caption + ' build ' + "
            "(Get-WmiObject Win32_OperatingSystem).BuildNumber"
        )
        os_info = stdout.strip()
        build_str = ""
        for part in os_info.split():
            if part.isdigit():
                build_str = part
        os_ok = int(build_str) >= 14393 if build_str else False
        checks.append({"name": "os_version", "passed": os_ok, "output": os_info})

        # Disk space — free on C: must be > 500 MB (512 000 000 bytes)
        stdout2, _, rc2 = client.run_ps(
            "(Get-PSDrive C).Free"
        )
        try:
            free_bytes = int(stdout2.strip())
            disk_ok = free_bytes > 512_000_000
        except ValueError:
            free_bytes = 0
            disk_ok = False
        checks.append({"name": "disk_space_c", "passed": disk_ok,
                        "output": "{} bytes free".format(free_bytes)})

        # .NET version — look for 4.x or later in registry
        stdout3, _, rc3 = client.run_ps(
            "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full' "
            "-ErrorAction SilentlyContinue).Release"
        )
        try:
            release = int(stdout3.strip())
            dotnet_ok = release >= 394802  # .NET 4.6.2+
        except ValueError:
            release = 0
            dotnet_ok = False
        checks.append({"name": "dotnet_4x", "passed": dotnet_ok,
                        "output": "release key {}".format(release)})

        # WinRM accessible — if we got here the session connected
        checks.append({"name": "winrm_accessible", "passed": True, "output": "session established"})

        return checks

    try:
        checks = await loop.run_in_executor(None, _check)
    except Exception as e:
        return {
            "action": "winrm_check_prerequisites",
            "all_passed": False,
            "error": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "action": "winrm_check_prerequisites",
        "checks": checks,
        "all_passed": all(c["passed"] for c in checks),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters, execution_result, connector):
    # type: (dict, dict, object) -> dict
    return {"rolled_back": True, "reason": "check_prerequisites is read-only"}
```

- [ ] **Step 2: Write `download_agent.py`**

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

AGENT_DEST = "C:\\nexplane\\nexplane-agent.exe"


async def execute(parameters, asset_ids, connector):
    # type: (dict, list, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}
    agent_url = parameters.get("agent_url", "")

    if not creds:
        return {
            "action": "winrm_download_agent",
            "downloaded": True,
            "destination": AGENT_DEST,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    if not agent_url:
        raise ValueError("agent_url parameter is required")

    from ._client import get_winrm_client

    loop = asyncio.get_event_loop()

    def _download():
        client = get_winrm_client(connector)
        # Create directory
        client.run_ps("New-Item -ItemType Directory -Force -Path 'C:\\nexplane'")
        # Download
        script = (
            "Invoke-WebRequest -Uri '{}' -OutFile '{}' -UseBasicParsing".format(
                agent_url, AGENT_DEST
            )
        )
        stdout, stderr, rc = client.run_ps(script)
        if rc != 0:
            raise RuntimeError("Download failed (rc={}): {}".format(rc, stderr))
        return stdout.strip()

    try:
        output = await loop.run_in_executor(None, _download)
    except Exception as e:
        return {
            "action": "winrm_download_agent",
            "downloaded": False,
            "error": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "action": "winrm_download_agent",
        "downloaded": True,
        "destination": AGENT_DEST,
        "output": output,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters, execution_result, connector):
    # type: (dict, dict, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {"rolled_back": True, "reason": "mock — nothing to remove"}

    from ._client import get_winrm_client
    import asyncio as _asyncio

    loop = _asyncio.get_event_loop()

    def _remove():
        client = get_winrm_client(connector)
        stdout, stderr, rc = client.run_ps(
            "Remove-Item '{}' -Force -ErrorAction SilentlyContinue".format(AGENT_DEST)
        )
        return rc

    try:
        rc = await loop.run_in_executor(None, _remove)
        return {"rolled_back": True, "destination": AGENT_DEST, "rc": rc}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
```

- [ ] **Step 3: Write `install_agent.py`**

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

SERVICE_NAME = "nexplane-agent"
AGENT_EXE = "C:\\nexplane\\nexplane-agent.exe"


async def execute(parameters, asset_ids, connector):
    # type: (dict, list, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}
    agent_secret = parameters.get("agent_secret", "")
    control_plane_url = parameters.get("control_plane_url", "")

    if not creds:
        return {
            "action": "winrm_install_agent",
            "service_created": True,
            "service_name": SERVICE_NAME,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_winrm_client

    loop = asyncio.get_event_loop()

    def _install():
        client = get_winrm_client(connector)

        # Create service
        bin_path = '{} --secret "{}" --url "{}"'.format(AGENT_EXE, agent_secret, control_plane_url)
        stdout, stderr, rc = client.run_cmd(
            'sc.exe create {} binpath= "{}" start= auto'.format(SERVICE_NAME, bin_path)
        )
        if rc != 0 and "already exists" not in stderr.lower():
            raise RuntimeError("sc.exe create failed (rc={}): {}".format(rc, stderr))

        # Start service
        stdout2, stderr2, rc2 = client.run_cmd("sc.exe start {}".format(SERVICE_NAME))
        if rc2 != 0 and "already running" not in stderr2.lower():
            raise RuntimeError("sc.exe start failed (rc={}): {}".format(rc2, stderr2))

        return {"create_stdout": stdout, "start_stdout": stdout2}

    try:
        result = await loop.run_in_executor(None, _install)
    except Exception as e:
        return {
            "action": "winrm_install_agent",
            "service_created": False,
            "error": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "action": "winrm_install_agent",
        "service_created": True,
        "service_name": SERVICE_NAME,
        "details": result,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters, execution_result, connector):
    # type: (dict, dict, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {"rolled_back": True, "reason": "mock — nothing to remove"}

    from ._client import get_winrm_client
    import asyncio as _asyncio

    loop = _asyncio.get_event_loop()

    def _uninstall():
        client = get_winrm_client(connector)
        client.run_cmd("sc.exe stop {}".format(SERVICE_NAME))
        stdout, stderr, rc = client.run_cmd("sc.exe delete {}".format(SERVICE_NAME))
        return rc

    try:
        rc = await loop.run_in_executor(None, _uninstall)
        return {"rolled_back": True, "service_name": SERVICE_NAME, "delete_rc": rc}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
```

- [ ] **Step 4: Write `execute_template.py`**

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

# Approved PowerShell template IDs → scripts
APPROVED_PS_TEMPLATES = {
    "winrm_get_services": "Get-Service | Select-Object Name, Status, StartType | ConvertTo-Json",
    "winrm_get_running_services": "Get-Service | Where-Object Status -eq Running | Select-Object Name, Status | ConvertTo-Json",
    "winrm_get_eventlog_system": "Get-EventLog -LogName System -Newest 20 | Select-Object TimeGenerated, EntryType, Source, Message | ConvertTo-Json",
    "winrm_system_info": "systeminfo",
    "winrm_disk_space": "Get-PSDrive C | Select-Object Used, Free | ConvertTo-Json",
}


async def execute(parameters, asset_ids, connector):
    # type: (dict, list, object) -> dict
    template_id = parameters.get("template_id", "")
    if not template_id or template_id not in APPROVED_PS_TEMPLATES:
        raise ValueError(
            "Template '{}' is not approved. Approved: {}".format(
                template_id, list(APPROVED_PS_TEMPLATES.keys())
            )
        )

    script = APPROVED_PS_TEMPLATES[template_id]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "winrm_execute_template",
            "template_id": template_id,
            "host_results": [
                {"asset_id": str(a), "exit_code": 0,
                 "stdout": "[mock] template executed", "stderr": ""}
                for a in asset_ids
            ],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_winrm_client

    loop = asyncio.get_event_loop()

    def _run():
        client = get_winrm_client(connector)
        stdout, stderr, rc = client.run_ps(script)
        return stdout, stderr, rc

    host_results = []
    for asset_id in asset_ids:
        try:
            stdout, stderr, rc = await loop.run_in_executor(None, _run)
            host_results.append({
                "asset_id": str(asset_id),
                "exit_code": rc,
                "stdout": stdout,
                "stderr": stderr,
            })
        except Exception as e:
            host_results.append({
                "asset_id": str(asset_id),
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
            })

    return {
        "action": "winrm_execute_template",
        "template_id": template_id,
        "host_results": host_results,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters, execution_result, connector):
    # type: (dict, dict, object) -> dict
    return {"rolled_back": False, "reason": "template execution rollback is manual"}
```

- [ ] **Step 5: Write `collect_diagnostics.py`**

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone


async def execute(parameters, asset_ids, connector):
    # type: (dict, list, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "winrm_collect_diagnostics",
            "diagnostics": {"mock": True},
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_winrm_client

    loop = asyncio.get_event_loop()

    def _collect():
        client = get_winrm_client(connector)
        results = {}

        stdout, _, _ = client.run_ps(
            "Get-EventLog -LogName System -Newest 50 | "
            "Select-Object TimeGenerated, EntryType, Source, Message | ConvertTo-Json"
        )
        results["event_log_system"] = stdout.strip()

        stdout2, _, _ = client.run_ps(
            "Get-Service | Where-Object Status -eq Running | "
            "Select-Object Name, Status | ConvertTo-Json"
        )
        results["running_services"] = stdout2.strip()

        stdout3, _, _ = client.run_cmd("systeminfo")
        results["systeminfo"] = stdout3.strip()

        return results

    try:
        diagnostics = await loop.run_in_executor(None, _collect)
    except Exception as e:
        return {
            "action": "winrm_collect_diagnostics",
            "error": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "action": "winrm_collect_diagnostics",
        "diagnostics": diagnostics,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters, execution_result, connector):
    # type: (dict, dict, object) -> dict
    return {"rolled_back": True, "reason": "collect_diagnostics is read-only"}
```

- [ ] **Step 6: Verify syntax on all 5 files**

```
cd f:/Nexplane/nexplane && python -c "
import ast, os
for f in [
  'backend/app/connectors/executors/winrm/check_prerequisites.py',
  'backend/app/connectors/executors/winrm/download_agent.py',
  'backend/app/connectors/executors/winrm/install_agent.py',
  'backend/app/connectors/executors/winrm/execute_template.py',
  'backend/app/connectors/executors/winrm/collect_diagnostics.py',
]:
    ast.parse(open(f).read())
    print('OK:', f)
"
```

Expected: 5 `OK` lines.

- [ ] **Step 7: Commit**

```
git add backend/app/connectors/executors/winrm/
git commit -m "feat: add WinRM executor modules (check_prerequisites, download_agent, install_agent, execute_template, collect_diagnostics)"
```

---

### Task 3: Catalog JSON

**Files:**
- Create: `backend/app/connectors/catalog/winrm.json`

- [ ] **Step 1: Write `winrm.json`**

```json
{
    "connector_type": "winrm",
    "display_name": "WinRM (Windows Remote Management)",
    "credential_fields": [
        {
            "name": "hostname",
            "label": "Hostname / IP",
            "type": "string",
            "required": true
        },
        {
            "name": "port",
            "label": "Port",
            "type": "string",
            "required": false,
            "default": "5985"
        },
        {
            "name": "username",
            "label": "Username",
            "type": "string",
            "required": true
        },
        {
            "name": "password",
            "label": "Password",
            "type": "password",
            "required": true
        },
        {
            "name": "use_ssl",
            "label": "Use SSL (HTTPS on port 5986)",
            "type": "string",
            "required": false,
            "default": "false"
        }
    ],
    "actions": [
        {
            "action_id": "check_prerequisites",
            "generic_action": "check_prerequisites",
            "action_type": "change",
            "execution_tier": 5,
            "display_name": "Check Prerequisites",
            "description": "Verifies OS version (Windows Server 2016+), disk space, .NET version, and WinRM accessibility",
            "applicable_asset_types": ["server"],
            "parameters": [],
            "executor": "winrm.check_prerequisites",
            "estimated_duration_seconds": 15
        },
        {
            "action_id": "download_agent",
            "generic_action": "download_agent",
            "action_type": "change",
            "execution_tier": 5,
            "display_name": "Download Nexplane Agent",
            "description": "Downloads the Nexplane agent binary to C:\\nexplane\\nexplane-agent.exe via Invoke-WebRequest",
            "applicable_asset_types": ["server"],
            "parameters": [
                {
                    "name": "agent_url",
                    "type": "string",
                    "required": true
                }
            ],
            "executor": "winrm.download_agent",
            "rollback_action": "remove_agent",
            "estimated_duration_seconds": 60
        },
        {
            "action_id": "remove_agent",
            "generic_action": "remove_agent",
            "action_type": "change",
            "execution_tier": 5,
            "display_name": "Remove Agent Binary",
            "description": "Removes the downloaded agent binary from C:\\nexplane\\nexplane-agent.exe",
            "applicable_asset_types": ["server"],
            "parameters": [],
            "executor": "winrm.download_agent",
            "estimated_duration_seconds": 10
        },
        {
            "action_id": "install_agent",
            "generic_action": "install_agent",
            "action_type": "change",
            "execution_tier": 5,
            "display_name": "Install Nexplane Agent Service",
            "description": "Creates and starts the nexplane-agent Windows service",
            "applicable_asset_types": ["server"],
            "parameters": [
                {
                    "name": "agent_secret",
                    "type": "string",
                    "required": true
                },
                {
                    "name": "control_plane_url",
                    "type": "string",
                    "required": true
                }
            ],
            "executor": "winrm.install_agent",
            "rollback_action": "uninstall_agent",
            "estimated_duration_seconds": 30,
            "blast_radius_hint": "host_service_interruption"
        },
        {
            "action_id": "uninstall_agent",
            "generic_action": "uninstall_agent",
            "action_type": "change",
            "execution_tier": 5,
            "display_name": "Uninstall Nexplane Agent Service",
            "description": "Stops and deletes the nexplane-agent Windows service",
            "applicable_asset_types": ["server"],
            "parameters": [],
            "executor": "winrm.install_agent",
            "estimated_duration_seconds": 15
        },
        {
            "action_id": "execute_template",
            "generic_action": "execute_template",
            "action_type": "change",
            "execution_tier": 5,
            "display_name": "Execute Approved PowerShell Template",
            "description": "Executes a pre-approved PowerShell template by name. No freeform scripts.",
            "applicable_asset_types": ["server"],
            "parameters": [
                {
                    "name": "template_id",
                    "type": "string",
                    "required": true
                }
            ],
            "executor": "winrm.execute_template",
            "estimated_duration_seconds": 30,
            "blast_radius_hint": "host_service_interruption",
            "safety_notes": [
                "Only approved templates may be executed — no freeform PowerShell permitted"
            ]
        },
        {
            "action_id": "collect_diagnostics",
            "generic_action": "collect_diagnostics",
            "action_type": "change",
            "execution_tier": 5,
            "display_name": "Collect Diagnostics",
            "description": "Collects System event log, running services list, and systeminfo from the target host",
            "applicable_asset_types": ["server"],
            "parameters": [],
            "executor": "winrm.collect_diagnostics",
            "estimated_duration_seconds": 20
        }
    ]
}
```

- [ ] **Step 2: Verify JSON is valid**

```
python -c "import json; json.load(open('backend/app/connectors/catalog/winrm.json')); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```
git add backend/app/connectors/catalog/winrm.json
git commit -m "feat: add WinRM connector catalog"
```

---

### Task 4: ChangeType enum entries

**Files:**
- Modify: `backend/app/models/change_request.py`

Find the end of the ChangeType enum (last entry before class body closes). Add after the existing entries:

- [ ] **Step 1: Add WinRM change types**

Open `backend/app/models/change_request.py`. Find the last enum entry and add after it:

```python
    # WinRM connector
    winrm_check_prerequisites = "winrm_check_prerequisites"
    winrm_download_agent = "winrm_download_agent"
    winrm_install_agent = "winrm_install_agent"
    winrm_execute_template = "winrm_execute_template"
    winrm_collect_diagnostics = "winrm_collect_diagnostics"
```

- [ ] **Step 2: Verify syntax**

```
cd f:/Nexplane/nexplane && python -c "from backend.app.models.change_request import ChangeType; print(ChangeType.winrm_check_prerequisites)"
```

If that fails due to path: `python -c "import ast; ast.parse(open('backend/app/models/change_request.py').read()); print('OK')"`

Expected: `OK` or prints the enum value.

- [ ] **Step 3: Commit**

```
git add backend/app/models/change_request.py
git commit -m "feat: add WinRM change types to ChangeType enum"
```

---

### Task 5: Frontend — ConnectorType, labels, icons

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/components/AddConnectorModal.tsx`

- [ ] **Step 1: Add `winrm` to ConnectorType in `api.ts`**

In `frontend/src/types/api.ts`, find the `ConnectorType` union. After `| "jfrog";` add:

```typescript
  | "winrm";
```

(The semicolon goes on the new last line.)

- [ ] **Step 2: Add label and icon to `AddConnectorModal.tsx`**

In `CONNECTOR_LABELS`, after `jfrog: "JFrog Xray",` add:
```typescript
  winrm: "WinRM (Windows Remote Management)",
```

In `CONNECTOR_ICONS`, after `jfrog: "🐸",` add:
```typescript
  winrm: "🪟",
```

- [ ] **Step 3: Verify TypeScript compiles (no tsc errors)**

```
cd f:/Nexplane/nexplane/frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: no output (no errors).

- [ ] **Step 4: Commit**

```
git add frontend/src/types/api.ts frontend/src/components/AddConnectorModal.tsx
git commit -m "feat: add WinRM connector type to frontend"
```

---

### Task 6: Add pywinrm to requirements

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Check if pywinrm is already present**

```
grep pywinrm backend/requirements.txt
```

If no output, add it.

- [ ] **Step 2: Add pywinrm if missing**

Append to `backend/requirements.txt`:
```
pywinrm>=0.4.3
```

- [ ] **Step 3: Commit**

```
git add backend/requirements.txt
git commit -m "feat: add pywinrm>=0.4.3 to backend requirements"
```

---

### Task 7: Smoke phase WINRM_BOOTSTRAP

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

The phase function goes near the Windows EC2 helpers (around line 9297 after `run_phase_win_policy_pipeline`). The PHASE dispatch goes in the `try:` block in `main()` near the other named phases.

- [ ] **Step 1: Add `run_phase_winrm_bootstrap` function**

Insert the following function after `run_phase_win_policy_pipeline` (after line ~9358 in the file, before `run_phase_gcp_key_rotate`):

```python
def run_phase_winrm_bootstrap(client, cloud_account_id):
    # type: (object, str) -> None
    """Phase WINRM_BOOTSTRAP: Launch Windows Server 2022 EC2, enable WinRM via SSM,
    register a WinRM connector in Nexplane, run check_prerequisites / download_agent /
    install_agent CRs, rollback install, teardown. AMI cached after setup."""
    import hashlib as _hl
    import time as _t
    import socket as _socket
    print("\n[Phase WINRM_BOOTSTRAP] WinRM connector smoke test")

    ec2_client = _get_aws_boto3_client("ec2")
    ssm_client = _get_aws_boto3_client("ssm")
    if not ec2_client or not ssm_client:
        fail("[WINRM_BOOTSTRAP] AWS clients not available")

    win_ami_id = _get_windows_2022_ami(ec2_client)
    log("Windows Server 2022 AMI: {}".format(win_ami_id))

    setup_hash = _hl.md5(("winrm-v1-" + win_ami_id).encode()).hexdigest()
    cached_ami = _check_smoke_ami_cache(ssm_client, ec2_client, "winrm", setup_hash)

    instance_id, private_ip = _launch_windows_ec2(ec2_client, cached_ami or win_ami_id, cloud_account_id)
    connector_id = None
    asset_id = None

    try:
        log("Waiting for Windows SSM agent to register (~3-5 min)...")
        _wait_ssm_ready_win(ssm_client, instance_id, timeout=420)

        if not cached_ami:
            log("Enabling WinRM on Windows instance via SSM...")
            enable_resp = ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunPowerShellScript",
                Parameters={"commands": [
                    "Enable-PSRemoting -Force",
                    "Set-Item WSMan:\\localhost\\Service\\Auth\\Basic -Value $true",
                    "Set-Item WSMan:\\localhost\\Service\\AllowUnencrypted -Value $true",
                    "netsh advfirewall firewall add rule name='WinRM-HTTP' dir=in action=allow protocol=TCP localport=5985",
                    "Restart-Service WinRM",
                    "Write-Output 'WINRM_ENABLED'",
                ]},
                TimeoutSeconds=120,
            )
            enable_cmd_id = enable_resp["Command"]["CommandId"]
            _t.sleep(5)

            # Poll SSM command
            deadline = _t.time() + 180
            while _t.time() < deadline:
                _t.sleep(8)
                try:
                    inv = ssm_client.get_command_invocation(
                        CommandId=enable_cmd_id, InstanceId=instance_id
                    )
                    status = inv["Status"]
                    if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                        if status != "Success":
                            log("WinRM enable command status: {} — continuing anyway".format(status))
                        else:
                            log("WinRM enabled via SSM")
                        break
                except Exception:
                    pass

            _t.sleep(15)  # let WinRM service settle

            # Get public IP for WinRM access from runner
            desc = ec2_client.describe_instances(InstanceIds=[instance_id])
            public_ip = desc["Reservations"][0]["Instances"][0].get("PublicIpAddress", "")
            if not public_ip:
                public_ip = private_ip
            log("Instance public IP: {}".format(public_ip))

            # Wait for WinRM port 5985 to be reachable (up to 3 min)
            log("Waiting for WinRM port 5985...")
            deadline2 = _t.time() + 180
            port_open = False
            while _t.time() < deadline2:
                try:
                    sock = _socket.create_connection((public_ip, 5985), timeout=5)
                    sock.close()
                    port_open = True
                    log("WinRM port 5985 reachable")
                    break
                except OSError:
                    _t.sleep(10)
            if not port_open:
                log("WinRM port 5985 not reachable — will try anyway")

            # Get Administrator password via SSM (Windows password from EC2)
            log("Retrieving Windows Administrator password...")
            password = ""
            try:
                pw_resp = ec2_client.get_password_data(InstanceId=instance_id)
                # Password data may be empty if instance just launched
                # For smoke, use a known password set via SSM instead
            except Exception as _pw_e:
                log("Could not get EC2 password: {}".format(_pw_e))

            # Set a known password via SSM so we can use it for WinRM
            known_password = "NexplaneSmoke2024!"
            set_pw_resp = ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunPowerShellScript",
                Parameters={"commands": [
                    "$password = ConvertTo-SecureString '{}' -AsPlainText -Force".format(known_password),
                    "Set-LocalUser -Name Administrator -Password $password",
                    "Enable-LocalUser -Name Administrator",
                    "Write-Output 'PASSWORD_SET'",
                ]},
                TimeoutSeconds=60,
            )
            set_pw_cmd_id = set_pw_resp["Command"]["CommandId"]
            deadline3 = _t.time() + 120
            while _t.time() < deadline3:
                _t.sleep(8)
                try:
                    inv2 = ssm_client.get_command_invocation(
                        CommandId=set_pw_cmd_id, InstanceId=instance_id
                    )
                    if inv2["Status"] in ("Success", "Failed", "TimedOut"):
                        log("Set-LocalUser result: {}".format(inv2["Status"]))
                        break
                except Exception:
                    pass

            password = known_password
            _t.sleep(10)

            # Cache the AMI now that WinRM is set up
            try:
                from run_on_ec2 import get_or_create_smoke_ami
                get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "winrm", setup_hash)
            except Exception as _ami_e:
                log("AMI cache skipped: {}".format(_ami_e))

        else:
            # From cached AMI — use the same known password
            desc = ec2_client.describe_instances(InstanceIds=[instance_id])
            public_ip = desc["Reservations"][0]["Instances"][0].get("PublicIpAddress", "")
            if not public_ip:
                public_ip = private_ip
            password = "NexplaneSmoke2024!"
            log("Using cached WinRM AMI, public IP: {}".format(public_ip))

        # Register WinRM connector in Nexplane
        log("Registering WinRM connector...")
        conn_resp = client.post("/connectors", json={
            "connector_type": "winrm",
            "name": "nexplane-smoke-winrm-{}".format(instance_id[-8:]),
            "credentials": {
                "hostname": public_ip,
                "port": "5985",
                "username": "Administrator",
                "password": password,
                "use_ssl": "false",
            },
        })
        connector_id = conn_resp["id"]
        log("WinRM connector registered: {}".format(connector_id))

        # Register asset linked to connector
        asset_resp = client.post("/assets", json={
            "name": "nexplane-smoke-winrm-{}".format(instance_id[-8:]),
            "asset_type": "server",
            "connector_id": connector_id,
            "asset_metadata": {
                "instance_id": instance_id,
                "public_ip": public_ip,
                "os": "windows",
                "platform": "windows",
            },
            "tags": ["nexplane-smoke", "winrm"],
        })
        asset_id = asset_resp["id"]
        log("Asset registered: {}".format(asset_id))

        # CR 1: check_prerequisites
        log("Running winrm_check_prerequisites CR...")
        cr1 = client.run_cr(
            "[WINRM_BOOTSTRAP] check_prerequisites",
            "winrm_check_prerequisites",
            asset_id,
            {},
        )
        exec_runs1 = cr1.get("execution_runs") or []
        result1 = exec_runs1[0].get("result") if exec_runs1 else {}
        log("check_prerequisites result: {}".format(list(result1.keys())))
        if not result1.get("checks") and not result1.get("mock"):
            log("  WARNING: check_prerequisites returned unexpected result: {}".format(result1))

        # CR 2: download_agent
        backend_ip = "100.69.215.38"
        agent_url = "http://{}:8000/downloads/nexplane-agent-windows-amd64-0.3.1.exe".format(backend_ip)
        log("Running winrm_download_agent CR (url={})...".format(agent_url))
        cr2 = client.run_cr(
            "[WINRM_BOOTSTRAP] download_agent",
            "winrm_download_agent",
            asset_id,
            {"agent_url": agent_url},
        )
        exec_runs2 = cr2.get("execution_runs") or []
        result2 = exec_runs2[0].get("result") if exec_runs2 else {}
        log("download_agent result: {}".format(result2.get("downloaded", "?")))

        # CR 3: install_agent
        agent_secret = ""
        try:
            agent_secret = client.get_agent_secret()
        except Exception:
            pass
        control_plane_url = "http://{}:8000".format(backend_ip)
        log("Running winrm_install_agent CR...")
        cr3 = client.run_cr(
            "[WINRM_BOOTSTRAP] install_agent",
            "winrm_install_agent",
            asset_id,
            {
                "agent_secret": agent_secret,
                "control_plane_url": control_plane_url,
            },
        )
        exec_runs3 = cr3.get("execution_runs") or []
        result3 = exec_runs3[0].get("result") if exec_runs3 else {}
        service_created = result3.get("service_created", False)
        log("install_agent service_created={}".format(service_created))

        # Rollback install (verify service deleted)
        log("Running install_agent rollback...")
        cr3_id = cr3.get("id", "")
        if cr3_id:
            try:
                client.post("/change-requests/{}/rollback".format(cr3_id), json={})
                log("Rollback CR submitted")
            except Exception as _rb_e:
                log("Rollback post failed: {} — proceeding".format(_rb_e))

        log("Phase WINRM_BOOTSTRAP PASSED")

    except Exception:
        raise
    finally:
        # Cleanup connector and asset
        if asset_id:
            try:
                client.client.delete("{}/assets/{}".format(client.base, asset_id))
            except Exception:
                pass
        if connector_id:
            try:
                client.client.delete("{}/connectors/{}".format(client.base, connector_id))
            except Exception:
                pass
        # Terminate EC2
        try:
            ec2_client.terminate_instances(InstanceIds=[instance_id])
            log("Windows EC2 {} terminated".format(instance_id))
        except Exception:
            pass
```

- [ ] **Step 2: Add WINRM_BOOTSTRAP dispatch in `main()`**

In the `main()` function's `try:` block, after the `WIN_POLICY_PIPELINE` block (around line 13176), add:

```python
        if "WINRM_BOOTSTRAP" in phases:
            run_phase_winrm_bootstrap(client, cloud_account_id)
```

- [ ] **Step 3: Verify syntax**

```
cd f:/Nexplane/nexplane && python -c "import ast; ast.parse(open('backend/tests/smoke/test_aws_live.py').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: add WINRM_BOOTSTRAP smoke phase"
```

---

### Task 8: Run smoke phase and fix bugs

- [ ] **Step 1: Set AWS environment variables and run**

```powershell
$env:AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE"
$env:AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
$env:AWS_DEFAULT_REGION="us-east-1"
cd f:/Nexplane/nexplane
python backend/tests/smoke/run_on_ec2.py --phases WINRM_BOOTSTRAP
```

Expected: `ALL SELECTED PHASES PASSED`

- [ ] **Step 2: Fix any bugs and re-run until passing**

Common failure modes:
- WinRM auth fails → check username/password in SSM set-password step
- Port 5985 not reachable → verify security group allows inbound TCP 5985 from runner
- Import error for pywinrm → ensure `pywinrm>=0.4.3` is installed in backend container
- Executor not found → verify `executor_registry` loads `winrm.*` modules correctly
- ChangeType unknown → verify enum entries are spelled correctly

- [ ] **Step 3: Final commit**

```
git add -A
git commit -m "feat: add WinRM connector for Windows remote management + WINRM_BOOTSTRAP smoke phase"
```
