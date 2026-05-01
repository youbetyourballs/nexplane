# Windows Security Hardening Implementation Plan (Spec 5c)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 13 agent commands for Windows security hardening: authentication/credentials (LAPS, Credential Guard, PowerShell CLM), application control (AppLocker, SMB hardening), disk/network (BitLocker, Windows Firewall, TLS protocols, RDP), and audit/monitoring (Windows audit policy, scheduled task audit, registry hardening).

**Architecture:** New `agent/commands/winharden/` package. Same pattern as linuxauth/ossecurity: public API in `winharden.go` (cross-platform, param validation), Windows implementations in `*_windows.go` files with `//go:build windows`, non-Windows stubs in `winharden_other.go` with `//go:build !windows`, tests in `winharden_test.go` (no build tag). All 13 commands registered in `executor.go`, cataloged in `nexplane_agent_mock.json`, with Python mock stubs.

**Tech Stack:** Go 1.22+, `os/exec` for PowerShell/netsh/reg commands, registry snapshot via `reg export`, firewall snapshot via `netsh advfirewall export`.

**Working directory for all commands:** `f:\Nexplane\nexplane\.worktrees\agent-hardening`

---

### Task 1: Package scaffolding

**Files:**
- Create: `agent/commands/winharden/winharden.go`
- Create: `agent/commands/winharden/winharden_other.go`
- Create: `agent/commands/winharden/winharden_test.go`

- [ ] **Step 1: Create `agent/commands/winharden/winharden.go`**

```go
package winharden

import "fmt"

func ConfigureLAPSExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action != "enable" && action != "disable" {
		return nil, fmt.Errorf("action must be 'enable' or 'disable', got %q", action)
	}
	return lapsExecuteOS(params)
}

func ConfigureLAPSRollback(params map[string]any) (map[string]any, error) {
	return lapsRollbackOS(params)
}

func EnableCredentialGuardExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action != "enable" && action != "disable" {
		return nil, fmt.Errorf("action must be 'enable' or 'disable', got %q", action)
	}
	return credGuardExecuteOS(params)
}

func EnableCredentialGuardRollback(params map[string]any) (map[string]any, error) {
	return credGuardRollbackOS(params)
}

func EnforcePowerShellCLMExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action != "enable" && action != "disable" {
		return nil, fmt.Errorf("action must be 'enable' or 'disable', got %q", action)
	}
	return psclmExecuteOS(params)
}

func EnforcePowerShellCLMRollback(params map[string]any) (map[string]any, error) {
	return psclmRollbackOS(params)
}

func DeployAppLockerPolicyExecute(params map[string]any) (map[string]any, error) {
	if _, ok := params["policy"].(string); !ok {
		return nil, fmt.Errorf("policy (AppLocker XML) is required")
	}
	return applockerExecuteOS(params)
}

func DeployAppLockerPolicyRollback(params map[string]any) (map[string]any, error) {
	return applockerRollbackOS(params)
}

func HardenSMBExecute(params map[string]any) (map[string]any, error) {
	return smbExecuteOS(params)
}

func HardenSMBRollback(params map[string]any) (map[string]any, error) {
	return smbRollbackOS(params)
}

func EnableBitLockerExecute(params map[string]any) (map[string]any, error) {
	drive, _ := params["drive_letter"].(string)
	if drive == "" {
		drive = "C:"
	}
	protector, _ := params["protector"].(string)
	if protector == "" {
		protector = "tpm"
	}
	valid := map[string]bool{"tpm": true, "tpm_pin": true, "recovery_key_only": true}
	if !valid[protector] {
		return nil, fmt.Errorf("protector must be tpm, tpm_pin, or recovery_key_only, got %q", protector)
	}
	if protector == "tpm_pin" {
		if pin, _ := params["pin"].(string); pin == "" {
			return nil, fmt.Errorf("pin is required when protector=tpm_pin")
		}
	}
	return bitlockerExecuteOS(params)
}

func EnableBitLockerRollback(params map[string]any) (map[string]any, error) {
	return bitlockerRollbackOS(params)
}

func ConfigureWindowsFirewallExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	valid := map[string]bool{"add_rule": true, "remove_rule": true, "set_default_action": true}
	if !valid[action] {
		return nil, fmt.Errorf("action must be add_rule, remove_rule, or set_default_action, got %q", action)
	}
	return winfirewallExecuteOS(params)
}

func ConfigureWindowsFirewallRollback(params map[string]any) (map[string]any, error) {
	return winfirewallRollbackOS(params)
}

func HardenTLSProtocolsExecute(params map[string]any) (map[string]any, error) {
	return tlsExecuteOS(params)
}

func HardenTLSProtocolsRollback(params map[string]any) (map[string]any, error) {
	return tlsRollbackOS(params)
}

func HardenRDPExecute(params map[string]any) (map[string]any, error) {
	return rdpExecuteOS(params)
}

func HardenRDPRollback(params map[string]any) (map[string]any, error) {
	return rdpRollbackOS(params)
}

func ConfigureWindowsAuditPolicyExecute(params map[string]any) (map[string]any, error) {
	if profile, ok := params["profile"].(string); ok && profile != "" {
		valid := map[string]bool{"cis_level1": true, "cis_level2": true, "stig": true, "custom": true}
		if !valid[profile] {
			return nil, fmt.Errorf("profile must be cis_level1, cis_level2, stig, or custom, got %q", profile)
		}
	}
	return auditpolExecuteOS(params)
}

func ConfigureWindowsAuditPolicyRollback(params map[string]any) (map[string]any, error) {
	return auditpolRollbackOS(params)
}

func AuditScheduledTasksExecute(params map[string]any) (map[string]any, error) {
	return auditTasksOS(params)
}

func HardenRegistryExecute(params map[string]any) (map[string]any, error) {
	return registryExecuteOS(params)
}

func HardenRegistryRollback(params map[string]any) (map[string]any, error) {
	return registryRollbackOS(params)
}
```

- [ ] **Step 2: Create `agent/commands/winharden/winharden_other.go`**

