# Front-to-Back Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 27 silent executor stubs so OS hardening CRs actually execute on hosts, add missing Windows hardening Go commands, then ship four operator-facing phases: Foundation, Scale, Identity, and Proactive Hardening.

**Architecture:** The Go agent is fully implemented for all hardening capabilities. Python executor files call `dispatch_agent_job()` to send work to the agent. The entire Phase 0 fix is replacing stub return values with real dispatch calls — the canonical pattern lives in `backend/app/connectors/executors/nexplane_agent/change_ip.py`. Phases 1–4 are backend API + frontend + smoke test work built on top of the corrected executor layer.

**Tech Stack:** FastAPI (Python), React/React Query (TypeScript), Go 1.22 agent, PostgreSQL, AWS SDK (boto3), SSM for smoke test host verification.

---

## Phase 0A: Linux Executor Wire-Up (14 stubs → real dispatch)

### Task 1: Wire all 14 Linux hardening executors

**Files (modify all 14):**
- `backend/app/connectors/executors/nexplane_agent/configure_seccomp.py`
- `backend/app/connectors/executors/nexplane_agent/configure_apparmor.py`
- `backend/app/connectors/executors/nexplane_agent/configure_selinux.py`
- `backend/app/connectors/executors/nexplane_agent/apply_sysctl_hardening.py`
- `backend/app/connectors/executors/nexplane_agent/configure_host_firewall.py`
- `backend/app/connectors/executors/nexplane_agent/blacklist_kernel_modules.py`
- `backend/app/connectors/executors/nexplane_agent/harden_mount_options.py`
- `backend/app/connectors/executors/nexplane_agent/deploy_auditd_rules.py`
- `backend/app/connectors/executors/nexplane_agent/setup_file_integrity_monitoring.py`
- `backend/app/connectors/executors/nexplane_agent/audit_os_security_posture.py` *(read-only, no rollback)*
- `backend/app/connectors/executors/nexplane_agent/deploy_ebpf_policy.py`
- `backend/app/connectors/executors/nexplane_agent/configure_ebpf_security_policy.py`
- `backend/app/connectors/executors/nexplane_agent/audit_ebpf_posture.py` *(read-only, no rollback)*
- `backend/app/connectors/executors/nexplane_agent/harden_ssh.py`
- `backend/app/connectors/executors/nexplane_agent/configure_pam.py`
- `backend/app/connectors/executors/nexplane_agent/audit_scheduled_tasks.py` *(read-only)*

- [ ] **Step 1: Write the failing unit test that proves each stub doesn't call dispatch**

```python
# backend/app/tests/test_hardening_executors_dispatch.py
import pytest
from unittest.mock import AsyncMock, patch

HARDENING_EXECUTORS = [
    ("configure_seccomp", "configure_seccomp"),
    ("configure_apparmor", "configure_apparmor"),
    ("configure_selinux", "configure_selinux"),
    ("apply_sysctl_hardening", "apply_sysctl_hardening"),
    ("configure_host_firewall", "configure_host_firewall"),
    ("blacklist_kernel_modules", "blacklist_kernel_modules"),
    ("harden_mount_options", "harden_mount_options"),
    ("deploy_auditd_rules", "deploy_auditd_rules"),
    ("setup_file_integrity_monitoring", "setup_file_integrity_monitoring"),
    ("deploy_ebpf_policy", "deploy_ebpf_policy"),
    ("configure_ebpf_security_policy", "configure_ebpf_security_policy"),
    ("harden_ssh", "harden_ssh"),
    ("configure_pam", "configure_pam"),
]

@pytest.mark.parametrize("module_name,expected_command", HARDENING_EXECUTORS)
@pytest.mark.asyncio
async def test_executor_dispatches_to_agent(module_name, expected_command):
    import importlib
    mod = importlib.import_module(
        f"app.connectors.executors.nexplane_agent.{module_name}"
    )
    mock_dispatch = AsyncMock(return_value={"status": "ok", "snapshot_id": "snap-123"})
    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await mod.execute({"service_name": "nginx"}, ["asset-uuid-1"], None)

    mock_dispatch.assert_called_once()
    call_kwargs = mock_dispatch.call_args
    assert call_kwargs.kwargs.get("command") == expected_command or call_kwargs.args[0] == expected_command
    assert result.get("_asset_ids") == ["asset-uuid-1"]

READ_ONLY_EXECUTORS = [
    "audit_os_security_posture",
    "audit_ebpf_posture",
    "audit_scheduled_tasks",
]

@pytest.mark.parametrize("module_name", READ_ONLY_EXECUTORS)
@pytest.mark.asyncio
async def test_readonly_executor_dispatches_to_agent(module_name):
    import importlib
    mod = importlib.import_module(
        f"app.connectors.executors.nexplane_agent.{module_name}"
    )
    mock_dispatch = AsyncMock(return_value={"status": "ok"})
    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await mod.execute({}, ["asset-uuid-1"], None)

    mock_dispatch.assert_called_once()
```

- [ ] **Step 2: Run test to verify they all fail (stubs don't call dispatch)**

```bash
docker compose exec backend pytest app/tests/test_hardening_executors_dispatch.py -v 2>&1 | tail -30
```
Expected: 16 FAILED — `assert mock_dispatch.called` fails because stubs return hardcoded dicts.

- [ ] **Step 3: Rewrite all 14 executor files (+ 3 read-only) using the canonical pattern**

Replace **every** executor listed above with this template (vary only the `command` string and `timeout_seconds`):

**`configure_seccomp.py`** (command: `"configure_seccomp"`, timeout: 120s):
```python
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await dispatch_agent_job(
        command="configure_seccomp",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await dispatch_agent_job(
        command="configure_seccomp",
        parameters={"action": "restore", "snapshot_id": execution_result.get("snapshot_id", "")},
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
```

Apply identical pattern to all 14 with these command strings and timeouts:

| File | `command=` | `timeout_seconds=` | Has rollback |
|------|-----------|-------------------|--------------|
| `configure_seccomp.py` | `"configure_seccomp"` | 120 | Yes |
| `configure_apparmor.py` | `"configure_apparmor"` | 120 | Yes |
| `configure_selinux.py` | `"configure_selinux"` | 120 | Yes |
| `apply_sysctl_hardening.py` | `"apply_sysctl_hardening"` | 60 | Yes |
| `configure_host_firewall.py` | `"configure_host_firewall"` | 60 | Yes |
| `blacklist_kernel_modules.py` | `"blacklist_kernel_modules"` | 60 | Yes |
| `harden_mount_options.py` | `"harden_mount_options"` | 60 | Yes |
| `deploy_auditd_rules.py` | `"deploy_auditd_rules"` | 60 | Yes |
| `setup_file_integrity_monitoring.py` | `"setup_file_integrity_monitoring"` | 120 | Yes |
| `deploy_ebpf_policy.py` | `"deploy_ebpf_policy"` | 120 | Yes |
| `configure_ebpf_security_policy.py` | `"configure_ebpf_security_policy"` | 120 | Yes |
| `harden_ssh.py` | `"harden_ssh"` | 60 | Yes |
| `configure_pam.py` | `"configure_pam"` | 60 | Yes |

For the three read-only executors, omit the rollback function and do not set `_asset_ids`:

```python
# audit_os_security_posture.py, audit_ebpf_posture.py, audit_scheduled_tasks.py
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await dispatch_agent_job(
        command="<command_name>",   # audit_os_security_posture | audit_ebpf_posture | audit_scheduled_tasks
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=60,
    )
```

- [ ] **Step 4: Run tests — expect all 16 to pass**

```bash
docker compose exec backend pytest app/tests/test_hardening_executors_dispatch.py -v 2>&1 | tail -20
```
Expected: 16 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/configure_seccomp.py \
        backend/app/connectors/executors/nexplane_agent/configure_apparmor.py \
        backend/app/connectors/executors/nexplane_agent/configure_selinux.py \
        backend/app/connectors/executors/nexplane_agent/apply_sysctl_hardening.py \
        backend/app/connectors/executors/nexplane_agent/configure_host_firewall.py \
        backend/app/connectors/executors/nexplane_agent/blacklist_kernel_modules.py \
        backend/app/connectors/executors/nexplane_agent/harden_mount_options.py \
        backend/app/connectors/executors/nexplane_agent/deploy_auditd_rules.py \
        backend/app/connectors/executors/nexplane_agent/setup_file_integrity_monitoring.py \
        backend/app/connectors/executors/nexplane_agent/audit_os_security_posture.py \
        backend/app/connectors/executors/nexplane_agent/deploy_ebpf_policy.py \
        backend/app/connectors/executors/nexplane_agent/configure_ebpf_security_policy.py \
        backend/app/connectors/executors/nexplane_agent/audit_ebpf_posture.py \
        backend/app/connectors/executors/nexplane_agent/harden_ssh.py \
        backend/app/connectors/executors/nexplane_agent/configure_pam.py \
        backend/app/connectors/executors/nexplane_agent/audit_scheduled_tasks.py \
        backend/app/tests/test_hardening_executors_dispatch.py
git commit -m "fix: wire 14 Linux OS hardening executors to dispatch_agent_job (was: silent stubs)"
```

---

### Task 2: Wire Linux patch executors + register in agent dispatch table

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/apply_linux_patches.py`
- Modify: `backend/app/connectors/executors/nexplane_agent/audit_linux_patch_status.py`
- Modify: `agent/executor/executor.go` (add linuxpatch + winpatch imports and entries)

The `apply_linux_patches` and `apply_windows_patches` commands exist in the Go agent's `linuxpatch` and `winpatch` packages but are not registered in the executor dispatch table.

- [ ] **Step 1: Add linuxpatch and winpatch to executor.go**

In `agent/executor/executor.go`, add to the import block:
```go
"nexplane-agent/commands/linuxpatch"
"nexplane-agent/commands/winpatch"
```

In the `commandTable` map (near line 40), add:
```go
"apply_linux_patches":      linuxpatch.ApplyLinuxPatchesExecute,
"audit_linux_patch_status": linuxpatch.AuditLinuxPatchStatusExecute,
"apply_windows_patches":    winpatch.ApplyWindowsPatchesExecute,
"audit_windows_patch_status": winpatch.AuditWindowsPatchStatusExecute,
```

In the `rollbackTable` map, add:
```go
"apply_linux_patches":   linuxpatch.ApplyLinuxPatchesRollback,
"apply_windows_patches": winpatch.ApplyWindowsPatchesRollback,
```

- [ ] **Step 2: Wire the Python executors**

`backend/app/connectors/executors/nexplane_agent/apply_linux_patches.py`:
```python
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await dispatch_agent_job(
        command="apply_linux_patches",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=600,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # Linux patches cannot be automatically reversed; rollback is restore-from-snapshot
    return {"rolled_back": False, "reason": "patch rollback requires snapshot restore"}
```

`backend/app/connectors/executors/nexplane_agent/audit_linux_patch_status.py`:
```python
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await dispatch_agent_job(
        command="audit_linux_patch_status",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
```

- [ ] **Step 3: Build the agent to confirm no compile errors**

```bash
cd agent && go build ./... 2>&1
```
Expected: no output (clean build).

- [ ] **Step 4: Run the dispatch test for the new patch executors**

Add to `backend/app/tests/test_hardening_executors_dispatch.py` in the `HARDENING_EXECUTORS` list:
```python
("apply_linux_patches", "apply_linux_patches"),
("audit_linux_patch_status", "audit_linux_patch_status"),
```
Run: `docker compose exec backend pytest app/tests/test_hardening_executors_dispatch.py -v -k "patch" 2>&1 | tail -10`
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add agent/executor/executor.go \
        backend/app/connectors/executors/nexplane_agent/apply_linux_patches.py \
        backend/app/connectors/executors/nexplane_agent/audit_linux_patch_status.py \
        backend/app/tests/test_hardening_executors_dispatch.py
git commit -m "fix: register linuxpatch/winpatch in agent dispatch table; wire Python patch executors"
```

---

## Phase 0B: Windows Executor Wire-Up (13 stubs → real dispatch)

### Task 3: Wire all 13 Windows hardening executors

**Files (modify all 13):**
- `backend/app/connectors/executors/nexplane_agent/configure_laps.py`
- `backend/app/connectors/executors/nexplane_agent/enable_credential_guard.py`
- `backend/app/connectors/executors/nexplane_agent/enforce_powershell_clm.py`
- `backend/app/connectors/executors/nexplane_agent/deploy_applocker_policy.py`
- `backend/app/connectors/executors/nexplane_agent/harden_smb.py`
- `backend/app/connectors/executors/nexplane_agent/enable_bitlocker.py`
- `backend/app/connectors/executors/nexplane_agent/configure_windows_firewall.py`
- `backend/app/connectors/executors/nexplane_agent/harden_tls_protocols.py`
- `backend/app/connectors/executors/nexplane_agent/harden_rdp.py`
- `backend/app/connectors/executors/nexplane_agent/configure_windows_audit_policy.py`
- `backend/app/connectors/executors/nexplane_agent/harden_registry.py`
- `backend/app/connectors/executors/nexplane_agent/apply_windows_patches.py`
- `backend/app/connectors/executors/nexplane_agent/audit_windows_patch_status.py`

- [ ] **Step 1: Add Windows executor tests to the test file**

Append to `backend/app/tests/test_hardening_executors_dispatch.py`:
```python
WINDOWS_HARDENING_EXECUTORS = [
    ("configure_laps", "configure_laps"),
    ("enable_credential_guard", "enable_credential_guard"),
    ("enforce_powershell_clm", "enforce_powershell_clm"),
    ("deploy_applocker_policy", "deploy_applocker_policy"),
    ("harden_smb", "harden_smb"),
    ("enable_bitlocker", "enable_bitlocker"),
    ("configure_windows_firewall", "configure_windows_firewall"),
    ("harden_tls_protocols", "harden_tls_protocols"),
    ("harden_rdp", "harden_rdp"),
    ("configure_windows_audit_policy", "configure_windows_audit_policy"),
    ("harden_registry", "harden_registry"),
    ("apply_windows_patches", "apply_windows_patches"),
]

@pytest.mark.parametrize("module_name,expected_command", WINDOWS_HARDENING_EXECUTORS)
@pytest.mark.asyncio
async def test_windows_executor_dispatches_to_agent(module_name, expected_command):
    import importlib
    mod = importlib.import_module(
        f"app.connectors.executors.nexplane_agent.{module_name}"
    )
    mock_dispatch = AsyncMock(return_value={"status": "ok", "snapshot_id": "snap-win-1"})
    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        mock_dispatch,
    ):
        result = await mod.execute({"profile": "cis_level1"}, ["win-asset-uuid-1"], None)

    mock_dispatch.assert_called_once()
    assert result.get("_asset_ids") == ["win-asset-uuid-1"]
```

- [ ] **Step 2: Run — expect all 12 to fail**

```bash
docker compose exec backend pytest app/tests/test_hardening_executors_dispatch.py -v -k "windows" 2>&1 | tail -20
```
Expected: 12 FAILED.

- [ ] **Step 3: Rewrite all 13 Windows executors**

Apply same template as Task 1. Command strings and timeouts:

| File | `command=` | `timeout_seconds=` | Has rollback |
|------|-----------|-------------------|--------------|
| `configure_laps.py` | `"configure_laps"` | 60 | Yes |
| `enable_credential_guard.py` | `"enable_credential_guard"` | 60 | Yes |
| `enforce_powershell_clm.py` | `"enforce_powershell_clm"` | 60 | Yes |
| `deploy_applocker_policy.py` | `"deploy_applocker_policy"` | 60 | Yes |
| `harden_smb.py` | `"harden_smb"` | 60 | Yes |
| `enable_bitlocker.py` | `"enable_bitlocker"` | 300 | Yes |
| `configure_windows_firewall.py` | `"configure_windows_firewall"` | 60 | Yes |
| `harden_tls_protocols.py` | `"harden_tls_protocols"` | 60 | Yes |
| `harden_rdp.py` | `"harden_rdp"` | 60 | Yes |
| `configure_windows_audit_policy.py` | `"configure_windows_audit_policy"` | 60 | Yes |
| `harden_registry.py` | `"harden_registry"` | 60 | Yes |
| `apply_windows_patches.py` | `"apply_windows_patches"` | 600 | No — returns `{"rolled_back": False, "reason": "patch rollback requires snapshot restore"}` |
| `audit_windows_patch_status.py` | `"audit_windows_patch_status"` | 120 | No — read-only |

- [ ] **Step 4: Run all executor dispatch tests**

```bash
docker compose exec backend pytest app/tests/test_hardening_executors_dispatch.py -v 2>&1 | tail -10
```
Expected: 30 PASSED (16 Linux + 12 Windows + 2 patch).

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/configure_laps.py \
        backend/app/connectors/executors/nexplane_agent/enable_credential_guard.py \
        backend/app/connectors/executors/nexplane_agent/enforce_powershell_clm.py \
        backend/app/connectors/executors/nexplane_agent/deploy_applocker_policy.py \
        backend/app/connectors/executors/nexplane_agent/harden_smb.py \
        backend/app/connectors/executors/nexplane_agent/enable_bitlocker.py \
        backend/app/connectors/executors/nexplane_agent/configure_windows_firewall.py \
        backend/app/connectors/executors/nexplane_agent/harden_tls_protocols.py \
        backend/app/connectors/executors/nexplane_agent/harden_rdp.py \
        backend/app/connectors/executors/nexplane_agent/configure_windows_audit_policy.py \
        backend/app/connectors/executors/nexplane_agent/harden_registry.py \
        backend/app/connectors/executors/nexplane_agent/apply_windows_patches.py \
        backend/app/connectors/executors/nexplane_agent/audit_windows_patch_status.py \
        backend/app/tests/test_hardening_executors_dispatch.py
git commit -m "fix: wire 13 Windows OS hardening executors to dispatch_agent_job (was: silent stubs)"
```

---

## Phase 0C: New Go Agent Commands (Windows: WDAC, ASR, Sysmon; Linux: seccomp_learn, iptables_log_baseline)

### Task 4: WDAC audit and enforce Go commands

**Files:**
- Create: `agent/commands/winharden/wdac_windows.go`
- Modify: `agent/commands/winharden/winharden.go` (add exported wrappers)
- Modify: `agent/commands/winharden/winharden_other.go` (add stub stubs for non-Windows)
- Modify: `agent/executor/executor.go` (register commands)
- Create: `agent/commands/winharden/wdac_other.go`

- [ ] **Step 1: Write wdac_windows.go**

```go
//go:build windows

package winharden

import (
	"fmt"
	"os"
	"strings"
	"time"
)

const wdacPolicyPath = `C:\Windows\System32\CodeIntegrity\SIPolicy.p7b`
const wdacXMLPath = `C:\Windows\System32\CodeIntegrity\nexplane-SIPolicy.xml`
const wdacEventLog = `Microsoft-Windows-CodeIntegrity/Operational`

// wdacAuditModeXML is a minimal WDAC policy allowing everything but logging unsigned/untrusted code.
const wdacAuditModeXML = `<?xml version="1.0" encoding="utf-8"?>
<SiPolicy xmlns="urn:schemas-microsoft-com:sipolicy">
  <VersionEx>10.0.0.0</VersionEx>
  <PolicyTypeID>{A244370E-44C9-4C06-B551-F6016E563076}</PolicyTypeID>
  <PlatformID>{2E07F7E4-194C-4D20-B96C-134C44A9F1A5}</PlatformID>
  <Rules>
    <Rule><Option>Enabled:Audit Mode</Option></Rule>
    <Rule><Option>Enabled:Advanced Boot Options Menu</Option></Rule>
  </Rules>
  <EKUs/>
  <FileRules/>
  <Signers/>
  <SigningScenarios>
    <SigningScenario Value="131" ID="ID_SIGNINGSCENARIO_DRIVERS" FriendlyName="Drivers">
      <ProductSigners/>
    </SigningScenario>
    <SigningScenario Value="12" ID="ID_SIGNINGSCENARIO_WINDOWS" FriendlyName="User Mode">
      <ProductSigners/>
    </SigningScenario>
  </SigningScenarios>
  <UpdatePolicySigners/>
  <CiSigners/>
  <HvciOptions>0</HvciOptions>
</SiPolicy>`

func wdacAuditExecuteOS(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}

	// Write and apply audit-mode policy
	if err := os.WriteFile(wdacXMLPath, []byte(wdacAuditModeXML), 0644); err != nil {
		return nil, fmt.Errorf("write WDAC XML: %w", err)
	}
	if out, err := runPS(fmt.Sprintf(
		`ConvertFrom-CIPolicy -XmlFilePath "%s" -BinaryFilePath "%s"`, wdacXMLPath, wdacPolicyPath,
	)); err != nil {
		return nil, fmt.Errorf("ConvertFrom-CIPolicy: %s: %w", out, err)
	}
	// Refresh policy
	if out, err := runPS(`Invoke-CimMethod -Namespace root/Microsoft/Windows/CI -ClassName PS_UpdateAndCompareCIPolicy -MethodName Update -Arguments @{FilePath="` + wdacPolicyPath + `"}`); err != nil {
		// Non-fatal — policy may reload on next boot
		_ = out
	}

	// Collect events during duration window
	time.Sleep(time.Duration(duration) * time.Second)
	out, _ := runPS(fmt.Sprintf(
		`Get-WinEvent -LogName "%s" -FilterXPath "*[System[EventID=3076]]" -MaxEvents 100 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Message`,
		wdacEventLog,
	))

	events := parseLines(string(out))
	return map[string]any{
		"action":           "wdac_audit",
		"duration_seconds": int(duration),
		"audit_events":     events,
		"event_count":      len(events),
		"policy_path":      wdacPolicyPath,
	}, nil
}

func wdacEnforceExecuteOS(params map[string]any) (map[string]any, error) {
	policyContent, _ := params["policy_xml"].(string)
	if policyContent == "" {
		return nil, fmt.Errorf("policy_xml is required for wdac_enforce")
	}
	if err := os.WriteFile(wdacXMLPath, []byte(policyContent), 0644); err != nil {
		return nil, fmt.Errorf("write WDAC XML: %w", err)
	}
	if out, err := runPS(fmt.Sprintf(
		`ConvertFrom-CIPolicy -XmlFilePath "%s" -BinaryFilePath "%s"`, wdacXMLPath, wdacPolicyPath,
	)); err != nil {
		return nil, fmt.Errorf("ConvertFrom-CIPolicy: %s: %w", out, err)
	}
	out, _ := runPS(`Invoke-CimMethod -Namespace root/Microsoft/Windows/CI -ClassName PS_UpdateAndCompareCIPolicy -MethodName Update -Arguments @{FilePath="` + wdacPolicyPath + `"}`)
	return map[string]any{
		"action":      "wdac_enforce",
		"policy_path": wdacPolicyPath,
		"output":      strings.TrimSpace(string(out)),
	}, nil
}

func wdacRollbackOS(_ map[string]any) (map[string]any, error) {
	// Remove the policy file; reboot will revert to no WDAC policy
	_ = os.Remove(wdacPolicyPath)
	_ = os.Remove(wdacXMLPath)
	return map[string]any{"action": "wdac_rollback", "status": "policy_removed_reboot_required"}, nil
}

func parseLines(s string) []string {
	var lines []string
	for _, l := range strings.Split(s, "\n") {
		l = strings.TrimSpace(l)
		if l != "" {
			lines = append(lines, l)
		}
	}
	return lines
}
```