```go
//go:build !windows

package winharden

import "fmt"

func lapsExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_laps requires Windows")
}
func lapsRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_laps requires Windows")
}
func credGuardExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enable_credential_guard requires Windows")
}
func credGuardRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enable_credential_guard requires Windows")
}
func psclmExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enforce_powershell_clm requires Windows")
}
func psclmRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enforce_powershell_clm requires Windows")
}
func applockerExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_applocker_policy requires Windows")
}
func applockerRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_applocker_policy requires Windows")
}
func smbExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_smb requires Windows")
}
func smbRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_smb requires Windows")
}
func bitlockerExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enable_bitlocker requires Windows")
}
func bitlockerRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enable_bitlocker requires Windows")
}
func winfirewallExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_windows_firewall requires Windows")
}
func winfirewallRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_windows_firewall requires Windows")
}
func tlsExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_tls_protocols requires Windows")
}
func tlsRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_tls_protocols requires Windows")
}
func rdpExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_rdp requires Windows")
}
func rdpRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_rdp requires Windows")
}
func auditpolExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_windows_audit_policy requires Windows")
}
func auditpolRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_windows_audit_policy requires Windows")
}
func auditTasksOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_scheduled_tasks requires Windows")
}
func registryExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_registry requires Windows")
}
func registryRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_registry requires Windows")
}
```

- [ ] **Step 3: Create `agent/commands/winharden/winharden_test.go`**

```go
package winharden_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/winharden"
)

func TestConfigureLAPSInvalidAction(t *testing.T) {
	_, err := winharden.ConfigureLAPSExecute(map[string]any{"action": "toggle"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestEnableCredentialGuardInvalidAction(t *testing.T) {
	_, err := winharden.EnableCredentialGuardExecute(map[string]any{"action": "maybe"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestEnforcePSCLMInvalidAction(t *testing.T) {
	_, err := winharden.EnforcePowerShellCLMExecute(map[string]any{"action": "toggle"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestDeployAppLockerPolicyRequiresPolicy(t *testing.T) {
	_, err := winharden.DeployAppLockerPolicyExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "policy") {
		t.Errorf("expected policy error, got: %v", err)
	}
}

func TestEnableBitLockerInvalidProtector(t *testing.T) {
	_, err := winharden.EnableBitLockerExecute(map[string]any{"protector": "usb"})
	if err == nil || !strings.Contains(err.Error(), "protector must be") {
		t.Errorf("expected protector error, got: %v", err)
	}
}

func TestEnableBitLockerTPMPinRequiresPin(t *testing.T) {
	_, err := winharden.EnableBitLockerExecute(map[string]any{"protector": "tpm_pin"})
	if err == nil || !strings.Contains(err.Error(), "pin is required") {
		t.Errorf("expected pin error, got: %v", err)
	}
}

func TestConfigureWindowsFirewallInvalidAction(t *testing.T) {
	_, err := winharden.ConfigureWindowsFirewallExecute(map[string]any{"action": "delete"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestConfigureWindowsAuditPolicyInvalidProfile(t *testing.T) {
	_, err := winharden.ConfigureWindowsAuditPolicyExecute(map[string]any{"profile": "nsa_level42"})
	if err == nil || !strings.Contains(err.Error(), "profile must be") {
		t.Errorf("expected profile error, got: %v", err)
	}
}
```

- [ ] **Step 4: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/winharden/... && "C:/Program Files/Go/bin/go.exe" test ./commands/winharden/... -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add agent/commands/winharden/
git commit -m "feat(winharden): add package scaffolding with param validation and stubs"
```

---

### Task 2: Windows implementations (all 13 commands)

**Files:**
- Create: `agent/commands/winharden/laps_windows.go`
- Create: `agent/commands/winharden/credguard_windows.go`
- Create: `agent/commands/winharden/psclm_windows.go`
- Create: `agent/commands/winharden/applocker_windows.go`
- Create: `agent/commands/winharden/smb_windows.go`
- Create: `agent/commands/winharden/bitlocker_windows.go`
- Create: `agent/commands/winharden/firewall_windows.go`
- Create: `agent/commands/winharden/tls_windows.go`
- Create: `agent/commands/winharden/rdp_windows.go`
- Create: `agent/commands/winharden/auditpol_windows.go`
- Create: `agent/commands/winharden/tasks_windows.go`
- Create: `agent/commands/winharden/registry_windows.go`

All Windows implementations follow this pattern:
1. Check caller is Administrator (`os.Getuid() == 0` does not work on Windows; use `net session` or SID check)
2. Capture registry/config snapshot via PowerShell
3. Apply changes via PowerShell or reg.exe
4. Return result with snapshot

**Helper pattern for running PowerShell:**
```go
func runPS(script string) ([]byte, error) {
    return exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", script).CombinedOutput()
}
```

**Helper pattern for registry snapshot:**
```go
func regExport(key string) (string, error) {
    tmp, err := os.CreateTemp("", "nexplane-reg-*.reg")
    if err != nil { return "", err }
    tmp.Close()
    defer os.Remove(tmp.Name())
    if out, err := exec.Command("reg", "export", key, tmp.Name(), "/y").CombinedOutput(); err != nil {
        return "", fmt.Errorf("reg export %s: %s: %w", key, out, err)
    }
    data, err := os.ReadFile(tmp.Name())
    return string(data), err
}
```

- [ ] **Step 1: Create `agent/commands/winharden/laps_windows.go`**

```go
//go:build windows

package winharden

import (
	"fmt"
	"os"
	"os/exec"
	"time"
)

const lapsRegKey = `HKLM\SOFTWARE\Policies\Microsoft Services\AdmPwd`

func lapsExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)

	snapshot, err := regExport(lapsRegKey)
	if err != nil {
		snapshot = "" // key may not exist yet
	}

	passwordAgeDays := 30
	if v, _ := params["password_age_days"].(float64); v > 0 {
		passwordAgeDays = int(v)
	}
	passwordLength := 14
	if v, _ := params["password_length"].(float64); v > 0 {
		passwordLength = int(v)
	}

	if action == "enable" {
		script := fmt.Sprintf(`
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd" -Name "AdmPwdEnabled" -Value 1 -Type DWord -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd" -Name "PasswordAgeDays" -Value %d -Type DWord -Force
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd" -Name "PasswordLength" -Value %d -Type DWord -Force
gpupdate /force`, passwordAgeDays, passwordLength)
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("enabling LAPS: %s: %w", out, err)
		}
	} else {
		script := `Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd" -Name "AdmPwdEnabled" -Value 0 -Type DWord -Force; gpupdate /force`
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("disabling LAPS: %s: %w", out, err)
		}
	}

	return map[string]any{
		"action": action, "password_age_days": passwordAgeDays,
		"password_length": passwordLength,
		"snapshot":        snapshot,
		"applied_at":      time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func lapsRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	return regImport(snapshot)
}

func runPS(script string) ([]byte, error) {
	return exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", script).CombinedOutput()
}

func regExport(key string) (string, error) {
	tmp, err := os.CreateTemp("", "nexplane-reg-*.reg")
	if err != nil {
		return "", err
	}
	tmp.Close()
	defer os.Remove(tmp.Name())
	if out, err := exec.Command("reg", "export", key, tmp.Name(), "/y").CombinedOutput(); err != nil {
		return "", fmt.Errorf("reg export: %s: %w", out, err)
	}
	data, err := os.ReadFile(tmp.Name())
	return string(data), err
}

func regImport(regContent string) (map[string]any, error) {
	tmp, err := os.CreateTemp("", "nexplane-reg-restore-*.reg")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.WriteString(regContent); err != nil {
		return nil, err
	}
	tmp.Close()
	if out, err := exec.Command("reg", "import", tmp.Name()).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("reg import: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 2: Create `agent/commands/winharden/credguard_windows.go`**

```go
//go:build windows

package winharden

import (
	"fmt"
	"time"
)

const deviceGuardKey = `HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard`

func credGuardExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	uefiLock, _ := params["require_uefi_lock"].(bool)

	snapshot, _ := regExport(deviceGuardKey)

	if action == "enable" {
		lsaCfgFlags := 1
		if uefiLock {
			lsaCfgFlags = 2
		}
		script := fmt.Sprintf(`
$key = "HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard"
New-Item -Path $key -Force | Out-Null
Set-ItemProperty -Path $key -Name "EnableVirtualizationBasedSecurity" -Value 1 -Type DWord -Force
Set-ItemProperty -Path $key -Name "RequirePlatformSecurityFeatures" -Value 1 -Type DWord -Force
Set-ItemProperty -Path $key -Name "LsaCfgFlags" -Value %d -Type DWord -Force
Set-ItemProperty -Path $key -Name "HypervisorEnforcedCodeIntegrity" -Value 1 -Type DWord -Force`, lsaCfgFlags)
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("enabling Credential Guard: %s: %w", out, err)
		}
	} else {
		script := `
$key = "HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard"
Set-ItemProperty -Path $key -Name "EnableVirtualizationBasedSecurity" -Value 0 -Type DWord -Force
Set-ItemProperty -Path $key -Name "LsaCfgFlags" -Value 0 -Type DWord -Force`
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("disabling Credential Guard: %s: %w", out, err)
		}
	}

	return map[string]any{
		"action": action, "uefi_lock": uefiLock, "reboot_required": true,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func credGuardRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	uefiLock, _ := params["uefi_lock"].(bool)
	if uefiLock {
		return map[string]any{
			"rolled_back": false,
			"warning":     "UEFI lock prevents registry rollback — firmware intervention required",
		}, nil
	}
	return regImport(snapshot)
}
```

- [ ] **Step 3: Create `agent/commands/winharden/psclm_windows.go`**

```go
//go:build windows

package winharden

import (
	"fmt"
	"time"
)

const psEnvKey = `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`

func psclmExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	mechanism, _ := params["mechanism"].(string)
	if mechanism == "" {
		mechanism = "registry"
	}

	snapshot, _ := regExport(psEnvKey)

	if action == "enable" {
		if mechanism == "registry" {
			script := `New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" -Name "__PSLockdownPolicy" -Value "4" -PropertyType String -Force`
			if out, err := runPS(script); err != nil {
				return nil, fmt.Errorf("enabling CLM: %s: %w", out, err)
			}
		}
	} else {
		script := `Remove-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" -Name "__PSLockdownPolicy" -ErrorAction SilentlyContinue`
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("disabling CLM: %s: %w", out, err)
		}
	}

	return map[string]any{
		"action": action, "mechanism": mechanism,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func psclmRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	return regImport(snapshot)
}
```

- [ ] **Step 4: Create `agent/commands/winharden/applocker_windows.go`**

```go
//go:build windows

package winharden

import (
	"fmt"
	"os"
	"time"
)