- [ ] **Step 2: Add exported wrappers to winharden.go**

```go
func WDACauditExecute(params map[string]any) (map[string]any, error) {
	return wdacAuditExecuteOS(params)
}

func WDACenforceExecute(params map[string]any) (map[string]any, error) {
	return wdacEnforceExecuteOS(params)
}

func WDACrollback(params map[string]any) (map[string]any, error) {
	return wdacRollbackOS(params)
}
```

- [ ] **Step 3: Add non-Windows stubs to winharden_other.go**

```go
//go:build !windows

package winharden

func wdacAuditExecuteOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "WDAC is Windows-only"}, nil
}

func wdacEnforceExecuteOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "WDAC is Windows-only"}, nil
}

func wdacRollbackOS(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "WDAC is Windows-only"}, nil
}
```

- [ ] **Step 4: Register in executor.go**

Add to `commandTable`:
```go
"wdac_audit":   winharden.WDACauditExecute,
"wdac_enforce": winharden.WDACenforceExecute,
```
Add to `rollbackTable`:
```go
"wdac_audit":   winharden.WDACrollback,
"wdac_enforce": winharden.WDACrollback,
```

- [ ] **Step 5: Create Python executors for wdac_audit and wdac_enforce**

`backend/app/connectors/executors/nexplane_agent/wdac_audit.py`:
```python
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await dispatch_agent_job(
        command="wdac_audit",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=int(parameters.get("duration_seconds", 60)) + 30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await dispatch_agent_job(
        command="wdac_audit",
        parameters={"action": "restore"},
        asset_ids=asset_ids,
        timeout_seconds=60,
    )
```

`backend/app/connectors/executors/nexplane_agent/wdac_enforce.py`: same pattern, `command="wdac_enforce"`, `timeout_seconds=120`.

- [ ] **Step 6: Build agent**

```bash
cd agent && go build ./... && echo "OK"
```
Expected: `OK`.

- [ ] **Step 7: Commit**

```bash
git add agent/commands/winharden/wdac_windows.go \
        agent/commands/winharden/winharden.go \
        agent/commands/winharden/winharden_other.go \
        agent/executor/executor.go \
        backend/app/connectors/executors/nexplane_agent/wdac_audit.py \
        backend/app/connectors/executors/nexplane_agent/wdac_enforce.py
git commit -m "feat: add WDAC audit/enforce Go agent commands + Python executors"
```

---

### Task 5: ASR (Attack Surface Reduction) audit and block Go commands

**Files:**
- Create: `agent/commands/winharden/asr_windows.go`
- Modify: `agent/commands/winharden/winharden.go`, `winharden_other.go`
- Modify: `agent/executor/executor.go`
- Create: `backend/app/connectors/executors/nexplane_agent/asr_audit.py`
- Create: `backend/app/connectors/executors/nexplane_agent/asr_enforce.py`

- [ ] **Step 1: Write asr_windows.go**

```go
//go:build windows

package winharden

import (
	"fmt"
	"strings"
	"time"
)

// ASR rule GUIDs (subset of 16 rules; all supported by Windows Defender via Set-MpPreference)
var asrRuleNames = map[string]string{
	"block-office-child-processes":         "D4F940AB-401B-4EFC-AADC-AD5F3C50688A",
	"block-credential-stealing":            "9E6C4E1F-7D60-472F-BA1A-A39EF669E4B2",
	"block-untrusted-executables-email":    "BE9BA2D9-53EA-4CDC-84E5-9B1EEEE46550",
	"block-office-macro-win32-api":         "92E97FA1-2EDF-4476-BDD6-9DD0B4DDDC7B",
	"block-script-obfuscated-js-vbs":       "5BEB7EFE-FD9A-4556-801D-275E5FFC04CC",
	"block-js-vbs-launching-executable":    "D3E037E1-3EB8-44C8-A917-57927947596D",
	"block-process-creation-psexec-wmi":    "D1E49AAC-8F56-4280-B9BA-993A6D77406C",
	"block-untrusted-usb-processes":        "B2B3F03D-6A65-4F7B-A9C7-1C7EF74A9BA4",
}

func asrAuditExecuteOS(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}
	rules := asrRuleIDList(params)

	// Enable rules in audit mode
	for _, ruleID := range rules {
		out, err := runPS(fmt.Sprintf(
			`Add-MpPreference -AttackSurfaceReductionRules_Ids %s -AttackSurfaceReductionRules_Actions AuditMode`,
			ruleID,
		))
		if err != nil {
			return nil, fmt.Errorf("ASR audit mode for %s: %s: %w", ruleID, out, err)
		}
	}

	time.Sleep(time.Duration(duration) * time.Second)

	// Collect audit events (Event ID 1122)
	out, _ := runPS(
		`Get-WinEvent -LogName "Microsoft-Windows-Windows Defender/Operational" ` +
		`-FilterXPath "*[System[EventID=1122]]" -MaxEvents 200 -ErrorAction SilentlyContinue ` +
		`| Select-Object -ExpandProperty Message`,
	)
	events := parseLines(string(out))
	return map[string]any{
		"action":           "asr_audit",
		"duration_seconds": int(duration),
		"rules_audited":    rules,
		"audit_events":     events,
		"event_count":      len(events),
	}, nil
}

func asrEnforceExecuteOS(params map[string]any) (map[string]any, error) {
	rules := asrRuleIDList(params)
	for _, ruleID := range rules {
		out, err := runPS(fmt.Sprintf(
			`Add-MpPreference -AttackSurfaceReductionRules_Ids %s -AttackSurfaceReductionRules_Actions Enabled`,
			ruleID,
		))
		if err != nil {
			return nil, fmt.Errorf("ASR block mode for %s: %s: %w", ruleID, out, err)
		}
	}
	return map[string]any{"action": "asr_enforce", "rules_enforced": rules}, nil
}

func asrRollbackOS(params map[string]any) (map[string]any, error) {
	rules := asrRuleIDList(params)
	for _, ruleID := range rules {
		runPS(fmt.Sprintf(
			`Remove-MpPreference -AttackSurfaceReductionRules_Ids %s`, ruleID,
		))
	}
	return map[string]any{"action": "asr_rollback", "rules_cleared": rules}, nil
}

func asrRuleIDList(params map[string]any) []string {
	var ids []string
	if raw, ok := params["rule_names"].([]any); ok {
		for _, r := range raw {
			if name, ok := r.(string); ok {
				if id, found := asrRuleNames[name]; found {
					ids = append(ids, id)
				}
			}
		}
	}
	if len(ids) == 0 {
		// Default: enable the three most impactful rules
		ids = []string{
			asrRuleNames["block-office-child-processes"],
			asrRuleNames["block-credential-stealing"],
			asrRuleNames["block-untrusted-executables-email"],
		}
	}
	return ids
}
```

- [ ] **Step 2: Add wrappers to winharden.go + stubs to winharden_other.go**

In `winharden.go`:
```go
func ASRauditExecute(params map[string]any) (map[string]any, error) { return asrAuditExecuteOS(params) }
func ASRenforceExecute(params map[string]any) (map[string]any, error) { return asrEnforceExecuteOS(params) }
func ASRrollback(params map[string]any) (map[string]any, error) { return asrRollbackOS(params) }
```

In `winharden_other.go` (`//go:build !windows`):
```go
func asrAuditExecuteOS(params map[string]any) (map[string]any, error) { return map[string]any{"error": "ASR is Windows-only"}, nil }
func asrEnforceExecuteOS(params map[string]any) (map[string]any, error) { return map[string]any{"error": "ASR is Windows-only"}, nil }
func asrRollbackOS(params map[string]any) (map[string]any, error) { return map[string]any{"error": "ASR is Windows-only"}, nil }
```

- [ ] **Step 3: Register in executor.go**

```go
"asr_audit":   winharden.ASRauditExecute,
"asr_enforce": winharden.ASRenforceExecute,
```
Rollback:
```go
"asr_audit":   winharden.ASRrollback,
"asr_enforce": winharden.ASRrollback,
```

- [ ] **Step 4: Create Python executors**

`backend/app/connectors/executors/nexplane_agent/asr_audit.py` and `asr_enforce.py` — same dispatch pattern as Task 4, commands `"asr_audit"` and `"asr_enforce"`.

- [ ] **Step 5: Build and commit**

```bash
cd agent && go build ./... && echo "OK"
git add agent/commands/winharden/asr_windows.go agent/commands/winharden/winharden.go \
        agent/commands/winharden/winharden_other.go agent/executor/executor.go \
        backend/app/connectors/executors/nexplane_agent/asr_audit.py \
        backend/app/connectors/executors/nexplane_agent/asr_enforce.py
git commit -m "feat: add ASR (Attack Surface Reduction) audit/enforce Go agent commands"
```

---

### Task 6: Sysmon deploy and FIM Go commands

**Files:**
- Create: `agent/commands/winharden/sysmon_windows.go`
- Modify: `agent/commands/winharden/winharden.go`, `winharden_other.go`, `agent/executor/executor.go`
- Create: `backend/app/connectors/executors/nexplane_agent/sysmon_deploy.py`
- Create: `backend/app/connectors/executors/nexplane_agent/sysmon_fim.py`

- [ ] **Step 1: Write sysmon_windows.go**

```go
//go:build windows

package winharden

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const sysmonPath = `C:\Program Files\Sysmon\Sysmon64.exe`
const sysmonConfigPath = `C:\Program Files\Sysmon\nexplane-sysmon.xml`

// nexplaneSysmonConfig is a minimal Sysmon config focused on file integrity events.
const nexplaneSysmonConfig = `<Sysmon schemaversion="4.90">
  <EventFiltering>
    <RuleGroup name="FileCreate" groupRelation="or">
      <FileCreate onmatch="include">
        <TargetFilename condition="begin with">C:\Monitored\</TargetFilename>
        <TargetFilename condition="begin with">C:\inetpub\</TargetFilename>
        <TargetFilename condition="begin with">C:\Windows\System32\</TargetFilename>
      </FileCreate>
    </RuleGroup>
    <RuleGroup name="FileDelete" groupRelation="or">
      <FileDelete onmatch="include">
        <TargetFilename condition="begin with">C:\Monitored\</TargetFilename>
      </FileDelete>
    </RuleGroup>
  </EventFiltering>
</Sysmon>`

func sysmonDeployExecuteOS(params map[string]any) (map[string]any, error) {
	installDir := filepath.Dir(sysmonPath)
	if err := os.MkdirAll(installDir, 0755); err != nil {
		return nil, fmt.Errorf("create Sysmon dir: %w", err)
	}

	// Download Sysmon if not present (operator should pre-stage or provide URL)
	sysmonURL, _ := params["sysmon_url"].(string)
	if _, err := os.Stat(sysmonPath); os.IsNotExist(err) {
		if sysmonURL == "" {
			sysmonURL = "https://live.sysinternals.com/Sysmon64.exe"
		}
		out, err := runPS(fmt.Sprintf(
			`Invoke-WebRequest -Uri "%s" -OutFile "%s" -UseBasicParsing`, sysmonURL, sysmonPath,
		))
		if err != nil {
			return nil, fmt.Errorf("download Sysmon: %s: %w", out, err)
		}
	}

	// Write config
	config := nexplaneSysmonConfig
	if customConfig, ok := params["config_xml"].(string); ok && customConfig != "" {
		config = customConfig
	}
	if err := os.WriteFile(sysmonConfigPath, []byte(config), 0644); err != nil {
		return nil, fmt.Errorf("write Sysmon config: %w", err)
	}

	// Install or update config
	out, err := runPS(fmt.Sprintf(
		`& "%s" -accepteula -i "%s" 2>&1; if ($LASTEXITCODE -ne 0) { & "%s" -c "%s" }`,
		sysmonPath, sysmonConfigPath, sysmonPath, sysmonConfigPath,
	))
	if err != nil {
		return nil, fmt.Errorf("Sysmon install/update: %s: %w", out, err)
	}

	// Verify service running
	svcOut, _ := runPS(`(Get-Service sysmon64 -ErrorAction SilentlyContinue).Status`)
	return map[string]any{
		"action":      "sysmon_deploy",
		"sysmon_path": sysmonPath,
		"config_path": sysmonConfigPath,
		"service_status": strings.TrimSpace(string(svcOut)),
	}, nil
}

func sysmonFIMexecuteOS(params map[string]any) (map[string]any, error) {
	sinceMinutes, _ := params["since_minutes"].(float64)
	if sinceMinutes <= 0 {
		sinceMinutes = 60
	}
	path, _ := params["monitored_path"].(string)

	var filter string
	if path != "" {
		filter = fmt.Sprintf(
			`-FilterXPath "*[System[(EventID=11 or EventID=23)] and EventData[Data[@Name='TargetFilename'][contains(.,'%s')]]]"`,
			path,
		)
	} else {
		filter = `-FilterXPath "*[System[EventID=11 or EventID=23]]"`
	}

	out, _ := runPS(fmt.Sprintf(
		`Get-WinEvent -LogName "Microsoft-Windows-Sysmon/Operational" %s -MaxEvents 500 -ErrorAction SilentlyContinue `+
		`| Select-Object TimeCreated,Id,@{n='File';e={$_.Properties[0].Value}} `+
		`| ConvertTo-Json -Compress`,
		filter,
	))

	events := strings.TrimSpace(string(out))
	return map[string]any{
		"action":          "sysmon_fim",
		"monitored_path":  path,
		"since_minutes":   int(sinceMinutes),
		"events_json":     events,
		"event_count":     strings.Count(events, `"TimeCreated"`),
	}, nil
}

func sysmonRollbackOS(_ map[string]any) (map[string]any, error) {
	out, _ := runPS(fmt.Sprintf(`& "%s" -u 2>&1`, sysmonPath))
	return map[string]any{"action": "sysmon_rollback", "output": strings.TrimSpace(string(out))}, nil
}
```

- [ ] **Step 2: Add wrappers, stubs, executor registrations, Python executors** — same pattern as Tasks 4–5. Commands: `"sysmon_deploy"`, `"sysmon_fim"`.

- [ ] **Step 3: Build and commit**

```bash
cd agent && go build ./... && echo "OK"
git add agent/commands/winharden/ agent/executor/executor.go \
        backend/app/connectors/executors/nexplane_agent/sysmon_deploy.py \
        backend/app/connectors/executors/nexplane_agent/sysmon_fim.py
git commit -m "feat: add Sysmon deploy + FIM query Go agent commands"
```

---

### Task 7: Linux seccomp_learn Go command

**Files:**
- Create: `agent/commands/ossecurity/seccomp_learn_linux.go`
- Modify: `agent/commands/ossecurity/ossecurity.go` (add exported wrapper)
- Modify: `agent/commands/ossecurity/ossecurity_other.go` (non-Linux stub)
- Modify: `agent/executor/executor.go`
- Create: `backend/app/connectors/executors/nexplane_agent/seccomp_learn.py`

- [ ] **Step 1: Write seccomp_learn_linux.go**

```go
//go:build linux

package ossecurity

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const scmpLogProfileTemplate = `{
  "defaultAction": "SCMP_ACT_LOG",
  "syscalls": []
}`

func seccompLearnExecuteOS(params map[string]any) (map[string]interface{}, error) {
	serviceName, _ := params["service_name"].(string)
	if serviceName == "" {
		return nil, fmt.Errorf("service_name is required")
	}
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}

	// Write SCMP_ACT_LOG profile to a temp file
	profileDir := "/etc/nexplane/seccomp"
	if err := os.MkdirAll(profileDir, 0755); err != nil {
		return nil, fmt.Errorf("mkdir seccomp: %w", err)
	}
	learnProfilePath := filepath.Join(profileDir, serviceName+"-learn.json")
	if err := os.WriteFile(learnProfilePath, []byte(scmpLogProfileTemplate), 0644); err != nil {
		return nil, fmt.Errorf("write learn profile: %w", err)
	}

	// Apply the log profile via systemd drop-in
	dropInDir := fmt.Sprintf("/etc/systemd/system/%s.service.d", serviceName)
	if err := os.MkdirAll(dropInDir, 0755); err != nil {
		return nil, fmt.Errorf("mkdir drop-in: %w", err)
	}
	dropInPath := filepath.Join(dropInDir, "nexplane-seccomp-learn.conf")
	dropInContent := fmt.Sprintf("[Service]\nSeccompFilter=%s\n", learnProfilePath)
	if err := os.WriteFile(dropInPath, []byte(dropInContent), 0644); err != nil {
		return nil, fmt.Errorf("write drop-in: %w", err)
	}

	exec.Command("systemctl", "daemon-reload").Run()
	exec.Command("systemctl", "restart", serviceName).Run()

	// Wait for observation window
	time.Sleep(time.Duration(duration) * time.Second)

	// Parse audit log for SECCOMP entries for this service
	pid := getServicePID(serviceName)
	syscalls := collectSyscallsFromAuditLog(pid, serviceName)

	// Cleanup learn profile
	os.Remove(dropInPath)
	exec.Command("systemctl", "daemon-reload").Run()
	exec.Command("systemctl", "restart", serviceName).Run()

	return map[string]any{
		"action":           "seccomp_learn",
		"service_name":     serviceName,
		"duration_seconds": int(duration),
		"observed_syscalls": syscalls,
		"syscall_count":    len(syscalls),
	}, nil
}

func getServicePID(service string) string {
	out, err := exec.Command("systemctl", "show", "--property=MainPID", service).Output()
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(out), "\n") {
		if strings.HasPrefix(line, "MainPID=") {
			return strings.TrimPrefix(line, "MainPID=")
		}
	}
	return ""
}

func collectSyscallsFromAuditLog(pid, service string) []string {
	seen := map[string]bool{}
	var syscalls []string

	f, err := os.Open("/var/log/audit/audit.log")
	if err != nil {
		// Fallback: read kern log
		return syscalls
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.Contains(line, "type=SECCOMP") {
			continue
		}
		if pid != "" && !strings.Contains(line, "pid="+pid) {
			continue
		}
		if service != "" && !strings.Contains(line, "exe=") {
			continue
		}
		// Extract syscall number: syscall=N
		for _, field := range strings.Fields(line) {
			if strings.HasPrefix(field, "syscall=") {
				num := strings.TrimPrefix(field, "syscall=")
				if _, err := strconv.Atoi(num); err == nil && !seen[num] {
					seen[num] = true
					// Resolve syscall number to name via auditd
					name := resolveSyscallNumber(num)
					syscalls = append(syscalls, name)
				}
			}
		}
	}
	return syscalls
}

func resolveSyscallNumber(num string) string {
	out, err := exec.Command("ausyscall", num).Output()
	if err != nil {
		return "syscall_" + num
	}
	return strings.TrimSpace(string(out))
}
```

- [ ] **Step 2: Add exported wrapper in ossecurity.go**

```go
func SeccompLearnExecute(params map[string]any) (map[string]any, error) {
	return seccompLearnExecuteOS(params)
}
```

In `ossecurity_other.go` (non-Linux):
```go
func seccompLearnExecuteOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "seccomp_learn is Linux-only"}, nil
}
```

- [ ] **Step 3: Register in executor.go**

```go
"seccomp_learn": ossecurity.SeccompLearnExecute,
```

- [ ] **Step 4: Create Python executor**

`backend/app/connectors/executors/nexplane_agent/seccomp_learn.py`:
```python
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    duration = int(parameters.get("duration_seconds", 60))
    result = await dispatch_agent_job(
        command="seccomp_learn",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=duration + 30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result
```

- [ ] **Step 5: Build and commit**

```bash
cd agent && go build ./... && echo "OK"
git add agent/commands/ossecurity/ agent/executor/executor.go \
        backend/app/connectors/executors/nexplane_agent/seccomp_learn.py
git commit -m "feat: add seccomp_learn Go agent command (SCMP_ACT_LOG + syscall collection)"
```

---

### Task 8: Linux iptables_log_baseline Go command

**Files:**
- Create: `agent/commands/ossecurity/firewall_learn_linux.go`
- Modify: `agent/commands/ossecurity/ossecurity.go`, `ossecurity_other.go`
- Modify: `agent/executor/executor.go`
- Create: `backend/app/connectors/executors/nexplane_agent/firewall_learn.py`

- [ ] **Step 1: Write firewall_learn_linux.go**