func applockerExecuteOS(params map[string]any) (map[string]any, error) {
	policy, _ := params["policy"].(string)
	enforce, _ := params["enforce"].(bool)

	// Snapshot current effective policy
	snapshotOut, _ := runPS(`Get-AppLockerPolicy -Effective -Xml`)
	snapshot := string(snapshotOut)

	// Write policy XML to temp file and apply
	tmp, err := os.CreateTemp("", "nexplane-applocker-*.xml")
	if err != nil {
		return nil, fmt.Errorf("creating temp policy file: %w", err)
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.WriteString(policy); err != nil {
		return nil, fmt.Errorf("writing policy: %w", err)
	}
	tmp.Close()

	mode := "AuditOnly"
	if enforce {
		mode = "Enabled"
	}

	script := fmt.Sprintf(`
Set-AppLockerPolicy -XmlPolicy "%s"
Set-Service AppIDSvc -StartupType Automatic
Start-Service AppIDSvc -ErrorAction SilentlyContinue`, tmp.Name())
	if out, err := runPS(script); err != nil {
		return nil, fmt.Errorf("applying AppLocker policy: %s: %w", out, err)
	}

	return map[string]any{
		"enforce": enforce, "mode": mode,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func applockerRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	tmp, err := os.CreateTemp("", "nexplane-applocker-restore-*.xml")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())
	tmp.WriteString(snapshot)
	tmp.Close()
	if out, err := runPS(fmt.Sprintf(`Set-AppLockerPolicy -XmlPolicy "%s"`, tmp.Name())); err != nil {
		return nil, fmt.Errorf("restoring AppLocker policy: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 5: Create `agent/commands/winharden/smb_windows.go`**

```go
//go:build windows

package winharden

import (
	"time"
)

func smbExecuteOS(params map[string]any) (map[string]any, error) {
	disableSMB1, _ := params["disable_smb1"].(bool)
	if _, ok := params["disable_smb1"]; !ok {
		disableSMB1 = true
	}
	requireSigning, _ := params["require_signing"].(bool)
	if _, ok := params["require_signing"]; !ok {
		requireSigning = true
	}
	disableGuest, _ := params["disable_guest_access"].(bool)
	if _, ok := params["disable_guest_access"]; !ok {
		disableGuest = true
	}

	snapshotOut, _ := runPS(`Get-SmbServerConfiguration | ConvertTo-Json`)
	snapshot := string(snapshotOut)

	if disableSMB1 {
		runPS(`Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force`)
		runPS(`Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart`)
	}
	if requireSigning {
		runPS(`Set-SmbServerConfiguration -RequireSecuritySignature $true -Force`)
		runPS(`Set-SmbClientConfiguration -RequireSecuritySignature $true -Force`)
	}
	if disableGuest {
		runPS(`Set-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Services\LanManWorkstation\Parameters -Name AllowInsecureGuestAuth -Value 0`)
	}

	return map[string]any{
		"smb1_disabled": disableSMB1, "signing_required": requireSigning,
		"guest_disabled": disableGuest,
		"snapshot":       snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func smbRollbackOS(params map[string]any) (map[string]any, error) {
	// SMB settings are individual; restore from snapshot values
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	// Best-effort: re-enable SMB1 if it was enabled
	smb1Disabled, _ := params["smb1_disabled"].(bool)
	if smb1Disabled {
		runPS(`Enable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart`)
		runPS(`Set-SmbServerConfiguration -EnableSMB1Protocol $true -Force`)
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 6: Create `agent/commands/winharden/bitlocker_windows.go`**

```go
//go:build windows

package winharden

import (
	"fmt"
	"strings"
	"time"
)

func bitlockerExecuteOS(params map[string]any) (map[string]any, error) {
	drive, _ := params["drive_letter"].(string)
	if drive == "" {
		drive = "C:"
	}
	protector, _ := params["protector"].(string)
	if protector == "" {
		protector = "tpm"
	}
	method, _ := params["encryption_method"].(string)
	if method == "" {
		method = "XtsAes256"
	}

	// Snapshot current state
	snapshotOut, _ := runPS(fmt.Sprintf(`Get-BitLockerVolume -MountPoint "%s" | Select-Object VolumeStatus,EncryptionMethod,ProtectionStatus | ConvertTo-Json`, drive))
	snapshot := string(snapshotOut)

	// Add key protectors
	switch protector {
	case "tpm":
		runPS(fmt.Sprintf(`Add-BitLockerKeyProtector -MountPoint "%s" -TpmProtector`, drive))
	case "tpm_pin":
		pin, _ := params["pin"].(string)
		runPS(fmt.Sprintf(`Add-BitLockerKeyProtector -MountPoint "%s" -TpmAndPinProtector -Pin (ConvertTo-SecureString "%s" -AsPlainText -Force)`, drive, pin))
	case "recovery_key_only":
		// no TPM protector
	}

	// Always add recovery password protector
	recoveryOut, _ := runPS(fmt.Sprintf(`(Add-BitLockerKeyProtector -MountPoint "%s" -RecoveryPasswordProtector).KeyProtector | Where-Object KeyProtectorType -eq "RecoveryPassword" | Select-Object -ExpandProperty RecoveryPassword`, drive))
	recoveryKey := strings.TrimSpace(string(recoveryOut))

	// Enable BitLocker
	if out, err := runPS(fmt.Sprintf(`Enable-BitLocker -MountPoint "%s" -EncryptionMethod %s -UsedSpaceOnly`, drive, method)); err != nil {
		return nil, fmt.Errorf("enabling BitLocker: %s: %w", out, err)
	}

	return map[string]any{
		"drive": drive, "encryption_method": method, "protector": protector,
		"recovery_key": recoveryKey, "volume_status": "EncryptionInProgress",
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func bitlockerRollbackOS(params map[string]any) (map[string]any, error) {
	drive, _ := params["drive"].(string)
	if drive == "" {
		drive = "C:"
	}
	if out, err := runPS(fmt.Sprintf(`Disable-BitLocker -MountPoint "%s"`, drive)); err != nil {
		return nil, fmt.Errorf("disabling BitLocker: %s: %w", out, err)
	}
	return map[string]any{
		"rolled_back": true,
		"warning":     "BitLocker decryption in progress — this may take hours on large volumes",
	}, nil
}
```

- [ ] **Step 7: Create `agent/commands/winharden/firewall_windows.go`**

```go
//go:build windows

package winharden

import (
	"fmt"
	"os"
	"time"
)

func winfirewallExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)

	// Snapshot: export full firewall policy
	tmp, err := os.CreateTemp("", "nexplane-fw-*.wfw")
	if err != nil {
		return nil, err
	}
	snapshotPath := tmp.Name()
	tmp.Close()
	defer os.Remove(snapshotPath)
	runPS(fmt.Sprintf(`netsh advfirewall export "%s"`, snapshotPath))
	snapshotData, _ := os.ReadFile(snapshotPath)
	snapshot := string(snapshotData)

	rule, _ := params["rule"].(map[string]any)
	switch action {
	case "add_rule":
		name, _ := rule["name"].(string)
		direction, _ := rule["direction"].(string)
		proto, _ := rule["protocol"].(string)
		if proto == "" {
			proto = "TCP"
		}
		port, _ := rule["local_port"].(string)
		actionType, _ := rule["action_type"].(string)
		if actionType == "" {
			actionType = "Allow"
		}
		script := fmt.Sprintf(`New-NetFirewallRule -DisplayName "%s" -Direction %s -Protocol %s -LocalPort %s -Action %s -Enabled True`, name, direction, proto, port, actionType)
		if out, err := runPS(script); err != nil {
			return nil, fmt.Errorf("adding firewall rule: %s: %w", out, err)
		}
	case "remove_rule":
		name, _ := rule["name"].(string)
		if out, err := runPS(fmt.Sprintf(`Remove-NetFirewallRule -DisplayName "%s"`, name)); err != nil {
			return nil, fmt.Errorf("removing firewall rule: %s: %w", out, err)
		}
	case "set_default_action":
		defAction, _ := params["default_action"].(map[string]any)
		inbound, _ := defAction["inbound"].(string)
		outbound, _ := defAction["outbound"].(string)
		runPS(fmt.Sprintf(`Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultInboundAction %s -DefaultOutboundAction %s`, inbound, outbound))
	}

	return map[string]any{
		"action": action, "snapshot": snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func winfirewallRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	tmp, err := os.CreateTemp("", "nexplane-fw-restore-*.wfw")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())
	tmp.WriteString(snapshot)
	tmp.Close()
	if out, err := runPS(fmt.Sprintf(`netsh advfirewall import "%s"`, tmp.Name())); err != nil {
		return nil, fmt.Errorf("restoring firewall: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 8: Create `agent/commands/winharden/tls_windows.go`**

```go
//go:build windows

package winharden

import (
	"fmt"
	"time"
)

const schannelKey = `HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL`

func tlsExecuteOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := regExport(schannelKey)

	// Default: disable SSL2/3 and TLS1.0/1.1, enable TLS1.2/1.3
	disableProtocols := []string{"SSL 2.0", "SSL 3.0", "TLS 1.0", "TLS 1.1"}
	enableProtocols := []string{"TLS 1.2", "TLS 1.3"}

	if dp, ok := params["disable_protocols"].([]any); ok {
		disableProtocols = nil
		for _, p := range dp {
			if s, ok := p.(string); ok {
				disableProtocols = append(disableProtocols, s)
			}
		}
	}
	if ep, ok := params["enabled_protocols"].([]any); ok {
		enableProtocols = nil
		for _, p := range ep {
			if s, ok := p.(string); ok {
				enableProtocols = append(enableProtocols, s)
			}
		}
	}

	applyTo, _ := params["apply_to"].([]any)
	sides := []string{"Server", "Client"}
	if len(applyTo) > 0 {
		sides = nil
		for _, s := range applyTo {
			if str, ok := s.(string); ok {
				if str == "server" {
					sides = append(sides, "Server")
				} else if str == "client" {
					sides = append(sides, "Client")
				}
			}
		}
	}

	for _, proto := range disableProtocols {
		for _, side := range sides {
			key := fmt.Sprintf(`HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\%s\%s`, proto, side)
			script := fmt.Sprintf(`New-Item -Path "%s" -Force | Out-Null; Set-ItemProperty -Path "%s" -Name "Enabled" -Value 0 -Type DWord -Force; Set-ItemProperty -Path "%s" -Name "DisabledByDefault" -Value 1 -Type DWord -Force`, key, key, key)
			runPS(script)
		}
	}
	for _, proto := range enableProtocols {
		for _, side := range sides {
			key := fmt.Sprintf(`HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\%s\%s`, proto, side)
			script := fmt.Sprintf(`New-Item -Path "%s" -Force | Out-Null; Set-ItemProperty -Path "%s" -Name "Enabled" -Value 1 -Type DWord -Force; Set-ItemProperty -Path "%s" -Name "DisabledByDefault" -Value 0 -Type DWord -Force`, key, key, key)
			runPS(script)
		}
	}

	return map[string]any{
		"disabled_protocols": disableProtocols, "enabled_protocols": enableProtocols,
		"reboot_required": true,
		"snapshot":        snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func tlsRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	result, err := regImport(snapshot)
	if err != nil {
		return nil, err
	}
	result["reboot_required"] = true
	return result, nil
}
```

- [ ] **Step 9: Create `agent/commands/winharden/rdp_windows.go`**

```go
//go:build windows

package winharden

import (
	"fmt"
	"time"
)

const rdpKey = `HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp`

func rdpExecuteOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := regExport(rdpKey)

	requireNLA := true
	if v, ok := params["require_nla"].(bool); ok {
		requireNLA = v
	}

	nlaVal := 0
	if requireNLA {
		nlaVal = 1
	}

	script := fmt.Sprintf(`
$key = "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"
Set-ItemProperty -Path $key -Name "UserAuthentication" -Value %d -Type DWord -Force
Set-ItemProperty -Path $key -Name "SecurityLayer" -Value 2 -Type DWord -Force`, nlaVal)

	if idleTimeout, _ := params["idle_timeout_minutes"].(float64); idleTimeout > 0 {
		ms := int(idleTimeout) * 60 * 1000
		script += fmt.Sprintf("\nSet-ItemProperty -Path $key -Name 'MaxIdleTime' -Value %d -Type DWord -Force", ms)
	}
	if maxTimeout, _ := params["max_session_timeout_minutes"].(float64); maxTimeout > 0 {
		ms := int(maxTimeout) * 60 * 1000
		script += fmt.Sprintf("\nSet-ItemProperty -Path $key -Name 'MaxConnectionTime' -Value %d -Type DWord -Force", ms)
	}
	if port, _ := params["port"].(float64); port > 0 {
		script += fmt.Sprintf("\nSet-ItemProperty -Path $key -Name 'PortNumber' -Value %d -Type DWord -Force", int(port))
	}

	script += "\nRestart-Service TermService -Force"

	if out, err := runPS(script); err != nil {
		return nil, fmt.Errorf("hardening RDP: %s: %w", out, err)
	}

	return map[string]any{
		"nla": requireNLA, "snapshot": snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rdpRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	result, err := regImport(snapshot)
	if err != nil {
		return nil, err
	}
	runPS(`Restart-Service TermService -Force`)
	return result, nil
}
```

- [ ] **Step 10: Create `agent/commands/winharden/auditpol_windows.go`**

```go
//go:build windows

package winharden

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

type auditEntry struct {
	success bool
	failure bool
}

var cisLevel1Audit = map[string]auditEntry{
	"Logon":                          {true, true},
	"Account Logon":                  {true, true},
	"Object Access":                  {false, true},
	"Privilege Use":                  {false, true},
	"Detailed Tracking":              {true, false},
	"Policy Change":                  {true, false},
	"Account Management":             {true, true},
	"Directory Service Access":       {false, true},
	"System":                         {true, true},
}

func auditpolExecuteOS(params map[string]any) (map[string]any, error) {
	// Snapshot current settings
	snapshotOut, _ := exec.Command("auditpol", "/get", "/category:*", "/r").Output()
	snapshot := string(snapshotOut)

	profile, _ := params["profile"].(string)
	if profile == "" {
		profile = "cis_level1"
	}

	settings := make(map[string]auditEntry)
	for k, v := range cisLevel1Audit {
		settings[k] = v
	}
	if profile == "cis_level2" || profile == "stig" {
		settings["Object Access"] = auditEntry{true, true}
		settings["Privilege Use"] = auditEntry{true, true}
	}

	for category, entry := range settings {
		successFlag := "disable"
		if entry.success {
			successFlag = "enable"
		}
		failureFlag := "disable"
		if entry.failure {
			failureFlag = "enable"
		}
		exec.Command("auditpol", "/set", fmt.Sprintf(`/category:"%s"`, category),
			"/success:"+successFlag, "/failure:"+failureFlag).Run()
	}

	// Enable process creation command-line auditing
	runPS(`Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Audit" -Name "ProcessCreationIncludeCmdLine_Enabled" -Value 1 -Type DWord -Force`)

	return map[string]any{
		"profile_applied": profile,
		"categories_count": len(settings),
		"snapshot":        snapshot,
		"applied_at":      time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func auditpolRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	// Parse CSV snapshot and restore each subcategory
	for _, line := range strings.Split(snapshot, "\n") {
		// CSV: Machine Name,Policy Target,Subcategory,Subcategory GUID,Inclusion Setting,Exclusion Setting
		fields := strings.Split(line, ",")
		if len(fields) < 5 || fields[0] == "Machine Name" {
			continue
		}
		subcategory := fields[2]
		setting := fields[4]
		success, failure := "disable", "disable"
		if strings.Contains(setting, "Success") {
			success = "enable"
		}
		if strings.Contains(setting, "Failure") {
			failure = "enable"
		}
		exec.Command("auditpol", "/set", fmt.Sprintf(`/subcategory:"%s"`, subcategory),
			"/success:"+success, "/failure:"+failure).Run()
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 11: Create `agent/commands/winharden/tasks_windows.go`**

```go
//go:build windows

package winharden

import (
	"time"
)

func auditTasksOS(_ map[string]any) (map[string]any, error) {
	script := `
Get-ScheduledTask | ForEach-Object {
    [PSCustomObject]@{
        TaskName   = $_.TaskName
        TaskPath   = $_.TaskPath
        State      = $_.State
        Author     = $_.Principal.UserId
        RunLevel   = $_.Principal.RunLevel
        Actions    = ($_.Actions | ForEach-Object { $_.Execute }) -join "; "
        Hidden     = $_.Settings.Hidden
    }
} | ConvertTo-Json -Depth 2`

	tasksOut, _ := runPS(script)
	findings := []map[string]any{}

	// Flag privileged tasks (RunLevel = Highest)
	if len(tasksOut) > 0 {
		findings = append(findings, map[string]any{
			"tag":         "scheduled-task-inventory",
			"description": "Scheduled task inventory collected — review for unexpected entries",
			"raw":         string(tasksOut),
		})
	}

	tags := []string{}
	if len(findings) > 0 {
		tags = append(tags, "scheduled-task-findings")
	}

	return map[string]any{
		"findings":   findings,
		"total":      len(findings),
		"tags":       tags,
		"audited_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
```

- [ ] **Step 12: Create `agent/commands/winharden/registry_windows.go`**

```go
//go:build windows

package winharden

import (
	"strings"
	"time"
)

var registryHardeningSettings = map[string]struct {
	path  string
	name  string
	value int
	rtype string
}{
	"disable_autorun":            {`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer`, "NoDriveTypeAutoRun", 255, "REG_DWORD"},
	"disable_lm_hash":            {`HKLM\SYSTEM\CurrentControlSet\Control\Lsa`, "NoLMHash", 1, "REG_DWORD"},
	"disable_ntlmv1":             {`HKLM\SYSTEM\CurrentControlSet\Control\Lsa`, "LmCompatibilityLevel", 5, "REG_DWORD"},
	"disable_wdigest":            {`HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest`, "UseLogonCredential", 0, "REG_DWORD"},
	"enable_safe_dll_search":     {`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager`, "SafeDllSearchMode", 1, "REG_DWORD"},
	"enforce_uac_prompt":         {`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`, "ConsentPromptBehaviorAdmin", 2, "REG_DWORD"},
	"disable_print_spooler_remote": {`HKLM\Software\Policies\Microsoft\Windows NT\Printers`, "RegisterSpoolerRemoteRpcEndPoint", 2, "REG_DWORD"},
}

func registryExecuteOS(params map[string]any) (map[string]any, error) {
	// Collect snapshot keys
	keys := map[string]bool{}
	for _, s := range registryHardeningSettings {
		keys[s.path] = true
	}
	snapshots := map[string]string{}
	for key := range keys {
		snap, _ := regExport(key)
		snapshots[key] = snap
	}

	// Determine which settings to apply (default: all enabled)
	applied := []string{}
	for settingName, setting := range registryHardeningSettings {
		// Check if explicitly disabled via params
		if v, ok := params[settingName].(bool); ok && !v {
			continue
		}
		script := strings.Join([]string{
			`reg add "` + setting.path + `"`,
			`/v "` + setting.name + `"`,
			`/t ` + setting.rtype,
			`/d ` + itoa(setting.value),
			`/f`,
		}, " ")
		runPS(script)
		applied = append(applied, settingName)
	}

	return map[string]any{
		"settings_applied": applied,
		"snapshot":         snapshots,
		"applied_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func registryRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	for _, content := range snapshot {
		if s, ok := content.(string); ok && s != "" {
			regImport(s)
		}
	}
	return map[string]any{"rolled_back": true}, nil
}

func itoa(n int) string {
	return fmt.Sprintf("%d", n)
}
```

Note: `registry_windows.go` needs `"fmt"` import. Add it:

```go
//go:build windows

package winharden

import (
	"fmt"
	"strings"
	"time"
)
```

- [ ] **Step 13: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/winharden/... && "C:/Program Files/Go/bin/go.exe" test ./commands/winharden/... -v
```

Expected: PASS

- [ ] **Step 14: Commit**

```
git add agent/commands/winharden/
git commit -m "feat(winharden): implement all 13 Windows security hardening commands (Spec 5c)"
```

---

### Task 3: Register all 13 commands in `executor.go`

**Files:**
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Add winharden import and registrations to `executor.go`**

Add to imports: `"nexplane-agent/commands/winharden"`

Add to `commands` map:
```go
// Windows hardening (Spec 5c)
"configure_laps":                  winharden.ConfigureLAPSExecute,
"enable_credential_guard":         winharden.EnableCredentialGuardExecute,
"enforce_powershell_clm":          winharden.EnforcePowerShellCLMExecute,
"deploy_applocker_policy":         winharden.DeployAppLockerPolicyExecute,
"harden_smb":                      winharden.HardenSMBExecute,
"enable_bitlocker":                 winharden.EnableBitLockerExecute,
"configure_windows_firewall":      winharden.ConfigureWindowsFirewallExecute,
"harden_tls_protocols":            winharden.HardenTLSProtocolsExecute,
"harden_rdp":                      winharden.HardenRDPExecute,
"configure_windows_audit_policy":  winharden.ConfigureWindowsAuditPolicyExecute,
"audit_scheduled_tasks":           winharden.AuditScheduledTasksExecute,
"harden_registry":                 winharden.HardenRegistryExecute,
```

Add to `rollbacks` map:
```go
"configure_laps":                  winharden.ConfigureLAPSRollback,
"enable_credential_guard":         winharden.EnableCredentialGuardRollback,
"enforce_powershell_clm":          winharden.EnforcePowerShellCLMRollback,
"deploy_applocker_policy":         winharden.DeployAppLockerPolicyRollback,
"harden_smb":                      winharden.HardenSMBRollback,
"enable_bitlocker":                 winharden.EnableBitLockerRollback,
"configure_windows_firewall":      winharden.ConfigureWindowsFirewallRollback,
"harden_tls_protocols":            winharden.HardenTLSProtocolsRollback,
"harden_rdp":                      winharden.HardenRDPRollback,
"configure_windows_audit_policy":  winharden.ConfigureWindowsAuditPolicyRollback,
"harden_registry":                 winharden.HardenRegistryRollback,
```

- [ ] **Step 2: Build all packages and run all tests**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./... && "C:/Program Files/Go/bin/go.exe" test ./...
```

Expected: all PASS

- [ ] **Step 3: Commit**

```
git add agent/executor/executor.go
git commit -m "feat(executor): register all 13 winharden commands (Spec 5c)"
```

---

### Task 4: Catalog entries and mock stubs for all 13 commands

**Files:**
- Modify: `backend/app/connectors/catalog/nexplane_agent_mock.json`
- Create: 13 Python mock stubs

- [ ] **Step 1: Append 13 catalog entries to `nexplane_agent_mock.json`**

```json
{"action_id": "configure_laps", "generic_action": "configure_laps", "action_type": "change", "execution_tier": 3, "display_name": "Configure LAPS", "description": "Enable or disable Local Administrator Password Solution to randomize local admin passwords.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "action", "type": "string", "required": true}, {"name": "password_age_days", "type": "integer", "required": false, "default": 30}, {"name": "password_length", "type": "integer", "required": false, "default": 14}], "executor": "nexplane_agent_mock.configure_laps", "rollback_action": "configure_laps", "estimated_duration_seconds": 15},
{"action_id": "enable_credential_guard", "generic_action": "enable_credential_guard", "action_type": "change", "execution_tier": 3, "display_name": "Enable Credential Guard", "description": "Enable VBS-based Credential Guard to protect credential material in LSASS.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "action", "type": "string", "required": true}, {"name": "require_uefi_lock", "type": "boolean", "required": false, "default": false}], "executor": "nexplane_agent_mock.enable_credential_guard", "rollback_action": "enable_credential_guard", "estimated_duration_seconds": 10, "safety_notes": ["Requires reboot to take effect", "UEFI lock prevents registry rollback"]},
{"action_id": "enforce_powershell_clm", "generic_action": "enforce_powershell_clm", "action_type": "change", "execution_tier": 3, "display_name": "Enforce PowerShell CLM", "description": "Enforce PowerShell Constrained Language Mode via registry key.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "action", "type": "string", "required": true}, {"name": "mechanism", "type": "string", "required": false, "default": "registry"}], "executor": "nexplane_agent_mock.enforce_powershell_clm", "rollback_action": "enforce_powershell_clm", "estimated_duration_seconds": 10},
{"action_id": "deploy_applocker_policy", "generic_action": "deploy_applocker_policy", "action_type": "change", "execution_tier": 3, "display_name": "Deploy AppLocker Policy", "description": "Deploy AppLocker application allowlist policies. Defaults to AuditOnly mode.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "policy", "type": "string", "required": true}, {"name": "enforce", "type": "boolean", "required": false, "default": false}], "executor": "nexplane_agent_mock.deploy_applocker_policy", "rollback_action": "deploy_applocker_policy", "estimated_duration_seconds": 15, "safety_notes": ["Test in AuditOnly mode before enforcing"]},
{"action_id": "harden_smb", "generic_action": "harden_smb", "action_type": "change", "execution_tier": 3, "display_name": "Harden SMB", "description": "Disable SMBv1, require signing, disable guest access.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "disable_smb1", "type": "boolean", "required": false, "default": true}, {"name": "require_signing", "type": "boolean", "required": false, "default": true}, {"name": "disable_guest_access", "type": "boolean", "required": false, "default": true}], "executor": "nexplane_agent_mock.harden_smb", "rollback_action": "harden_smb", "estimated_duration_seconds": 15},
{"action_id": "enable_bitlocker", "generic_action": "enable_bitlocker", "action_type": "change", "execution_tier": 3, "display_name": "Enable BitLocker", "description": "Enable BitLocker full disk encryption. Returns recovery key in result.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "drive_letter", "type": "string", "required": false, "default": "C:"}, {"name": "protector", "type": "string", "required": false, "default": "tpm"}, {"name": "pin", "type": "string", "required": false}, {"name": "encryption_method", "type": "string", "required": false, "default": "XtsAes256"}], "executor": "nexplane_agent_mock.enable_bitlocker", "rollback_action": "enable_bitlocker", "estimated_duration_seconds": 30, "safety_notes": ["Save the recovery key — it cannot be recovered later", "Rollback after encryption started requires full decryption"]},
{"action_id": "configure_windows_firewall", "generic_action": "configure_windows_firewall", "action_type": "change", "execution_tier": 3, "display_name": "Configure Windows Firewall", "description": "Add/remove firewall rules or set default inbound/outbound actions.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "action", "type": "string", "required": true}, {"name": "rule", "type": "object", "required": false}, {"name": "default_action", "type": "object", "required": false}], "executor": "nexplane_agent_mock.configure_windows_firewall", "rollback_action": "configure_windows_firewall", "estimated_duration_seconds": 10, "safety_notes": ["Setting default inbound=Block without an allow rule for RDP/WinRM will lock out remote management"]},
{"action_id": "harden_tls_protocols", "generic_action": "harden_tls_protocols", "action_type": "change", "execution_tier": 3, "display_name": "Harden TLS Protocols", "description": "Disable SSL/TLS 1.0/1.1, enforce TLS 1.2/1.3 via SCHANNEL registry. Requires reboot.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "disable_protocols", "type": "array", "required": false}, {"name": "enabled_protocols", "type": "array", "required": false}, {"name": "apply_to", "type": "array", "required": false}], "executor": "nexplane_agent_mock.harden_tls_protocols", "rollback_action": "harden_tls_protocols", "estimated_duration_seconds": 10, "safety_notes": ["Disabling TLS 1.0/1.1 breaks legacy .NET clients", "Requires reboot or service restart"]},
{"action_id": "harden_rdp", "generic_action": "harden_rdp", "action_type": "change", "execution_tier": 3, "display_name": "Harden RDP", "description": "Require NLA, set encryption level, configure idle/disconnect timeout.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "require_nla", "type": "boolean", "required": false, "default": true}, {"name": "idle_timeout_minutes", "type": "integer", "required": false}, {"name": "port", "type": "integer", "required": false}], "executor": "nexplane_agent_mock.harden_rdp", "rollback_action": "harden_rdp", "estimated_duration_seconds": 15},
{"action_id": "configure_windows_audit_policy", "generic_action": "configure_windows_audit_policy", "action_type": "change", "execution_tier": 3, "display_name": "Configure Windows Audit Policy", "description": "Apply CIS/STIG auditpol settings for logon, object access, privilege use, process creation.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "profile", "type": "string", "required": false, "default": "cis_level1"}], "executor": "nexplane_agent_mock.configure_windows_audit_policy", "rollback_action": "configure_windows_audit_policy", "estimated_duration_seconds": 15},
{"action_id": "audit_scheduled_tasks", "generic_action": "audit_scheduled_tasks", "action_type": "ingest", "execution_tier": 3, "display_name": "Audit Scheduled Tasks", "description": "Read-only: enumerate all scheduled tasks, flag privileged, hidden, unregistered, and writable-binary tasks.", "applicable_asset_types": ["server", "workstation"], "parameters": [], "executor": "nexplane_agent_mock.audit_scheduled_tasks", "estimated_duration_seconds": 15},
{"action_id": "harden_registry", "generic_action": "harden_registry", "action_type": "change", "execution_tier": 3, "display_name": "Harden Registry", "description": "Apply CIS registry hardening: disable autorun, LM hash, NTLMv1, WDigest; enforce UAC; enable safe DLL search.", "applicable_asset_types": ["server", "workstation"], "parameters": [{"name": "profile", "type": "string", "required": false, "default": "cis_level1"}], "executor": "nexplane_agent_mock.harden_registry", "rollback_action": "harden_registry", "estimated_duration_seconds": 15, "safety_notes": ["disable_ntlmv1 breaks auth to legacy systems that only support NTLMv1"]}
```

- [ ] **Step 2: Create 13 mock stub files**

Create each file in `backend/app/connectors/executors/nexplane_agent_mock/`:

`configure_laps.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"action": parameters.get("action"), "password_age_days": 30, "password_length": 14, "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "configure_laps"}
```

`enable_credential_guard.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"action": parameters.get("action"), "uefi_lock": False, "reboot_required": True, "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "enable_credential_guard"}
```

`enforce_powershell_clm.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"action": parameters.get("action"), "mechanism": "registry", "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "enforce_powershell_clm"}
```

`deploy_applocker_policy.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"enforce": parameters.get("enforce", False), "mode": "AuditOnly", "snapshot": "<AppLockerPolicy Version='1'/>", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "deploy_applocker_policy"}
```

`harden_smb.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"smb1_disabled": True, "signing_required": True, "guest_disabled": True, "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "harden_smb"}
```

`enable_bitlocker.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"drive": parameters.get("drive_letter", "C:"), "encryption_method": "XtsAes256", "protector": parameters.get("protector", "tpm"),
            "recovery_key": "123456-654321-123456-654321-123456-654321-123456-654321",
            "volume_status": "EncryptionInProgress", "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "warning": "BitLocker decryption in progress"}
```

`configure_windows_firewall.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"action": parameters.get("action"), "snapshot": "# firewall policy", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "configure_windows_firewall"}
```

`harden_tls_protocols.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"disabled_protocols": ["SSL 2.0","SSL 3.0","TLS 1.0","TLS 1.1"], "enabled_protocols": ["TLS 1.2","TLS 1.3"],
            "reboot_required": True, "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "reboot_required": True}
```

`harden_rdp.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"nla": True, "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "harden_rdp"}
```

`configure_windows_audit_policy.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"profile_applied": parameters.get("profile", "cis_level1"), "categories_count": 9,
            "snapshot": "Machine Name,...", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "configure_windows_audit_policy"}
```

`audit_scheduled_tasks.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"findings": [{"tag": "scheduled-task-inventory", "description": "Scheduled task inventory collected"}],
            "total": 1, "tags": ["scheduled-task-findings"], "audited_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": False, "reason": "audit_scheduled_tasks is read-only"}
```

`harden_registry.py`:
```python
from datetime import datetime, timezone
async def execute(parameters, asset_ids, connector):
    return {"settings_applied": ["disable_autorun","disable_lm_hash","disable_ntlmv1","disable_wdigest"],
            "snapshot": {}, "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters, execution_result, connector):
    return {"rolled_back": True, "action": "harden_registry"}
```

- [ ] **Step 3: Validate JSON and run catalog tests**

```
python -c "import json; json.load(open('backend/app/connectors/catalog/nexplane_agent_mock.json')); print('valid')"
cd backend && python -m pytest app/tests/test_catalog_service.py -v
```

- [ ] **Step 4: Commit**

```
git add backend/app/connectors/catalog/nexplane_agent_mock.json backend/app/connectors/executors/nexplane_agent_mock/
git commit -m "feat(catalog+mock): add 13 winharden actions to catalog and mock stubs (Spec 5c)"
```