```go
//go:build linux

package ossecurity

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const firewallLogTag = "NEXPLANE-BASELINE"

func firewallLearnExecuteOS(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}

	// Add logging rules for outbound traffic (mirrors any intended DROP rules)
	// We log everything — the operator can then select what to block
	_, err := exec.Command("iptables", "-I", "OUTPUT", "1", "-j", "LOG",
		"--log-prefix", firewallLogTag+" ", "--log-level", "4").CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("add OUTPUT log rule: %w", err)
	}
	_, err = exec.Command("iptables", "-I", "INPUT", "1", "-j", "LOG",
		"--log-prefix", firewallLogTag+" ", "--log-level", "4").CombinedOutput()
	if err != nil {
		exec.Command("iptables", "-D", "OUTPUT", "1").Run()
		return nil, fmt.Errorf("add INPUT log rule: %w", err)
	}

	time.Sleep(time.Duration(duration) * time.Second)

	// Read kern.log / syslog for the tagged entries
	connections := parseFirewallLog()

	// Remove the log rules
	exec.Command("iptables", "-D", "OUTPUT", "1").Run()
	exec.Command("iptables", "-D", "INPUT", "1").Run()

	return map[string]any{
		"action":           "firewall_log_baseline",
		"duration_seconds": int(duration),
		"observed_connections": connections,
		"connection_count": len(connections),
	}, nil
}

func parseFirewallLog() []map[string]string {
	var connections []map[string]string
	seen := map[string]bool{}

	for _, logFile := range []string{"/var/log/kern.log", "/var/log/syslog", "/var/log/messages"} {
		f, err := os.Open(logFile)
		if err != nil {
			continue
		}
		scanner := bufio.NewScanner(f)
		for scanner.Scan() {
			line := scanner.Text()
			if !strings.Contains(line, firewallLogTag) {
				continue
			}
			conn := extractConnInfo(line)
			key := conn["src"] + "->" + conn["dst"] + ":" + conn["dpt"]
			if !seen[key] {
				seen[key] = true
				connections = append(connections, conn)
			}
		}
		f.Close()
	}
	return connections
}

func extractConnInfo(line string) map[string]string {
	info := map[string]string{"src": "", "dst": "", "spt": "", "dpt": "", "proto": ""}
	for _, field := range strings.Fields(line) {
		parts := strings.SplitN(field, "=", 2)
		if len(parts) != 2 {
			continue
		}
		switch parts[0] {
		case "SRC":
			info["src"] = parts[1]
		case "DST":
			info["dst"] = parts[1]
		case "SPT":
			info["spt"] = parts[1]
		case "DPT":
			info["dpt"] = parts[1]
		case "PROTO":
			info["proto"] = parts[1]
		}
	}
	return info
}
```

- [ ] **Step 2: Wire exports, stubs, registration, Python executor** — same as previous tasks. Command: `"firewall_log_baseline"`.

- [ ] **Step 3: Build and commit**

```bash
cd agent && go build ./... && echo "OK"
git add agent/commands/ossecurity/ agent/executor/executor.go \
        backend/app/connectors/executors/nexplane_agent/firewall_learn.py
git commit -m "feat: add firewall_log_baseline Go agent command (iptables LOG + connection collection)"
```

---

## Phase 0D: OSSEC_WIRE Smoke Test

### Task 9: Add OSSEC_WIRE smoke phase to test_aws_live.py

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add install script for insecure SSH config**

In `test_aws_live.py`, near the existing `_INSTALL_AUTO_APPS_LINUX` constant, add:

```python
_INSTALL_INSECURE_LINUX = r"""
set -e
# Enable password auth (known CIS violation for compliance test)
sed -i 's/^#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
systemctl restart sshd
echo "Insecure SSH configured"
"""

_TEARDOWN_INSECURE_LINUX = r"""
set -e
rm -f /etc/ssh/sshd_config.d/99-nexplane-hardening.conf
systemctl restart sshd
echo "SSH hardening removed"
"""
```

- [ ] **Step 2: Write the run_phase_ossec_wire function**

```python
def run_phase_ossec_wire(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase OSSEC_WIRE: verify all Linux OS hardening executors dispatch to the agent."""
    print("\n[Phase OSSEC_WIRE] Linux OS hardening executor wire-up verification")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]
    agent_asset = phase_a_result.get("agent_asset") or {}
    agent_asset_id = agent_asset.get("id")
    if not agent_asset_id:
        fail("[Phase OSSEC_WIRE] No agent asset ID from Phase A")

    try:
        _ssm(client, instance_asset["id"], instance_id, "OSSEC_WIRE",
             "setup_insecure_ssh", _INSTALL_INSECURE_LINUX)
        log("Insecure SSH configured for testing")

        # --- apply_sysctl_hardening ---
        cr_id = client.create_cr(
            "[OSSEC_WIRE] sysctl hardening",
            "apply_sysctl_hardening", agent_asset_id,
            {"profile": "cis_level1"},
        )
        client.post(f"/change-requests/{cr_id}/plan")
        client.post(f"/change-requests/{cr_id}/submit-for-approval")
        client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved"})
        client.post(f"/change-requests/{cr_id}/execute")
        _wait_cr_complete(client, cr_id, "[OSSEC_WIRE] sysctl")
        out = _ssm_output(client, instance_asset["id"], instance_id,
                          "OSSEC_WIRE", "verify_sysctl",
                          "sysctl net.ipv4.tcp_syncookies")
        if "1" not in out:
            fail(f"[OSSEC_WIRE] sysctl: expected net.ipv4.tcp_syncookies=1, got: {out}")
        log("sysctl hardening: REAL ✓")

        # --- harden_ssh ---
        cr_id = client.create_cr(
            "[OSSEC_WIRE] SSH hardening",
            "harden_ssh", agent_asset_id,
            {"profile": "cis_level1"},
        )
        client.post(f"/change-requests/{cr_id}/plan")
        client.post(f"/change-requests/{cr_id}/submit-for-approval")
        client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved"})
        client.post(f"/change-requests/{cr_id}/execute")
        _wait_cr_complete(client, cr_id, "[OSSEC_WIRE] harden_ssh")
        out = _ssm_output(client, instance_asset["id"], instance_id,
                          "OSSEC_WIRE", "verify_ssh",
                          "grep -i PasswordAuthentication /etc/ssh/sshd_config.d/99-nexplane-hardening.conf")
        if "no" not in out.lower():
            fail(f"[OSSEC_WIRE] harden_ssh: PasswordAuthentication not set to no, got: {out}")
        log("harden_ssh: REAL ✓")

        # --- deploy_auditd_rules ---
        cr_id = client.create_cr(
            "[OSSEC_WIRE] auditd CIS L1",
            "deploy_auditd_rules", agent_asset_id,
            {"profile": "cis_level1"},
        )
        client.post(f"/change-requests/{cr_id}/plan")
        client.post(f"/change-requests/{cr_id}/submit-for-approval")
        client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved"})
        client.post(f"/change-requests/{cr_id}/execute")
        _wait_cr_complete(client, cr_id, "[OSSEC_WIRE] auditd")
        out = _ssm_output(client, instance_asset["id"], instance_id,
                          "OSSEC_WIRE", "verify_auditd",
                          "auditctl -l 2>/dev/null | wc -l")
        rule_count = int(out.strip()) if out.strip().isdigit() else 0
        if rule_count == 0:
            fail(f"[OSSEC_WIRE] deploy_auditd_rules: no rules loaded (count=0)")
        log(f"deploy_auditd_rules: REAL ✓ ({rule_count} rules)")

        # --- audit_os_security_posture (read-only) ---
        cr_id = client.create_cr(
            "[OSSEC_WIRE] OS security posture",
            "audit_os_security_posture", agent_asset_id, {},
        )
        client.post(f"/change-requests/{cr_id}/plan")
        client.post(f"/change-requests/{cr_id}/submit-for-approval")
        client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved"})
        client.post(f"/change-requests/{cr_id}/execute")
        _wait_cr_complete(client, cr_id, "[OSSEC_WIRE] posture")
        cr = client.get(f"/change-requests/{cr_id}")
        exec_runs = cr.get("execution_runs") or []
        run_result = (exec_runs[0].get("result") or {}) if exec_runs else {}
        steps = (run_result.get("execution") or {}).get("steps") or []
        posture = steps[-1].get("result", {}) if steps else {}
        if posture.get("auditd_enabled") is None:
            fail("[OSSEC_WIRE] audit_os_security_posture: returned hardcoded/null auditd_enabled (still a stub)")
        log(f"audit_os_security_posture: REAL ✓ (auditd_enabled={posture.get('auditd_enabled')})")

        log("Phase OSSEC_WIRE complete — all tested hardening executors dispatch to agent")

    except Exception as e:
        print(f"\n❌ Phase OSSEC_WIRE failed: {e}")
        raise
    finally:
        try:
            _ssm(client, instance_asset["id"], instance_id, "OSSEC_WIRE",
                 "teardown_insecure_ssh", _TEARDOWN_INSECURE_LINUX)
        except Exception:
            pass


def _wait_cr_complete(client: NexplaneClient, cr_id: str, label: str, timeout: int = TIMEOUT_SECONDS) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.get(f"/change-requests/{cr_id}")
        if cr.get("status") in ("completed", "failed", "rolled_back"):
            if cr.get("status") != "completed":
                fail(f"{label}: CR ended with status '{cr.get('status')}'")
            return
        time.sleep(10)
    fail(f"{label}: CR timed out after {timeout}s")


def _ssm_output(client, instance_asset_id, instance_id, phase, label, command) -> str:
    """Run an SSM command and return stdout."""
    cr_id = client.create_cr(
        f"[{phase}] SSM {label}", "run_ssm_command",
        instance_asset_id, {"command": command},
    )
    client.post(f"/change-requests/{cr_id}/plan")
    client.post(f"/change-requests/{cr_id}/submit-for-approval")
    client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved"})
    client.post(f"/change-requests/{cr_id}/execute")
    deadline = time.time() + 60
    while time.time() < deadline:
        cr = client.get(f"/change-requests/{cr_id}")
        if cr.get("status") in ("completed", "failed"):
            exec_runs = cr.get("execution_runs") or []
            result = (exec_runs[0].get("result") or {}) if exec_runs else {}
            return str(result.get("stdout", "") or result.get("output", ""))
        time.sleep(5)
    return ""
```

- [ ] **Step 3: Wire into main() dispatch**

In `main()`, after the AUTO block:
```python
if "OSSEC_WIRE" in phases:
    if phase_a_result is None:
        fail("Phase OSSEC_WIRE requires Phase A to have run first")
    run_phase_ossec_wire(client, phase_a_result)
```

- [ ] **Step 4: Test the phase runs end-to-end via EC2 runner**

```bash
docker compose exec \
  -e AWS_ACCESS_KEY_ID=AKIAY57IGHLWB6RI3YVM \
  -e "AWS_SECRET_ACCESS_KEY=SLX7jYNMHu3Sd7C/bGflGn6KLGZD/64oHoHr/Hyk" \
  -e AWS_DEFAULT_REGION=us-east-1 \
  backend python tests/smoke/run_on_ec2.py \
    --email admin@acme.example --password admin123 \
    --phases A,OSSEC_WIRE \
    --tailscale-auth-key tskey-auth-kTYui1NBwG11CNTRL-3PypxPACT3JRppfHm8JQ4J6yqEirTU88H
```
Expected: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "test: add OSSEC_WIRE smoke phase — verifies Linux hardening executors dispatch to agent"
```

---

## Phase 1: Foundation

### Task 10: Notification system — backend events + delivery

**Files:**
- Create: `backend/app/models/notification.py`
- Create: `backend/app/services/notification_service.py`
- Create: `backend/app/routers/notifications.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/main.py` (include router)
- Create: `backend/alembic/versions/XXXX_add_notifications_table.py`

- [ ] **Step 1: Write the model test**

```python
# backend/app/tests/test_notification_service.py
import pytest
from unittest.mock import AsyncMock, patch
from app.services.notification_service import NotificationService, NotificationEvent


@pytest.mark.asyncio
async def test_emit_cr_awaiting_approval_creates_notification(db_session):
    svc = NotificationService(db_session)
    await svc.emit(NotificationEvent(
        event_type="cr.awaiting_approval",
        organization_id="org-uuid-1",
        actor_id="user-uuid-1",
        resource_id="cr-uuid-1",
        resource_type="change_request",
        message="CR 'Patch nginx' is awaiting your approval",
        recipients=["approver-uuid-1"],
    ))
    from sqlalchemy import select
    from app.models.notification import Notification
    result = await db_session.execute(
        select(Notification).where(Notification.resource_id == "cr-uuid-1")
    )
    notifications = result.scalars().all()
    assert len(notifications) == 1
    assert notifications[0].event_type == "cr.awaiting_approval"
    assert notifications[0].read is False
```

- [ ] **Step 2: Run test — expect FAIL (model doesn't exist)**

```bash
docker compose exec backend pytest app/tests/test_notification_service.py -v 2>&1 | tail -10
```
Expected: `ImportError: cannot import name 'Notification'`

- [ ] **Step 3: Create Notification model**

`backend/app/models/notification.py`:
```python
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Create NotificationService**

`backend/app/services/notification_service.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.notification import Notification


@dataclass
class NotificationEvent:
    event_type: str
    organization_id: str
    message: str
    recipients: List[str]          # list of user UUIDs
    actor_id: str | None = None
    resource_id: str | None = None
    resource_type: str | None = None


class NotificationService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def emit(self, event: NotificationEvent) -> None:
        """Persist in-app notifications for each recipient."""
        for recipient_id in event.recipients:
            n = Notification(
                organization_id=uuid.UUID(event.organization_id),
                recipient_user_id=uuid.UUID(recipient_id),
                event_type=event.event_type,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                message=event.message,
            )
            self.db.add(n)
        await self.db.commit()
```

- [ ] **Step 5: Generate and run alembic migration**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_notifications_table"
docker compose exec backend alembic upgrade head
```

- [ ] **Step 6: Run test — expect PASS**

```bash
docker compose exec backend pytest app/tests/test_notification_service.py -v 2>&1 | tail -10
```
Expected: 1 PASSED.

- [ ] **Step 7: Create notifications router**

`backend/app/routers/notifications.py`:
```python
from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.auth import current_user
from app.models.user import User
from app.models.notification import Notification

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class NotificationRead(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    event_type: str
    resource_type: str | None
    resource_id: str | None
    message: str
    read: bool
    created_at: datetime


@router.get("", response_model=list[NotificationRead])
async def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    q = select(Notification).where(
        Notification.recipient_user_id == user.id,
        Notification.organization_id == user.organization_id,
    ).order_by(Notification.created_at.desc()).limit(limit)
    if unread_only:
        q = q.where(Notification.read == False)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/{notification_id}/read", status_code=200)
async def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.recipient_user_id == user.id)
        .values(read=True)
    )
    await db.commit()
    return {"marked_read": True}


@router.post("/read-all", status_code=200)
async def mark_all_read(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(Notification)
        .where(Notification.recipient_user_id == user.id, Notification.read == False)
        .values(read=True)
    )
    await db.commit()
    return {"marked_read": True}
```

- [ ] **Step 8: Wire the router and integrate emit into CR approval workflow**

In `backend/app/main.py`, add:
```python
from app.routers.notifications import router as notifications_router
app.include_router(notifications_router)
```

In `backend/app/routers/change_requests.py`, in the `approve_change_request` endpoint, after setting status to `awaiting_approval` / `approved`:
```python
# After CR transitions to awaiting_approval, notify approvers
from app.services.notification_service import NotificationService, NotificationEvent
from app.models.user import User, UserRole
from sqlalchemy import select as _select

approvers_result = await db.execute(
    _select(User).where(
        User.organization_id == cr.organization_id,
        User.role.in_([UserRole.approver, UserRole.admin]),
    )
)
approvers = approvers_result.scalars().all()
notif_svc = NotificationService(db)
await notif_svc.emit(NotificationEvent(
    event_type="cr.awaiting_approval",
    organization_id=str(cr.organization_id),
    actor_id=str(user.id),
    resource_id=str(cr.id),
    resource_type="change_request",
    message=f"Change request '{cr.title}' is awaiting your approval",
    recipients=[str(u.id) for u in approvers],
))
```

- [ ] **Step 9: Add notification bell to frontend sidebar**

`frontend/src/components/Sidebar.tsx` — add unread count badge:
```tsx
// Near top of file
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

// Inside Sidebar component
const { data: notifications } = useQuery({
  queryKey: ["notifications", "unread"],
  queryFn: () => api.get("/notifications?unread_only=true&limit=50").then(r => r.data),
  refetchInterval: 30_000,
});
const unreadCount: number = notifications?.length ?? 0;
```

Add bell icon with badge to the sidebar nav:
```tsx
<NavLink to="/notifications" className={navClass}>
  <BellIcon className="w-5 h-5" />
  {unreadCount > 0 && (
    <span className="absolute -top-1 -right-1 bg-red-500 text-white text-xs rounded-full w-4 h-4 flex items-center justify-center">
      {unreadCount > 9 ? "9+" : unreadCount}
    </span>
  )}
</NavLink>
```

Create `frontend/src/pages/Notifications.tsx`:
```tsx
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

interface Notification {
  id: string;
  event_type: string;
  resource_type: string | null;
  resource_id: string | null;
  message: string;
  read: boolean;
  created_at: string;
}

export function Notifications() {
  const qc = useQueryClient();
  const { data: notifications, isLoading } = useQuery<Notification[]>({
    queryKey: ["notifications"],
    queryFn: () => api.get("/notifications").then(r => r.data),
    refetchInterval: 15_000,
  });

  const markAllRead = useMutation({
    mutationFn: () => api.post("/notifications/read-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <PageHeader
        title="Notifications"
        actions={
          <button
            onClick={() => markAllRead.mutate()}
            className="text-sm text-brand-600 hover:underline"
          >
            Mark all read
          </button>
        }
      />
      <div className="space-y-2 mt-4">
        {notifications?.length === 0 && (
          <p className="text-slate-500 text-sm italic">No notifications.</p>
        )}
        {notifications?.map(n => (
          <div
            key={n.id}
            className={`p-3 rounded border text-sm ${n.read ? "bg-white border-slate-200 text-slate-500" : "bg-blue-50 border-blue-200 text-slate-800 font-medium"}`}
          >
            <p>{n.message}</p>
            <p className="text-xs text-slate-400 mt-1">{new Date(n.created_at).toLocaleString()}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
```

Add route in `frontend/src/routes/index.tsx`:
```tsx
{ path: "/notifications", element: <Notifications /> }
```

- [ ] **Step 10: Run backend tests**

```bash
docker compose exec backend pytest app/tests/test_notification_service.py -v 2>&1 | tail -5
```
Expected: PASSED.

- [ ] **Step 11: Restart frontend and verify badge appears**

```bash
docker compose stop frontend && docker compose up frontend -d
```
Open `http://localhost:5173` → create and approve a CR → bell icon shows unread badge.

- [ ] **Step 12: Commit**

```bash
git add backend/app/models/notification.py backend/app/services/notification_service.py \
        backend/app/routers/notifications.py backend/app/tests/test_notification_service.py \
        backend/app/main.py backend/app/routers/change_requests.py \
        backend/alembic/versions/ \
        frontend/src/components/Sidebar.tsx frontend/src/pages/Notifications.tsx \
        frontend/src/routes/index.tsx
git commit -m "feat: notification system — in-app notifications for CR approval events with unread badge"
```

---

### Task 11: CVE context in approval queue

**Files:**
- Modify: `backend/app/models/change_request.py` (add `finding_ids` field)
- Create: `backend/alembic/versions/XXXX_add_cr_finding_ids.py`
- Modify: `backend/app/schemas/change_request.py`
- Modify: `frontend/src/pages/ApprovalsQueue.tsx`

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_cr_finding_link.py
import pytest
from app.schemas.change_request import ChangeRequestCreate

def test_cr_create_accepts_finding_ids():
    cr = ChangeRequestCreate(
        title="Patch CVE-2024-1234",
        change_type="agent_linux_patch",
        target_asset_ids=["asset-uuid-1"],
        desired_outcome={},
        finding_ids=["finding-uuid-1", "finding-uuid-2"],
    )
    assert len(cr.finding_ids) == 2
```

- [ ] **Step 2: Run — expect FAIL**

```bash
docker compose exec backend pytest app/tests/test_cr_finding_link.py -v 2>&1 | tail -5
```

- [ ] **Step 3: Add finding_ids to ChangeRequest model and schema**

In `backend/app/models/change_request.py`, add:
```python
from sqlalchemy import ARRAY, String
finding_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False, server_default="{}")
```

In `backend/app/schemas/change_request.py`, add to `ChangeRequestCreate` and `ChangeRequestRead`:
```python
finding_ids: list[str] = []
```

- [ ] **Step 4: Generate migration and run**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_cr_finding_ids"
docker compose exec backend alembic upgrade head
```

- [ ] **Step 5: Run test — expect PASS**

```bash
docker compose exec backend pytest app/tests/test_cr_finding_link.py -v 2>&1 | tail -5
```

- [ ] **Step 6: Update ApprovalsQueue frontend to show CVE context**

In `frontend/src/pages/ApprovalsQueue.tsx`, add CVE context display to the CR card when `finding_ids` is non-empty:
```tsx
{cr.finding_ids?.length > 0 && (
  <div className="mt-1 flex flex-wrap gap-1">
    {cr.finding_ids.map((fid: string) => (
      <span key={fid} className="text-xs bg-red-100 text-red-700 px-1.5 py-0.5 rounded">
        Finding {fid.slice(0, 8)}…
      </span>
    ))}
  </div>
)}
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/change_request.py backend/app/schemas/change_request.py \
        backend/app/tests/test_cr_finding_link.py backend/alembic/versions/ \
        frontend/src/pages/ApprovalsQueue.tsx
git commit -m "feat: add finding_ids to CR model; show CVE context in approval queue"
```

---

### Task 12: Maintenance window awareness in CR creation

**Files:**
- Modify: `backend/app/models/maintenance_window.py` (add `enforcement` field)
- Modify: `backend/app/routers/assets.py` (add `/assets/:id/maintenance-windows` endpoint)
- Modify: `backend/app/routers/change_requests.py` (block execution for hard-enforcement windows)
- Modify: `frontend/src/pages/CreateChangeRequest.tsx` (show window info on asset select)
- Create: `backend/alembic/versions/XXXX_add_mw_enforcement.py`

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_maintenance_window_enforcement.py
@pytest.mark.asyncio
async def test_hard_enforcement_blocks_execution_outside_window(client, admin_token, test_cr_id, test_mw_id):
    """CR execution is rejected when a hard-enforcement maintenance window is active."""
    # Set window to hard enforcement covering current time
    await client.patch(f"/maintenance-windows/{test_mw_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"enforcement": "hard", "schedule": "* * * * *", "duration_minutes": 60})

    resp = await client.post(f"/change-requests/{test_cr_id}/execute",
        headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 423  # Locked
    assert "maintenance_window" in resp.json()["detail"]
```

- [ ] **Step 2: Add `enforcement` field to MaintenanceWindow**

In `backend/app/models/maintenance_window.py`:
```python
enforcement: Mapped[str] = mapped_column(String(16), default="advisory", nullable=False)
# "advisory" = warn only; "hard" = block execution
```

- [ ] **Step 3: Block execution in the execute endpoint**

In `backend/app/routers/change_requests.py` in `execute_change_request`, before starting the workflow:
```python
from app.models.maintenance_window import MaintenanceWindow
from app.services.maintenance_window_service import is_in_maintenance_window

# Check hard-enforcement windows for target assets
if not cr.priority == "emergency":
    for asset_id in cr.target_asset_ids:
        asset = await db.get(Asset, asset_id)
        if asset:
            blocking_window = await is_in_maintenance_window(
                db, asset.tags or [], enforcement="hard"
            )
            if blocking_window:
                raise HTTPException(
                    status_code=423,
                    detail=f"maintenance_window: execution blocked by hard-enforcement window '{blocking_window.name}'"
                )
```

Create `backend/app/services/maintenance_window_service.py`:
```python
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from croniter import croniter
from app.models.maintenance_window import MaintenanceWindow


async def is_in_maintenance_window(
    db: AsyncSession,
    asset_tags: list[str],
    enforcement: str = "hard",
) -> Optional[MaintenanceWindow]:
    """Return the first active hard-enforcement window that applies to these tags, or None."""
    result = await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.enabled == True,
            MaintenanceWindow.enforcement == enforcement,
        )
    )
    windows = result.scalars().all()
    now = datetime.now(timezone.utc)
    for w in windows:
        # Check if any window tag matches asset tags
        window_tags = set(w.applies_to_tags or [])
        if window_tags and not window_tags.intersection(set(asset_tags)):
            continue
        # Check if currently inside the window
        cron = croniter(w.schedule, now)
        prev_start = cron.get_prev(datetime)
        if (now - prev_start).total_seconds() <= w.duration_minutes * 60:
            return w
    return None
```

- [ ] **Step 4: Generate migration and commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_maintenance_window_enforcement"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_maintenance_window_enforcement.py -v 2>&1 | tail -10
git add backend/ frontend/
git commit -m "feat: maintenance window hard enforcement blocks CR execution outside window"
```

---

### Task 13: Automatic pre-change snapshot

**Files:**
- Modify: `backend/app/models/change_request.py` (add `snapshot_before` field)
- Modify: `backend/app/schemas/change_request.py`
- Modify: `backend/app/workflows/execute_change_workflow.py` (insert snapshot step)
- Create: `backend/alembic/versions/XXXX_add_cr_snapshot_before.py`

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_snapshot_before.py
@pytest.mark.asyncio
async def test_snapshot_cr_fires_before_execution(mocker, test_cr_with_snapshot_before):
    """When snapshot_before=True, a create_ebs_snapshot CR executes first."""
    mock_snapshot = mocker.AsyncMock(return_value={"snapshot_id": "snap-abc123"})
    mocker.patch("app.connectors.executors.aws.create_ebs_snapshot.execute", mock_snapshot)
    # ... run workflow ...
    mock_snapshot.assert_called_once()
```

- [ ] **Step 2: Add `snapshot_before` to model and schema**

```python
# model
snapshot_before: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
# schema
snapshot_before: bool = False
```

- [ ] **Step 3: Inject snapshot step in workflow**

In `backend/app/workflows/execute_change_workflow.py`, before calling `activity_execute_change`:
```python
if data.get("snapshot_before") and data.get("change_type") not in ("create_ebs_snapshot", "agent_appdiscovery"):
    from app.connectors.executors.aws.create_ebs_snapshot import execute as snap_execute
    from app.models.asset import Asset
    for asset_id in data["target_asset_ids"]:
        async with AsyncSessionLocal() as snap_db:
            asset = await snap_db.get(Asset, uuid.UUID(asset_id))
            if asset and asset.asset_metadata.get("instance_id"):
                snapshot_result = await snap_execute(
                    {"description": f"pre-change-{cr_id[:8]}", "wait_for_completion": True},
                    [asset_id], None,
                )
                await write_audit_event(
                    organization_id=org_id,
                    event_type="pre_change_snapshot.created",
                    event_payload={"snapshot_id": snapshot_result.get("snapshot_id"), "asset_id": asset_id},
                    actor_id=actor_id,
                    change_request_id=cr_id,
                )
```

- [ ] **Step 4: Run test, generate migration, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_cr_snapshot_before"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_snapshot_before.py -v 2>&1 | tail -5
git add backend/ && git commit -m "feat: snapshot_before option chains EBS snapshot before destructive CR execution"
```

---

### Task 14: Finding auto-closure on CR completion

**Files:**
- Modify: `backend/app/models/finding.py` (add `state` field: open/remediated/mitigated/reopened)
- Modify: `backend/app/workflows/execute_change_workflow.py` (close linked findings on completion)
- Create: `backend/alembic/versions/XXXX_add_finding_state.py`

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_finding_auto_closure.py
@pytest.mark.asyncio
async def test_finding_auto_closes_when_cr_completes(db_session, test_finding, test_cr):
    test_cr.finding_ids = [str(test_finding.id)]
    test_cr.status = ChangeRequestStatus.completed
    await db_session.commit()
    from app.services.finding_service import close_linked_findings
    await close_linked_findings(db_session, str(test_cr.id))
    await db_session.refresh(test_finding)
    assert test_finding.state == "remediated"
```

- [ ] **Step 2: Add `state` to Finding model**

```python
state: Mapped[str] = mapped_column(String(32), default="open", nullable=False, index=True)
remediated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
remediated_by_cr_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
```

- [ ] **Step 3: Create finding_service.py**

`backend/app/services/finding_service.py`:
```python
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.finding import Finding
from app.models.change_request import ChangeRequest


async def close_linked_findings(db: AsyncSession, cr_id: str) -> int:
    """Mark all findings linked to cr_id as remediated. Returns count closed."""
    cr_uuid = uuid.UUID(cr_id)
    cr = await db.get(ChangeRequest, cr_uuid)
    if not cr or not cr.finding_ids:
        return 0
    count = 0
    for fid in cr.finding_ids:
        result = await db.execute(
            select(Finding).where(Finding.id == uuid.UUID(fid))
        )
        finding = result.scalar_one_or_none()
        if finding and finding.state == "open":
            finding.state = "remediated"
            finding.remediated_at = datetime.now(timezone.utc)
            finding.remediated_by_cr_id = cr_uuid
            count += 1
    await db.commit()
    return count
```

- [ ] **Step 4: Call in workflow on completion**

In `execute_change_workflow.py`, after `update_change_request_status(cr_id, "completed")`:
```python
from app.services.finding_service import close_linked_findings
closed = await close_linked_findings(db_context, cr_id)
if closed > 0:
    await write_audit_event(
        organization_id=org_id,
        event_type="findings.auto_closed",
        event_payload={"count": closed, "cr_id": cr_id},
        actor_id=actor_id,
        change_request_id=cr_id,
    )
```

- [ ] **Step 5: Run test, migrate, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_finding_state"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_finding_auto_closure.py -v 2>&1 | tail -5
git add backend/ && git commit -m "feat: vulnerability findings auto-close when linked patch CR completes"
```

---

### Task 15: Cross-asset timeline

**Files:**
- Create: `backend/app/routers/asset_timeline.py`
- Modify: `backend/app/main.py`
- Create: `frontend/src/pages/AssetDetail.tsx` (add Activity tab — modify existing)

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_asset_timeline.py
@pytest.mark.asyncio
async def test_asset_timeline_returns_cr_events(client, admin_token, test_asset_id, test_cr_id):
    resp = await client.get(f"/assets/{test_asset_id}/timeline",
        headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    events = resp.json()
    assert any(e["resource_type"] == "change_request" for e in events)
```

- [ ] **Step 2: Create asset timeline endpoint**

`backend/app/routers/asset_timeline.py`:
```python
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.auth import current_user
from app.models.user import User
from app.models.change_request import ChangeRequest
from app.models.audit_event import AuditEvent

router = APIRouter(prefix="/assets", tags=["Assets"])


class TimelineEvent(BaseModel):
    id: str
    timestamp: datetime
    event_type: str
    resource_type: str
    resource_id: str
    description: str
    actor_id: str | None
    outcome: str | None


@router.get("/{asset_id}/timeline", response_model=list[TimelineEvent])
async def asset_timeline(
    asset_id: uuid.UUID,
    limit: int = 100,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    asset_id_str = str(asset_id)
    events: list[TimelineEvent] = []

    # Change requests targeting this asset
    cr_result = await db.execute(
        select(ChangeRequest).where(
            ChangeRequest.organization_id == user.organization_id,
            ChangeRequest.target_asset_ids.contains([asset_id_str]),
        ).order_by(ChangeRequest.created_at.desc()).limit(limit)
    )
    for cr in cr_result.scalars():
        events.append(TimelineEvent(
            id=str(cr.id),
            timestamp=cr.created_at,
            event_type=f"cr.{cr.status.value}",
            resource_type="change_request",
            resource_id=str(cr.id),
            description=f"{cr.change_type}: {cr.title}",
            actor_id=str(cr.requester_id) if cr.requester_id else None,
            outcome=cr.status.value,
        ))

    # Audit events for this asset
    audit_result = await db.execute(
        select(AuditEvent).where(
            AuditEvent.organization_id == user.organization_id,
            AuditEvent.change_request_id.in_(
                select(ChangeRequest.id).where(
                    ChangeRequest.target_asset_ids.contains([asset_id_str])
                )
            )
        ).order_by(AuditEvent.created_at.desc()).limit(limit)
    )
    for ev in audit_result.scalars():
        events.append(TimelineEvent(
            id=str(ev.id),
            timestamp=ev.created_at,
            event_type=ev.event_type,
            resource_type="audit_event",
            resource_id=str(ev.id),
            description=ev.event_type,
            actor_id=str(ev.actor_id) if ev.actor_id else None,
            outcome=None,
        ))

    events.sort(key=lambda e: e.timestamp, reverse=True)
    return events[:limit]
```

- [ ] **Step 3: Add Activity tab to AssetDetail**

In `frontend/src/pages/AssetDetail.tsx`, add a tab for "Activity" that fetches `/assets/:id/timeline` and renders a chronological list of events.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/asset_timeline.py backend/app/tests/test_asset_timeline.py \
        backend/app/main.py frontend/src/pages/AssetDetail.tsx
git commit -m "feat: cross-asset timeline — unified event log on asset detail page"
```

---

### Task 16: Application-aware post-change verification

**Files:**
- Modify: `backend/app/models/change_request.py` (add `verification_checks` field)
- Modify: `backend/app/schemas/change_request.py`
- Create: `backend/app/services/verification_check_service.py`
- Modify: `backend/app/workflows/activities.py` (run checks after execution)
- Create: `backend/alembic/versions/XXXX_add_cr_verification_checks.py`

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_verification_checks.py
@pytest.mark.asyncio
async def test_http_check_passes(mocker):
    from app.services.verification_check_service import run_verification_checks
    mock_get = mocker.AsyncMock(return_value=mocker.MagicMock(status_code=200))
    mocker.patch("httpx.AsyncClient.get", mock_get)
    results = await run_verification_checks([
        {"type": "http", "url": "http://localhost:80", "expected_status": 200}
    ], asset_ids=["asset-1"])
    assert results[0]["passed"] is True

@pytest.mark.asyncio
async def test_http_check_fails_wrong_status(mocker):
    from app.services.verification_check_service import run_verification_checks
    mock_get = mocker.AsyncMock(return_value=mocker.MagicMock(status_code=500))
    mocker.patch("httpx.AsyncClient.get", mock_get)
    results = await run_verification_checks([
        {"type": "http", "url": "http://localhost:80", "expected_status": 200}
    ], asset_ids=["asset-1"])
    assert results[0]["passed"] is False
    assert "500" in results[0]["detail"]
```

- [ ] **Step 2: Create verification_check_service.py**

`backend/app/services/verification_check_service.py`:
```python
from __future__ import annotations
import asyncio
from typing import Any

import httpx


async def run_verification_checks(
    checks: list[dict[str, Any]],
    asset_ids: list[str],
) -> list[dict[str, Any]]:
    """Run each verification check and return results."""
    results = []
    for check in checks:
        result = await _run_single_check(check, asset_ids)
        results.append(result)
    return results


async def _run_single_check(check: dict, asset_ids: list[str]) -> dict:
    check_type = check.get("type", "")
    try:
        if check_type == "http":
            return await _http_check(check)
        elif check_type == "service":
            return await _service_check(check, asset_ids)
        elif check_type == "port":
            return await _port_check(check)
        elif check_type == "command":
            return await _command_check(check, asset_ids)
        else:
            return {"type": check_type, "passed": False, "detail": f"Unknown check type: {check_type}"}
    except Exception as e:
        return {"type": check_type, "passed": False, "detail": str(e)}


async def _http_check(check: dict) -> dict:
    url = check["url"]
    expected = check.get("expected_status", 200)
    body_contains = check.get("body_contains")
    async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
        resp = await client.get(url)
    passed = resp.status_code == expected
    detail = f"HTTP {resp.status_code}"
    if body_contains and body_contains not in resp.text:
        passed = False
        detail += f" — body did not contain '{body_contains}'"
    return {"type": "http", "url": url, "passed": passed, "detail": detail}


async def _service_check(check: dict, asset_ids: list[str]) -> dict:
    # Dispatch agent job to check systemd service status
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    service = check["service_name"]
    expected_state = check.get("expected_state", "active")
    result = await dispatch_agent_job(
        command="audit_os_security_posture",
        parameters={"check_service": service},
        asset_ids=asset_ids,
        timeout_seconds=30,
    )
    state = result.get("services", {}).get(service, {}).get("active_state", "unknown")
    passed = state == expected_state
    return {"type": "service", "service": service, "passed": passed, "detail": f"state={state}"}


async def _port_check(check: dict) -> dict:
    host = check["host"]
    port = int(check["port"])
    expect_open = check.get("expected_open", True)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
        writer.close()
        is_open = True
    except Exception:
        is_open = False
    passed = is_open == expect_open
    return {"type": "port", "host": host, "port": port, "passed": passed,
            "detail": f"port {'open' if is_open else 'closed'}"}


async def _command_check(check: dict, asset_ids: list[str]) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    cmd = check["command"]
    expected_exit = check.get("expected_exit_code", 0)
    result = await dispatch_agent_job(
        command="run_ssm_command",
        parameters={"command": cmd},
        asset_ids=asset_ids,
        timeout_seconds=30,
    )
    exit_code = result.get("exit_code", -1)
    passed = exit_code == expected_exit
    if "stdout_contains" in check:
        passed = passed and check["stdout_contains"] in result.get("stdout", "")
    return {"type": "command", "command": cmd, "passed": passed, "detail": f"exit_code={exit_code}"}
```

- [ ] **Step 3: Add to CR model/schema and call in workflow**

Add `verification_checks: list[dict] = []` to model (JSONB column) and schema. In `execute_change_workflow.py`, after execution completes successfully, before marking `completed`:

```python
if data.get("verification_checks"):
    from app.services.verification_check_service import run_verification_checks
    check_results = await run_verification_checks(
        data["verification_checks"], data["target_asset_ids"]
    )
    all_passed = all(r["passed"] for r in check_results)
    if not all_passed:
        failed = [r for r in check_results if not r["passed"]]
        await update_change_request_status(cr_id, "verification_failed")
        if execution_run_id:
            await update_execution_run_status(
                execution_run_id, "failed",
                {"execution": execution_result, "verification_checks": check_results,
                 "failed_checks": failed}
            )
        return
```

- [ ] **Step 4: Run tests, migrate, commit**

```bash
docker compose exec backend pytest app/tests/test_verification_checks.py -v 2>&1 | tail -10
docker compose exec backend alembic revision --autogenerate -m "add_cr_verification_checks"
docker compose exec backend alembic upgrade head
git add backend/ && git commit -m "feat: application-aware post-change verification checks (http/service/port/command)"
```

---

## Phases 2–4: Structured Stubs

*Each phase below needs its own implementation planning session. The task headings and file lists are complete; code will be written when the phase is ready to implement.*

---

## Phase 2 — Scale and Handoff

### Task 17: Bulk CR creation
- **Files:** `backend/app/routers/change_requests.py` (add `POST /change-requests/batch`), `backend/app/schemas/change_request.py` (BatchCreateRequest), `frontend/src/pages/Assets.tsx` (bulk action bar), `backend/app/tests/test_bulk_cr.py`
- `POST /change-requests/batch` accepts `[{change_type, target_asset_id, desired_outcome, ...}]`, creates all CRs, returns `{batch_id, cr_ids}`. `batch_id` is stored on each CR.

### Task 18: Bulk approval
- **Files:** `backend/app/routers/change_requests.py` (add `POST /change-requests/bulk-approve`), `frontend/src/pages/ApprovalsQueue.tsx`
- Rejects if any selected CR is above `critical` risk (configurable).

### Task 19: Campaign failure policy
- **Files:** `backend/app/models/patch_campaign.py` (add `failure_policy` field), `backend/app/services/patch_campaign_service.py`, `frontend/src/components/PatchCampaignList.tsx`

### Task 20: Emergency priority + escalation
- **Files:** `backend/app/models/change_request.py` (add `priority`, `emergency_reason`), `backend/app/models/org_settings.py` (add `escalation_chain`), `backend/app/workers/escalation_worker.py` (background check), `frontend/src/pages/CreateChangeRequest.tsx`

### Task 21: Finding "mitigated" state
- **Files:** `backend/app/models/finding.py` (add `mitigated_at`, `mitigated_by_cr_id`, `patch_available_at`), `backend/app/services/finding_service.py`, `frontend/src/pages/VulnerabilityRemediation.tsx`

### Task 22: Access review → remediation CR
- **Files:** `backend/app/models/access_review.py` (add `remediation_cr_id` to entries), `backend/app/routers/access_reviews.py` (add `/entries/:id/create-remediation`), `frontend/src/pages/AccessReviewDetail.tsx`

### Task 23: Re-scan trigger on CR completion
- **Files:** `backend/app/workflows/execute_change_workflow.py`, `backend/app/services/scanner_service.py`, per-connector rescan implementation

### Task 24: Compliance "fix all" + attestation
- **Files:** `backend/app/routers/compliance.py` (add `POST /compliance/controls/:id/remediate-all` and `POST /compliance/controls/:id/attest`), `backend/app/models/compliance_attestation.py`, `frontend/src/pages/Compliance.tsx`

### Task 25: Staging → production promotion
- **Files:** `backend/app/models/project.py` (add `ProjectPhase`), `backend/app/workers/soak_timer_worker.py`, `frontend/src/pages/ProjectDetail.tsx`

### Task 26: External ticket closure
- **Files:** `backend/app/models/change_request.py` (add `external_links`), `backend/app/services/external_ticket_service.py` (Jira/PagerDuty), `backend/app/workflows/execute_change_workflow.py`

---

## Phase 3 — Identity Security

### Task 27: Emergency user lockout CR type
- **Files:** `backend/app/connectors/executors/nexplane_agent/emergency_user_lockout.py`, `backend/app/connectors/executors/aws/disable_iam_user.py` (extend), connector fan-out logic, `agent/commands/linuxauth/` (Linux local user lockout), `frontend/src/pages/ChangeRequestList.tsx` (IR tab: "Isolate User")

### Task 28: Temporary user suspension + reversal
- **Files:** New CR type `user_suspension`, scheduled `user_suspension_reversal` CR (via `execute_at` field), AWS IAM and AD implementations

### Task 29: Session termination
- **Files:** Okta connector `terminate_sessions`, AWS STS session invalidation, `backend/app/connectors/executors/` per-IdP

### Task 30: Scope reduction
- **Files:** `user_scope_reduction` CR type with modes: `demote_to_readonly`, `ip_restriction`, `mfa_required`; AWS IAM policy attachment per mode

### Task 31: Time-bound access grants
- **Files:** `backend/app/models/change_request.py` (add `access_expiry_hours`, `scheduled_rollback_cr_id`), `backend/app/workers/access_expiry_worker.py`, scheduled rollback CR creation

### Task 32: MFA enforcement as remediation
- **Files:** `enforce_mfa` CR type, AWS IAM MFA-required condition policy, Okta factor enrollment policy

---

## Phase 4 — Proactive Hardening

### Task 33: Hardening project templates
- **Files:** `backend/app/models/project.py` (add `template` field), `backend/app/routers/projects.py` (template creation endpoint), template definitions for seccomp-rollout/microsegmentation/AppArmor-rollout, `frontend/src/pages/Projects.tsx`

### Task 34: Generalized learn → enforce pipeline
- **Files:** New Python executors for `seccomp_learn`, `firewall_log_baseline`, `apparmor_complain` (already wired — just add `duration_seconds` to learn mode), `apparmor_complain_collect` result parsing, `backend/app/services/policy_baseline_service.py`, `backend/app/models/policy_baseline.py`

### Task 35: AI policy generation from discovery
- **Files:** `backend/app/routers/policy_generate.py` (`POST /policy/generate` — takes learn result, returns policy), uses existing AI service (`ai_service.py`), prompt templates per control type (seccomp/AppArmor/iptables/WDAC/ASR)

### Task 36: Policy drift detection
- **Files:** `backend/app/models/policy_baseline.py` (create), `backend/app/workers/drift_check_worker.py` (weekly APScheduler job), new `drift_check` agent command (Linux: Task 7 above; Windows: Task 6 above), `frontend/src/pages/Compliance.tsx` (drift alerts section)

### Task 37: Threat model linkage
- **Files:** `backend/app/models/project.py` (add `risk_context` JSONB), `backend/app/schemas/project.py`, `frontend/src/pages/Projects.tsx`, `frontend/src/pages/ProjectDetail.tsx`

---

## OSSEC_WIRE and WIN_OSSEC_WIRE Smoke Phases

*(WIN_OSSEC_WIRE requires a Windows Server 2022 EC2 — use `--phases A_WIN,WIN_OSSEC_WIRE` with a Windows AMI override.)*

Smoke tests for Phases 1–4 are described in the spec at `docs/superpowers/specs/2026-05-13-front-to-back-workflow-design.md` under the Smoke Test Phase Catalog and are implemented alongside each feature task (Tasks 9, and future tasks for FIND_LIFECYCLE, BULK_PATCH, etc.).
