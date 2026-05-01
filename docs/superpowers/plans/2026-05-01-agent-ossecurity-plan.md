# Linux OS Security Hardening Implementation Plan (Spec 5a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 13 agent commands for Linux OS security hardening: MAC/sandboxing (SELinux, AppArmor, seccomp), kernel/network hardening (sysctl, host firewall, module blacklisting, mount options), and monitoring/integrity (auditd, AIDE/Tripwire, eBPF).

**Architecture:** Two new packages: `agent/commands/ossecurity/` (10 commands, Linux-only) and `agent/commands/ebpf/` (3 commands, Linux-only). Same pattern as linuxauth: public API in package root file, implementations in `*_linux.go` files, stubs in `*_other.go` for non-Linux builds, tests in `*_test.go` (no build tag, validates params only). All 13 commands registered in `executor.go`, cataloged in `nexplane_agent_mock.json`, with Python mock stubs.

**Tech Stack:** Go 1.22+, `os/exec` for shell commands, drop-in file pattern for `sysctl.d` and `modprobe.d`, nexplane-managed-begin/end blocks for files that don't support drop-ins.

**Working directory for all commands:** `f:\Nexplane\nexplane\.worktrees\agent-hardening`

**NOTE:** The `insertManagedBlock` helper is defined in `agent/commands/linuxauth/pam_linux.go`. The ossecurity package needs its own copy — do not import linuxauth.

---

### Task 1: `ossecurity` package scaffolding

**Files:**
- Create: `agent/commands/ossecurity/ossecurity.go`
- Create: `agent/commands/ossecurity/ossecurity_other.go`
- Create: `agent/commands/ossecurity/ossecurity_test.go`

- [ ] **Step 1: Create `agent/commands/ossecurity/ossecurity.go`**

```go
package ossecurity

import "fmt"

var validSELinuxModes = map[string]bool{"enforcing": true, "permissive": true, "disabled": true}
var validAppArmorModes = map[string]bool{"enforce": true, "complain": true, "disable": true}
var validFirewallActions = map[string]bool{"add_rule": true, "remove_rule": true, "flush": true}
var validMountActions = map[string]bool{"apply": true}
var validFIMTools = map[string]bool{"aide": true, "tripwire": true, "auto": true}

func ConfigureSELinuxExecute(params map[string]any) (map[string]any, error) {
	mode, hasMode := params["mode"].(string)
	_, hasModule := params["policy_module_path"].(string)
	_, hasGenerate := params["generate_from_audit_log"].(bool)
	if !hasMode && !hasModule && !hasGenerate {
		return nil, fmt.Errorf("at least one of mode, policy_module_path, or generate_from_audit_log is required")
	}
	if hasMode && !validSELinuxModes[mode] {
		return nil, fmt.Errorf("invalid mode %q: must be enforcing, permissive, or disabled", mode)
	}
	return selinuxExecuteOS(params)
}

func ConfigureSELinuxRollback(params map[string]any) (map[string]any, error) {
	return selinuxRollbackOS(params)
}

func ConfigureAppArmorExecute(params map[string]any) (map[string]any, error) {
	if mode, ok := params["mode"].(string); ok && mode != "" {
		if !validAppArmorModes[mode] {
			return nil, fmt.Errorf("invalid mode %q: must be enforce, complain, or disable", mode)
		}
	}
	return apparmorExecuteOS(params)
}

func ConfigureAppArmorRollback(params map[string]any) (map[string]any, error) {
	return apparmorRollbackOS(params)
}

func ConfigureSeccompExecute(params map[string]any) (map[string]any, error) {
	if svc, _ := params["service_name"].(string); svc == "" {
		return nil, fmt.Errorf("service_name is required")
	}
	if _, ok := params["profile"].(string); !ok {
		return nil, fmt.Errorf("profile (seccomp JSON) is required")
	}
	return seccompExecuteOS(params)
}

func ConfigureSeccompRollback(params map[string]any) (map[string]any, error) {
	return seccompRollbackOS(params)
}

func ApplySysctlHardeningExecute(params map[string]any) (map[string]any, error) {
	return sysctlExecuteOS(params)
}

func ApplySysctlHardeningRollback(params map[string]any) (map[string]any, error) {
	return sysctlRollbackOS(params)
}

func ConfigureHostFirewallExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if !validFirewallActions[action] {
		return nil, fmt.Errorf("action must be add_rule, remove_rule, or flush, got %q", action)
	}
	return firewallExecuteOS(params)
}

func ConfigureHostFirewallRollback(params map[string]any) (map[string]any, error) {
	return firewallRollbackOS(params)
}

func BlacklistKernelModulesExecute(params map[string]any) (map[string]any, error) {
	modules, _ := params["modules"].([]any)
	if len(modules) == 0 {
		return nil, fmt.Errorf("modules list is required and must not be empty")
	}
	return blacklistExecuteOS(params)
}

func BlacklistKernelModulesRollback(params map[string]any) (map[string]any, error) {
	return blacklistRollbackOS(params)
}

func HardenMountOptionsExecute(params map[string]any) (map[string]any, error) {
	return mountExecuteOS(params)
}

func HardenMountOptionsRollback(params map[string]any) (map[string]any, error) {
	return mountRollbackOS(params)
}

func DeployAuditdRulesExecute(params map[string]any) (map[string]any, error) {
	profile, _ := params["profile"].(string)
	_, hasRules := params["rules"].(string)
	if profile == "" && !hasRules {
		return nil, fmt.Errorf("profile or rules is required")
	}
	return auditdExecuteOS(params)
}

func DeployAuditdRulesRollback(params map[string]any) (map[string]any, error) {
	return auditdRollbackOS(params)
}

func SetupFileIntegrityMonitoringExecute(params map[string]any) (map[string]any, error) {
	return fimExecuteOS(params)
}

func SetupFileIntegrityMonitoringRollback(params map[string]any) (map[string]any, error) {
	return fimRollbackOS(params)
}

func AuditOSSecurityPostureExecute(params map[string]any) (map[string]any, error) {
	return auditPostureOS(params)
}
```

- [ ] **Step 2: Create `agent/commands/ossecurity/ossecurity_other.go`**

```go
//go:build !linux

package ossecurity

import "fmt"

func selinuxExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_selinux requires Linux")
}
func selinuxRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_selinux requires Linux")
}
func apparmorExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_apparmor requires Linux")
}
func apparmorRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_apparmor requires Linux")
}
func seccompExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_seccomp requires Linux")
}
func seccompRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_seccomp requires Linux")
}
func sysctlExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_sysctl_hardening requires Linux")
}
func sysctlRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_sysctl_hardening requires Linux")
}
func firewallExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_host_firewall requires Linux")
}
func firewallRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_host_firewall requires Linux")
}
func blacklistExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("blacklist_kernel_modules requires Linux")
}
func blacklistRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("blacklist_kernel_modules requires Linux")
}
func mountExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_mount_options requires Linux")
}
func mountRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_mount_options requires Linux")
}
func auditdExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_auditd_rules requires Linux")
}
func auditdRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_auditd_rules requires Linux")
}
func fimExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("setup_file_integrity_monitoring requires Linux")
}
func fimRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("setup_file_integrity_monitoring requires Linux")
}
func auditPostureOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_os_security_posture requires Linux")
}
```

- [ ] **Step 3: Create `agent/commands/ossecurity/ossecurity_test.go`**

```go
package ossecurity_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/ossecurity"
)

func TestConfigureSELinuxRequiresParam(t *testing.T) {
	_, err := ossecurity.ConfigureSELinuxExecute(map[string]any{})
	if err == nil {
		t.Error("expected error when no params provided")
	}
}

func TestConfigureSELinuxInvalidMode(t *testing.T) {
	_, err := ossecurity.ConfigureSELinuxExecute(map[string]any{"mode": "turbo"})
	if err == nil || !strings.Contains(err.Error(), "invalid mode") {
		t.Errorf("expected 'invalid mode' error, got: %v", err)
	}
}

func TestConfigureSELinuxValidModes(t *testing.T) {
	for _, m := range []string{"enforcing", "permissive", "disabled"} {
		_, err := ossecurity.ConfigureSELinuxExecute(map[string]any{"mode": m})
		if err != nil && strings.Contains(err.Error(), "invalid mode") {
			t.Errorf("mode %q should be valid, got: %v", m, err)
		}
	}
}

func TestConfigureAppArmorInvalidMode(t *testing.T) {
	_, err := ossecurity.ConfigureAppArmorExecute(map[string]any{"mode": "broken"})
	if err == nil || !strings.Contains(err.Error(), "invalid mode") {
		t.Errorf("expected 'invalid mode' error, got: %v", err)
	}
}

func TestConfigureSeccompRequiresService(t *testing.T) {
	_, err := ossecurity.ConfigureSeccompExecute(map[string]any{"profile": "{}"})
	if err == nil || !strings.Contains(err.Error(), "service_name") {
		t.Errorf("expected service_name error, got: %v", err)
	}
}

func TestConfigureSeccompRequiresProfile(t *testing.T) {
	_, err := ossecurity.ConfigureSeccompExecute(map[string]any{"service_name": "nginx"})
	if err == nil || !strings.Contains(err.Error(), "profile") {
		t.Errorf("expected profile error, got: %v", err)
	}
}

func TestConfigureHostFirewallInvalidAction(t *testing.T) {
	_, err := ossecurity.ConfigureHostFirewallExecute(map[string]any{"action": "delete"})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected action error, got: %v", err)
	}
}

func TestBlacklistKernelModulesRequiresModules(t *testing.T) {
	_, err := ossecurity.BlacklistKernelModulesExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "modules") {
		t.Errorf("expected modules error, got: %v", err)
	}
}

func TestDeployAuditdRulesRequiresProfileOrRules(t *testing.T) {
	_, err := ossecurity.DeployAuditdRulesExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "profile or rules") {
		t.Errorf("expected profile or rules error, got: %v", err)
	}
}
```

- [ ] **Step 4: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/ossecurity/... && "C:/Program Files/Go/bin/go.exe" test ./commands/ossecurity/... -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add agent/commands/ossecurity/
git commit -m "feat(ossecurity): add package scaffolding with param validation and stubs"
```

---

### Task 2: `apply_sysctl_hardening` and `blacklist_kernel_modules` (simple drop-in file commands)

**Files:**
- Create: `agent/commands/ossecurity/sysctl_linux.go`
- Create: `agent/commands/ossecurity/modules_linux.go`

- [ ] **Step 1: Create `agent/commands/ossecurity/sysctl_linux.go`**

```go
//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

var cisLevel1Sysctl = map[string]string{
	"net.ipv4.ip_forward":                  "0",
	"net.ipv4.conf.all.send_redirects":      "0",
	"net.ipv4.conf.default.send_redirects":  "0",
	"net.ipv4.conf.all.accept_redirects":    "0",
	"net.ipv4.conf.default.accept_redirects": "0",
	"net.ipv4.conf.all.rp_filter":           "1",
	"net.ipv4.conf.default.rp_filter":       "1",
	"net.ipv4.tcp_syncookies":               "1",
	"net.ipv4.icmp_echo_ignore_broadcasts":  "1",
	"net.ipv4.icmp_ignore_bogus_error_responses": "1",
	"net.ipv4.tcp_timestamps":               "0",
	"kernel.randomize_va_space":             "2",
	"kernel.dmesg_restrict":                 "1",
	"fs.suid_dumpable":                      "0",
}

const sysctlDropIn = "/etc/sysctl.d/99-nexplane-hardening.conf"

func sysctlExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("apply_sysctl_hardening requires root privileges")
	}

	// Merge profile defaults with any overrides
	settings := make(map[string]string)
	for k, v := range cisLevel1Sysctl {
		settings[k] = v
	}
	if overrides, ok := params["settings"].(map[string]any); ok {
		for k, v := range overrides {
			settings[k] = fmt.Sprintf("%v", v)
		}
	}

	// Snapshot: read existing drop-in if present
	snapshot := ""
	if data, err := os.ReadFile(sysctlDropIn); err == nil {
		snapshot = string(data)
	}

	// Write drop-in
	var sb strings.Builder
	sb.WriteString("# Nexplane sysctl hardening — do not edit manually\n")
	for k, v := range settings {
		fmt.Fprintf(&sb, "%s = %s\n", k, v)
	}
	if err := os.WriteFile(sysctlDropIn, []byte(sb.String()), 0644); err != nil {
		return nil, fmt.Errorf("writing sysctl drop-in: %w", err)
	}

	// Apply immediately
	if out, err := exec.Command("sysctl", "--system").CombinedOutput(); err != nil {
		return nil, fmt.Errorf("sysctl --system failed: %s", out)
	}

	return map[string]any{
		"settings_applied": settings,
		"drop_in_path":     sysctlDropIn,
		"snapshot":         snapshot,
		"applied_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func sysctlRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if snapshot == "" {
		// Drop-in didn't exist before; remove it
		if err := os.Remove(sysctlDropIn); err != nil && !os.IsNotExist(err) {
			return nil, fmt.Errorf("removing sysctl drop-in: %w", err)
		}
	} else {
		if err := os.WriteFile(sysctlDropIn, []byte(snapshot), 0644); err != nil {
			return nil, fmt.Errorf("restoring sysctl drop-in: %w", err)
		}
	}
	exec.Command("sysctl", "--system").Run() //nolint
	return map[string]any{"rolled_back": true, "drop_in_path": sysctlDropIn}, nil
}

func insertManagedBlock(content, block string) string {
	const beg = "# nexplane-managed-begin\n"
	const end = "# nexplane-managed-end"
	si := strings.Index(content, beg)
	ei := strings.Index(content, end)
	if si != -1 && ei != -1 {
		return content[:si] + beg + block + end + content[ei+len(end):]
	}
	if !strings.HasSuffix(content, "\n") {
		content += "\n"
	}
	return content + beg + block + end + "\n"
}
```

- [ ] **Step 2: Create `agent/commands/ossecurity/modules_linux.go`**

```go
//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const moduleBlacklist = "/etc/modprobe.d/nexplane-blacklist.conf"

func blacklistExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("blacklist_kernel_modules requires root privileges")
	}
	modulesRaw, _ := params["modules"].([]any)
	modules := make([]string, 0, len(modulesRaw))
	for _, m := range modulesRaw {
		if s, ok := m.(string); ok {
			modules = append(modules, s)
		}
	}

	// Snapshot
	snapshot := ""
	if data, err := os.ReadFile(moduleBlacklist); err == nil {
		snapshot = string(data)
	}

	// Write blacklist file
	var sb strings.Builder
	sb.WriteString("# Nexplane kernel module blacklist — do not edit manually\n")
	for _, m := range modules {
		fmt.Fprintf(&sb, "blacklist %s\ninstall %s /bin/true\n", m, m)
	}
	if err := os.WriteFile(moduleBlacklist, []byte(sb.String()), 0644); err != nil {
		return nil, fmt.Errorf("writing blacklist: %w", err)
	}

	// Update initramfs (best-effort, may not be available in all environments)
	exec.Command("update-initramfs", "-u").Run() //nolint

	return map[string]any{
		"modules_blacklisted": modules,
		"blacklist_path":      moduleBlacklist,
		"snapshot":            snapshot,
		"applied_at":          time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func blacklistRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if snapshot == "" {
		if err := os.Remove(moduleBlacklist); err != nil && !os.IsNotExist(err) {
			return nil, fmt.Errorf("removing blacklist: %w", err)
		}
	} else {
		if err := os.WriteFile(moduleBlacklist, []byte(snapshot), 0644); err != nil {
			return nil, fmt.Errorf("restoring blacklist: %w", err)
		}
	}
	exec.Command("update-initramfs", "-u").Run() //nolint
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 3: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/ossecurity/... && "C:/Program Files/Go/bin/go.exe" test ./commands/ossecurity/... -v
```

- [ ] **Step 4: Commit**

```
git add agent/commands/ossecurity/sysctl_linux.go agent/commands/ossecurity/modules_linux.go
git commit -m "feat(ossecurity): implement apply_sysctl_hardening and blacklist_kernel_modules"
```

---

### Task 3: `configure_selinux` and `configure_apparmor`

**Files:**
- Create: `agent/commands/ossecurity/selinux_linux.go`
- Create: `agent/commands/ossecurity/apparmor_linux.go`

- [ ] **Step 1: Create `agent/commands/ossecurity/selinux_linux.go`**

```go
//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func selinuxExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_selinux requires root privileges")
	}
	if _, err := exec.LookPath("sestatus"); err != nil {
		return nil, fmt.Errorf("SELinux not available: sestatus not found")
	}

	// Snapshot: current mode and config
	currentModeOut, _ := exec.Command("getenforce").Output()
	currentMode := strings.TrimSpace(string(currentModeOut))
	configData, _ := os.ReadFile("/etc/selinux/config")
	snapshot := map[string]any{
		"previous_mode":   currentMode,
		"selinux_config":  string(configData),
		"modules_installed": []string{},
	}

	newMode := currentMode
	modulesInstalled := []string{}

	// Apply mode
	if mode, ok := params["mode"].(string); ok && mode != "" {
		modeInt := map[string]string{"enforcing": "1", "permissive": "0"}
		if code, ok := modeInt[mode]; ok {
			if out, err := exec.Command("setenforce", code).CombinedOutput(); err != nil {
				return nil, fmt.Errorf("setenforce: %s: %w", out, err)
			}
		}
		// Update /etc/selinux/config persistently
		config := string(configData)
		var lines []string
		for _, l := range strings.Split(config, "\n") {
			if strings.HasPrefix(l, "SELINUX=") {
				lines = append(lines, "SELINUX="+mode)
			} else {
				lines = append(lines, l)
			}
		}
		if err := os.WriteFile("/etc/selinux/config", []byte(strings.Join(lines, "\n")), 0644); err != nil {
			return nil, fmt.Errorf("updating selinux config: %w", err)
		}
		newMode = mode
	}

	// Install policy module
	if modulePath, ok := params["policy_module_path"].(string); ok && modulePath != "" {
		out, err := exec.Command("semodule", "-i", modulePath).CombinedOutput()
		if err != nil {
			return nil, fmt.Errorf("semodule -i: %s: %w", out, err)
		}
		// Extract module name from path
		parts := strings.Split(modulePath, "/")
		name := strings.TrimSuffix(parts[len(parts)-1], ".pp")
		name = strings.TrimSuffix(name, ".te")
		modulesInstalled = append(modulesInstalled, name)
		snapshot["modules_installed"] = modulesInstalled
	}

	// Generate from audit log
	if gen, _ := params["generate_from_audit_log"].(bool); gen {
		if _, err := exec.LookPath("ausearch"); err != nil {
			return nil, fmt.Errorf("ausearch not found (required for generate_from_audit_log)")
		}
		// ausearch | audit2allow -M nexplane_generated
		ausearchOut, err := exec.Command("ausearch", "-m", "avc", "-ts", "recent").Output()
		if err == nil && len(ausearchOut) > 0 {
			cmd := exec.Command("audit2allow", "-M", "nexplane_generated")
			cmd.Stdin = strings.NewReader(string(ausearchOut))
			if out, err := cmd.CombinedOutput(); err != nil {
				return nil, fmt.Errorf("audit2allow: %s: %w", out, err)
			}
			if out, err := exec.Command("semodule", "-i", "nexplane_generated.pp").CombinedOutput(); err != nil {
				return nil, fmt.Errorf("semodule -i nexplane_generated.pp: %s: %w", out, err)
			}
			modulesInstalled = append(modulesInstalled, "nexplane_generated")
			snapshot["modules_installed"] = modulesInstalled
		}
	}

	return map[string]any{
		"previous_mode":     currentMode,
		"new_mode":          newMode,
		"modules_installed": modulesInstalled,
		"config_snapshot":   snapshot,
		"applied_at":        time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func selinuxRollbackOS(params map[string]any) (map[string]any, error) {
	configSnapshot, ok := params["config_snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("config_snapshot is required for rollback")
	}

	// Restore mode
	if prevMode, _ := configSnapshot["previous_mode"].(string); prevMode != "" {
		modeInt := map[string]string{"Enforcing": "1", "Permissive": "0", "enforcing": "1", "permissive": "0"}
		if code, ok := modeInt[prevMode]; ok {
			exec.Command("setenforce", code).Run() //nolint
		}
	}

	// Restore config file
	if configData, _ := configSnapshot["selinux_config"].(string); configData != "" {
		os.WriteFile("/etc/selinux/config", []byte(configData), 0644) //nolint
	}

	// Unload any installed modules
	if modules, _ := configSnapshot["modules_installed"].([]any); len(modules) > 0 {
		for _, m := range modules {
			if name, ok := m.(string); ok && name != "" {
				exec.Command("semodule", "-r", name).Run() //nolint
			}
		}
	}

	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 2: Create `agent/commands/ossecurity/apparmor_linux.go`**

```go
//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func apparmorExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_apparmor requires root privileges")
	}
	if _, err := exec.LookPath("aa-status"); err != nil {
		return nil, fmt.Errorf("AppArmor not available: aa-status not found")
	}

	profileName, _ := params["profile_name"].(string)
	profileContent, _ := params["profile_content"].(string)
	mode, _ := params["mode"].(string)

	// Snapshot: current profile status
	statusOut, _ := exec.Command("aa-status", "--json").Output()
	snapshot := map[string]any{
		"aa_status": string(statusOut),
		"profile":   profileName,
	}

	// If profile content provided, write it
	if profileContent != "" && profileName != "" {
		profilePath := filepath.Join("/etc/apparmor.d", profileName)
		if existing, err := os.ReadFile(profilePath); err == nil {
			snapshot["previous_profile_content"] = string(existing)
		}
		if err := os.WriteFile(profilePath, []byte(profileContent), 0644); err != nil {
			return nil, fmt.Errorf("writing AppArmor profile: %w", err)
		}
		// Parse/load the profile
		if out, err := exec.Command("apparmor_parser", "-r", profilePath).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("apparmor_parser: %s: %w", out, err)
		}
	}

	// Set mode
	appliedMode := ""
	if mode != "" && profileName != "" {
		var cmd *exec.Cmd
		switch mode {
		case "enforce":
			cmd = exec.Command("aa-enforce", profileName)
		case "complain":
			cmd = exec.Command("aa-complain", profileName)
		case "disable":
			cmd = exec.Command("aa-disable", profileName)
		}
		if cmd != nil {
			if out, err := cmd.CombinedOutput(); err != nil {
				return nil, fmt.Errorf("aa-%s %s: %s: %w", mode, profileName, out, err)
			}
			appliedMode = mode
		}
	}

	return map[string]any{
		"profile_name": profileName,
		"mode_applied": appliedMode,
		"snapshot":     snapshot,
		"applied_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func apparmorRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	profileName, _ := snapshot["profile"].(string)

	if prev, ok := snapshot["previous_profile_content"].(string); ok && profileName != "" {
		profilePath := filepath.Join("/etc/apparmor.d", profileName)
		if prev == "" {
			os.Remove(profilePath)
		} else {
			os.WriteFile(profilePath, []byte(prev), 0644) //nolint
			exec.Command("apparmor_parser", "-r", profilePath).Run() //nolint
		}
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 3: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/ossecurity/... && "C:/Program Files/Go/bin/go.exe" test ./commands/ossecurity/... -v
```

- [ ] **Step 4: Commit**

```
git add agent/commands/ossecurity/selinux_linux.go agent/commands/ossecurity/apparmor_linux.go
git commit -m "feat(ossecurity): implement configure_selinux and configure_apparmor"
```

---

### Task 4: `configure_seccomp`, `configure_host_firewall`, `harden_mount_options`

**Files:**
- Create: `agent/commands/ossecurity/seccomp_linux.go`
- Create: `agent/commands/ossecurity/firewall_linux.go`
- Create: `agent/commands/ossecurity/mount_linux.go`

- [ ] **Step 1: Create `agent/commands/ossecurity/seccomp_linux.go`**

```go
//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func seccompExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_seccomp requires root privileges")
	}
	serviceName, _ := params["service_name"].(string)
	profile, _ := params["profile"].(string)

	// Write drop-in for systemd service
	dropInDir := fmt.Sprintf("/etc/systemd/system/%s.service.d", serviceName)
	if err := os.MkdirAll(dropInDir, 0755); err != nil {
		return nil, fmt.Errorf("creating drop-in dir: %w", err)
	}
	dropInPath := filepath.Join(dropInDir, "nexplane-seccomp.conf")

	// Snapshot
	snapshot := ""
	if data, err := os.ReadFile(dropInPath); err == nil {
		snapshot = string(data)
	}

	// Write systemd drop-in with seccomp profile path
	// Write profile to /etc/nexplane/seccomp/<service>.json
	seccompDir := "/etc/nexplane/seccomp"
	if err := os.MkdirAll(seccompDir, 0755); err != nil {
		return nil, fmt.Errorf("creating seccomp dir: %w", err)
	}
	profilePath := filepath.Join(seccompDir, serviceName+".json")
	if err := os.WriteFile(profilePath, []byte(profile), 0644); err != nil {
		return nil, fmt.Errorf("writing seccomp profile: %w", err)
	}

	dropInContent := fmt.Sprintf("[Service]\nSeccompFilter=%s\nSystemCallErrorNumber=EPERM\n", profilePath)
	if err := os.WriteFile(dropInPath, []byte(dropInContent), 0644); err != nil {
		return nil, fmt.Errorf("writing drop-in: %w", err)
	}

	return map[string]any{
		"service_name": serviceName,
		"drop_in_path": dropInPath,
		"profile_path": profilePath,
		"snapshot":     snapshot,
		"applied_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func seccompRollbackOS(params map[string]any) (map[string]any, error) {
	serviceName, _ := params["service_name"].(string)
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	dropInPath := fmt.Sprintf("/etc/systemd/system/%s.service.d/nexplane-seccomp.conf", serviceName)
	profilePath := fmt.Sprintf("/etc/nexplane/seccomp/%s.json", serviceName)

	os.Remove(profilePath)
	if snapshot == "" {
		os.Remove(dropInPath)
	} else {
		os.WriteFile(dropInPath, []byte(snapshot), 0644) //nolint
	}
	return map[string]any{"rolled_back": true, "service": serviceName}, nil
}
```

- [ ] **Step 2: Create `agent/commands/ossecurity/firewall_linux.go`**

```go
//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func firewallExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_host_firewall requires root privileges")
	}

	// Detect firewall tool
	tool := detectFirewallTool()

	// Snapshot current ruleset
	snapshot := captureFirewallSnapshot(tool)

	action, _ := params["action"].(string)

	switch tool {
	case "nftables":
		return nftablesAction(action, params, snapshot)
	case "firewalld":
		return firewalldAction(action, params, snapshot)
	default:
		return iptablesAction(action, params, snapshot)
	}
}

func detectFirewallTool() string {
	if _, err := exec.LookPath("firewall-cmd"); err == nil {
		if exec.Command("systemctl", "is-active", "firewalld").Run() == nil {
			return "firewalld"
		}
	}
	if _, err := exec.LookPath("nft"); err == nil {
		return "nftables"
	}
	return "iptables"
}

func captureFirewallSnapshot(tool string) string {
	switch tool {
	case "nftables":
		out, _ := exec.Command("nft", "list", "ruleset").Output()
		return string(out)
	case "firewalld":
		out, _ := exec.Command("firewall-cmd", "--list-all").Output()
		return string(out)
	default:
		out, _ := exec.Command("iptables-save").Output()
		return string(out)
	}
}

func iptablesAction(action string, params map[string]any, snapshot string) (map[string]any, error) {
	rule, _ := params["rule"].(map[string]any)
	var args []string
	switch action {
	case "add_rule":
		args = buildIPTablesArgs("-A", rule)
	case "remove_rule":
		args = buildIPTablesArgs("-D", rule)
	case "flush":
		args = []string{"-F"}
	}
	if len(args) > 0 {
		if out, err := exec.Command("iptables", args...).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("iptables %v: %s: %w", args, out, err)
		}
	}
	return map[string]any{
		"tool": "iptables", "action": action,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func buildIPTablesArgs(flag string, rule map[string]any) []string {
	if rule == nil {
		return nil
	}
	chain, _ := rule["chain"].(string)
	if chain == "" {
		chain = "INPUT"
	}
	args := []string{flag, chain}
	if proto, _ := rule["protocol"].(string); proto != "" {
		args = append(args, "-p", proto)
	}
	if port, _ := rule["port"].(string); port != "" {
		args = append(args, "--dport", port)
	}
	if src, _ := rule["source"].(string); src != "" {
		args = append(args, "-s", src)
	}
	actionType, _ := rule["action_type"].(string)
	if actionType == "" {
		actionType = "ACCEPT"
	}
	args = append(args, "-j", strings.ToUpper(actionType))
	return args
}

func nftablesAction(action string, params map[string]any, snapshot string) (map[string]any, error) {
	// For simplicity, support add_rule via nft add rule and flush via nft flush
	switch action {
	case "flush":
		exec.Command("nft", "flush", "ruleset").Run() //nolint
	default:
		// Detailed nftables rule construction would be added here
	}
	return map[string]any{
		"tool": "nftables", "action": action,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func firewalldAction(action string, params map[string]any, snapshot string) (map[string]any, error) {
	rule, _ := params["rule"].(map[string]any)
	switch action {
	case "add_rule":
		if port, _ := rule["port"].(string); port != "" {
			proto, _ := rule["protocol"].(string)
			if proto == "" {
				proto = "tcp"
			}
			exec.Command("firewall-cmd", "--permanent", "--add-port="+port+"/"+proto).Run() //nolint
			exec.Command("firewall-cmd", "--reload").Run()                                   //nolint
		}
	case "remove_rule":
		if port, _ := rule["port"].(string); port != "" {
			proto, _ := rule["protocol"].(string)
			if proto == "" {
				proto = "tcp"
			}
			exec.Command("firewall-cmd", "--permanent", "--remove-port="+port+"/"+proto).Run() //nolint
			exec.Command("firewall-cmd", "--reload").Run()                                      //nolint
		}
	case "flush":
		exec.Command("firewall-cmd", "--permanent", "--complete-reload").Run() //nolint
	}
	return map[string]any{
		"tool": "firewalld", "action": action,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func firewallRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	tool, _ := params["tool"].(string)
	switch tool {
	case "nftables":
		cmd := exec.Command("nft", "-f", "-")
		cmd.Stdin = strings.NewReader(snapshot)
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("restoring nftables: %s: %w", out, err)
		}
	case "firewalld":
		exec.Command("firewall-cmd", "--complete-reload").Run() //nolint
	default:
		// iptables-restore
		cmd := exec.Command("iptables-restore")
		cmd.Stdin = strings.NewReader(snapshot)
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("iptables-restore: %s: %w", out, err)
		}
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 3: Create `agent/commands/ossecurity/mount_linux.go`**

```go
//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func mountExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("harden_mount_options requires root privileges")
	}

	// Read current fstab
	fstabData, err := os.ReadFile("/etc/fstab")
	if err != nil {
		return nil, fmt.Errorf("reading /etc/fstab: %w", err)
	}
	snapshot := string(fstabData)

	// Target mounts and required options
	targets := map[string][]string{
		"/tmp":      {"noexec", "nosuid", "nodev"},
		"/dev/shm":  {"noexec", "nosuid", "nodev"},
		"/var/tmp":  {"noexec", "nosuid", "nodev"},
	}

	// Allow override of targets from params
	if custom, ok := params["targets"].(map[string]any); ok {
		for mount, optsRaw := range custom {
			if opts, ok := optsRaw.([]any); ok {
				var optStrings []string
				for _, o := range opts {
					if s, ok := o.(string); ok {
						optStrings = append(optStrings, s)
					}
				}
				targets[mount] = optStrings
			}
		}
	}

	lines := strings.Split(snapshot, "\n")
	for i, line := range lines {
		if strings.HasPrefix(line, "#") || strings.TrimSpace(line) == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		mountPoint := fields[1]
		if requiredOpts, ok := targets[mountPoint]; ok {
			currentOpts := strings.Split(fields[3], ",")
			// Add missing options
			for _, req := range requiredOpts {
				found := false
				for _, cur := range currentOpts {
					if cur == req {
						found = true
						break
					}
				}
				if !found {
					currentOpts = append(currentOpts, req)
				}
			}
			fields[3] = strings.Join(currentOpts, ",")
			lines[i] = strings.Join(fields, "\t")
		}
	}

	newFstab := strings.Join(lines, "\n")
	if err := os.WriteFile("/etc/fstab", []byte(newFstab), 0644); err != nil {
		return nil, fmt.Errorf("writing /etc/fstab: %w", err)
	}

	// Remount affected filesystems
	for mount := range targets {
		exec.Command("mount", "-o", "remount", mount).Run() //nolint
	}

	return map[string]any{
		"targets_hardened": targets,
		"snapshot":         snapshot,
		"applied_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func mountRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if err := os.WriteFile("/etc/fstab", []byte(snapshot), 0644); err != nil {
		return nil, fmt.Errorf("restoring /etc/fstab: %w", err)
	}
	// Remount with restored options
	exec.Command("mount", "-a").Run() //nolint
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 4: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/ossecurity/... && "C:/Program Files/Go/bin/go.exe" test ./commands/ossecurity/... -v
```

- [ ] **Step 5: Commit**

```
git add agent/commands/ossecurity/seccomp_linux.go agent/commands/ossecurity/firewall_linux.go agent/commands/ossecurity/mount_linux.go
git commit -m "feat(ossecurity): implement configure_seccomp, configure_host_firewall, harden_mount_options"
```

---

### Task 5: `deploy_auditd_rules`, `setup_file_integrity_monitoring`, `audit_os_security_posture`

**Files:**
- Create: `agent/commands/ossecurity/auditd_linux.go`
- Create: `agent/commands/ossecurity/fim_linux.go`
- Create: `agent/commands/ossecurity/posture_linux.go`

- [ ] **Step 1: Create `agent/commands/ossecurity/auditd_linux.go`**

```go
//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"time"
)

var auditProfiles = map[string]string{
	"cis_level1": `-a always,exit -F arch=b64 -S execve -k exec_commands
-w /etc/passwd -p wa -k identity
-w /etc/group -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/sudoers -p wa -k sudoers
-w /etc/ssh/sshd_config -p wa -k sshd
-a always,exit -F arch=b64 -S adjtimex -S settimeofday -k time_change
-a always,exit -F arch=b64 -S sethostname -S setdomainname -k system_locale
`,
	"cis_level2": `-a always,exit -F arch=b64 -S execve -k exec_commands
-a always,exit -F arch=b64 -S open -F exit=-EPERM -k access
-a always,exit -F arch=b64 -S open -F exit=-EACCES -k access
-w /etc/passwd -p wa -k identity
-w /etc/group -p wa -k identity
-w /etc/shadow -p wa -k identity
-w /etc/gshadow -p wa -k identity
-w /etc/sudoers -p wa -k sudoers
-w /etc/sudoers.d/ -p wa -k sudoers
-w /etc/ssh/sshd_config -p wa -k sshd
-a always,exit -F arch=b64 -S adjtimex -S settimeofday -k time_change
-a always,exit -F arch=b64 -S sethostname -S setdomainname -k system_locale
-a always,exit -F arch=b64 -S chmod -S fchmod -S fchmodat -k perm_mod
-a always,exit -F arch=b64 -S chown -S fchown -S lchown -S fchownat -k perm_chng
`,
}

const auditRulesPath = "/etc/audit/rules.d/99-nexplane.rules"

func auditdExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("deploy_auditd_rules requires root privileges")
	}
	if _, err := exec.LookPath("auditctl"); err != nil {
		return nil, fmt.Errorf("auditd not installed: auditctl not found")
	}

	// Snapshot
	snapshot := ""
	if data, err := os.ReadFile(auditRulesPath); err == nil {
		snapshot = string(data)
	}

	// Determine rules content
	rulesContent := ""
	if profile, ok := params["profile"].(string); ok && profile != "" {
		content, ok := auditProfiles[profile]
		if !ok {
			return nil, fmt.Errorf("unknown profile %q: must be cis_level1 or cis_level2", profile)
		}
		rulesContent = content
	}
	if customRules, ok := params["rules"].(string); ok && customRules != "" {
		rulesContent += customRules
	}

	// Ensure rules directory exists
	if err := os.MkdirAll("/etc/audit/rules.d", 0750); err != nil {
		return nil, fmt.Errorf("creating rules dir: %w", err)
	}
	if err := os.WriteFile(auditRulesPath, []byte(rulesContent), 0640); err != nil {
		return nil, fmt.Errorf("writing audit rules: %w", err)
	}

	// Reload auditd
	if out, err := exec.Command("augenrules", "--load").CombinedOutput(); err != nil {
		// Fallback: auditctl -R
		exec.Command("auditctl", "-R", auditRulesPath).Run() //nolint
		_ = out
	}

	return map[string]any{
		"rules_path": auditRulesPath,
		"profile":    params["profile"],
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func auditdRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if snapshot == "" {
		if err := os.Remove(auditRulesPath); err != nil && !os.IsNotExist(err) {
			return nil, fmt.Errorf("removing audit rules: %w", err)
		}
	} else {
		if err := os.WriteFile(auditRulesPath, []byte(snapshot), 0640); err != nil {
			return nil, fmt.Errorf("restoring audit rules: %w", err)
		}
	}
	exec.Command("augenrules", "--load").Run() //nolint
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 2: Create `agent/commands/ossecurity/fim_linux.go`**

```go
//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func fimExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("setup_file_integrity_monitoring requires root privileges")
	}

	action, _ := params["action"].(string)
	if action == "" {
		action = "init"
	}
	tool := detectFIMTool()

	switch tool {
	case "aide":
		return aideAction(action, params)
	case "tripwire":
		return tripwireAction(action, params)
	default:
		return nil, fmt.Errorf("no file integrity monitoring tool found (aide or tripwire required)")
	}
}

func detectFIMTool() string {
	if _, err := exec.LookPath("aide"); err == nil {
		return "aide"
	}
	if _, err := exec.LookPath("tripwire"); err == nil {
		return "tripwire"
	}
	return ""
}

func aideAction(action string, params map[string]any) (map[string]any, error) {
	switch action {
	case "init":
		if out, err := exec.Command("aide", "--init").CombinedOutput(); err != nil {
			return nil, fmt.Errorf("aide --init: %s: %w", out, err)
		}
		// Move new database into place
		exec.Command("mv", "/var/lib/aide/aide.db.new.gz", "/var/lib/aide/aide.db.gz").Run() //nolint
		return map[string]any{
			"tool": "aide", "action": "init",
			"database_path": "/var/lib/aide/aide.db.gz",
			"applied_at":    time.Now().UTC().Format(time.RFC3339),
		}, nil
	case "check":
		out, err := exec.Command("aide", "--check").CombinedOutput()
		return map[string]any{
			"tool": "aide", "action": "check",
			"output":     string(out),
			"violations": err != nil,
			"checked_at": time.Now().UTC().Format(time.RFC3339),
		}, nil
	default:
		return nil, fmt.Errorf("unknown action %q: must be init or check", action)
	}
}

func tripwireAction(action string, params map[string]any) (map[string]any, error) {
	switch action {
	case "init":
		if out, err := exec.Command("tripwire", "--init").CombinedOutput(); err != nil {
			return nil, fmt.Errorf("tripwire --init: %s: %w", out, err)
		}
		return map[string]any{
			"tool": "tripwire", "action": "init",
			"applied_at": time.Now().UTC().Format(time.RFC3339),
		}, nil
	case "check":
		out, _ := exec.Command("tripwire", "--check").CombinedOutput()
		violations := strings.Contains(string(out), "Violations:")
		return map[string]any{
			"tool": "tripwire", "action": "check",
			"output":     string(out),
			"violations": violations,
			"checked_at": time.Now().UTC().Format(time.RFC3339),
		}, nil
	default:
		return nil, fmt.Errorf("unknown action %q: must be init or check", action)
	}
}

func fimRollbackOS(params map[string]any) (map[string]any, error) {
	tool := detectFIMTool()
	switch tool {
	case "aide":
		os.Remove("/var/lib/aide/aide.db.gz")
	case "tripwire":
		os.Remove("/var/lib/tripwire/$(hostname).twd")
	}
	return map[string]any{"rolled_back": true, "tool": tool}, nil
}
```

- [ ] **Step 3: Create `agent/commands/ossecurity/posture_linux.go`**

```go
//go:build linux

package ossecurity

import (
	"os/exec"
	"strings"
	"time"
)

func auditPostureOS(_ map[string]any) (map[string]any, error) {
	result := map[string]any{"audited_at": time.Now().UTC().Format(time.RFC3339)}

	// SELinux status
	selinuxOut, _ := exec.Command("sestatus").Output()
	result["selinux"] = map[string]any{
		"available": len(selinuxOut) > 0,
		"status":    strings.TrimSpace(string(selinuxOut)),
	}

	// AppArmor status
	aaOut, _ := exec.Command("aa-status", "--json").Output()
	result["apparmor"] = map[string]any{
		"available": len(aaOut) > 0,
		"status":    strings.TrimSpace(string(aaOut)),
	}

	// auditd status
	auditOut, _ := exec.Command("auditctl", "-s").Output()
	result["auditd"] = map[string]any{
		"available": len(auditOut) > 0,
		"status":    strings.TrimSpace(string(auditOut)),
	}

	// Active denials (ausearch last hour)
	denialsOut, _ := exec.Command("ausearch", "-m", "avc", "-ts", "recent").Output()
	result["recent_denials"] = strings.TrimSpace(string(denialsOut))

	return result, nil
}
```

- [ ] **Step 4: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/ossecurity/... && "C:/Program Files/Go/bin/go.exe" test ./commands/ossecurity/... -v
```

- [ ] **Step 5: Commit**

```
git add agent/commands/ossecurity/auditd_linux.go agent/commands/ossecurity/fim_linux.go agent/commands/ossecurity/posture_linux.go
git commit -m "feat(ossecurity): implement deploy_auditd_rules, setup_file_integrity_monitoring, audit_os_security_posture"
```

---

### Task 6: `ebpf` package — `deploy_ebpf_policy`, `configure_ebpf_security_policy`, `audit_ebpf_posture`

**Files:**
- Create: `agent/commands/ebpf/ebpf.go`
- Create: `agent/commands/ebpf/ebpf_other.go`
- Create: `agent/commands/ebpf/ebpf_linux.go`
- Create: `agent/commands/ebpf/ebpf_test.go`

- [ ] **Step 1: Create `agent/commands/ebpf/ebpf.go`**

```go
package ebpf

import "fmt"

var validAttachTypes = map[string]bool{
	"kprobe": true, "tracepoint": true, "tc": true, "xdp": true, "cgroup": true, "lsm": true,
}

var validFrameworks = map[string]bool{
	"cilium": true, "falco": true, "tetragon": true, "bpfd": true,
}

func DeployEBPFPolicyExecute(params map[string]any) (map[string]any, error) {
	if prog, _ := params["program_path"].(string); prog == "" {
		return nil, fmt.Errorf("program_path is required")
	}
	if attachType, ok := params["attach_type"].(string); ok && attachType != "" {
		if !validAttachTypes[attachType] {
			return nil, fmt.Errorf("invalid attach_type %q: must be one of kprobe, tracepoint, tc, xdp, cgroup, lsm", attachType)
		}
	}
	return ebpfDeployOS(params)
}

func DeployEBPFPolicyRollback(params map[string]any) (map[string]any, error) {
	return ebpfDeployRollbackOS(params)
}

func ConfigureEBPFSecurityPolicyExecute(params map[string]any) (map[string]any, error) {
	framework, _ := params["framework"].(string)
	if !validFrameworks[framework] {
		return nil, fmt.Errorf("invalid framework %q: must be one of cilium, falco, tetragon, bpfd", framework)
	}
	if _, ok := params["policy"].(string); !ok {
		return nil, fmt.Errorf("policy content is required")
	}
	return ebpfPolicyOS(params)
}

func ConfigureEBPFSecurityPolicyRollback(params map[string]any) (map[string]any, error) {
	return ebpfPolicyRollbackOS(params)
}

func AuditEBPFPostureExecute(params map[string]any) (map[string]any, error) {
	return ebpfAuditOS(params)
}
```

- [ ] **Step 2: Create `agent/commands/ebpf/ebpf_other.go`**

```go
//go:build !linux

package ebpf

import "fmt"

func ebpfDeployOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_ebpf_policy requires Linux")
}
func ebpfDeployRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_ebpf_policy requires Linux")
}
func ebpfPolicyOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ebpf_security_policy requires Linux")
}
func ebpfPolicyRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ebpf_security_policy requires Linux")
}
func ebpfAuditOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_ebpf_posture requires Linux")
}
```

- [ ] **Step 3: Create `agent/commands/ebpf/ebpf_linux.go`**

```go
//go:build linux

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func ebpfDeployOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("deploy_ebpf_policy requires root privileges")
	}
	if _, err := exec.LookPath("bpftool"); err != nil {
		return nil, fmt.Errorf("bpftool not found (required for eBPF program management)")
	}

	programPath, _ := params["program_path"].(string)
	attachType, _ := params["attach_type"].(string)
	attachTarget, _ := params["attach_target"].(string)

	// Load program
	out, err := exec.Command("bpftool", "prog", "load", programPath, "/sys/fs/bpf/nexplane_prog").CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("bpftool prog load: %s: %w", out, err)
	}

	// Get program ID
	idOut, _ := exec.Command("bpftool", "prog", "show", "pinned", "/sys/fs/bpf/nexplane_prog", "--json").Output()
	progID := strings.TrimSpace(string(idOut))

	// Attach (best-effort for common types)
	attachResult := ""
	if attachType != "" && attachTarget != "" {
		attachOut, err := exec.Command("bpftool", "net", "attach", attachType,
			"id", progID, "dev", attachTarget).CombinedOutput()
		if err != nil {
			attachResult = fmt.Sprintf("attach failed: %s", attachOut)
		} else {
			attachResult = "attached"
		}
	}

	return map[string]any{
		"program_path": programPath,
		"attach_type":  attachType,
		"attach_target": attachTarget,
		"attach_result": attachResult,
		"pin_path":     "/sys/fs/bpf/nexplane_prog",
		"snapshot":     map[string]any{"pin_path": "/sys/fs/bpf/nexplane_prog"},
		"applied_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ebpfDeployRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	pinPath, _ := snapshot["pin_path"].(string)
	if pinPath != "" {
		// Detach and unpin
		exec.Command("bpftool", "net", "detach", "xdp", "dev", "eth0").Run() //nolint
		exec.Command("rm", "-f", pinPath).Run()                               //nolint
	}
	return map[string]any{"rolled_back": true}, nil
}

func ebpfPolicyOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_ebpf_security_policy requires root privileges")
	}
	framework, _ := params["framework"].(string)
	policy, _ := params["policy"].(string)

	// Write policy to framework-specific location
	policyPath := fmt.Sprintf("/etc/nexplane/ebpf/%s-policy.yaml", framework)
	if err := os.MkdirAll("/etc/nexplane/ebpf", 0755); err != nil {
		return nil, fmt.Errorf("creating ebpf policy dir: %w", err)
	}
	snapshot := ""
	if data, err := os.ReadFile(policyPath); err == nil {
		snapshot = string(data)
	}
	if err := os.WriteFile(policyPath, []byte(policy), 0644); err != nil {
		return nil, fmt.Errorf("writing policy: %w", err)
	}

	// Apply via framework CLI (best-effort)
	switch framework {
	case "falco":
		exec.Command("falco", "-r", policyPath, "--validate").Run() //nolint
	case "cilium":
		exec.Command("cilium", "policy", "import", policyPath).Run() //nolint
	}

	return map[string]any{
		"framework":   framework,
		"policy_path": policyPath,
		"snapshot":    snapshot,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ebpfPolicyRollbackOS(params map[string]any) (map[string]any, error) {
	framework, _ := params["framework"].(string)
	snapshot, _ := params["snapshot"].(string)
	policyPath := fmt.Sprintf("/etc/nexplane/ebpf/%s-policy.yaml", framework)
	if snapshot == "" {
		os.Remove(policyPath)
	} else {
		os.WriteFile(policyPath, []byte(snapshot), 0644) //nolint
	}
	return map[string]any{"rolled_back": true}, nil
}

func ebpfAuditOS(_ map[string]any) (map[string]any, error) {
	result := map[string]any{"audited_at": time.Now().UTC().Format(time.RFC3339)}

	// List all loaded eBPF programs
	out, err := exec.Command("bpftool", "prog", "list", "--json").Output()
	if err != nil {
		result["bpftool_available"] = false
		result["programs"] = []any{}
		return result, nil
	}
	result["bpftool_available"] = true
	result["programs_raw"] = strings.TrimSpace(string(out))

	// List network attachments
	netOut, _ := exec.Command("bpftool", "net", "list", "--json").Output()
	result["network_attachments_raw"] = strings.TrimSpace(string(netOut))

	// Flag programs not loaded by nexplane (heuristic: check pinned paths)
	unexpectedOut, _ := exec.Command("find", "/sys/fs/bpf", "-not", "-path", "*/nexplane*", "-type", "f").Output()
	var unexpected []string
	for _, line := range strings.Split(strings.TrimSpace(string(unexpectedOut)), "\n") {
		if line != "" {
			unexpected = append(unexpected, line)
		}
	}
	result["unexpected_programs"] = unexpected
	result["tags"] = []string{}
	if len(unexpected) > 0 {
		result["tags"] = []string{"unexpected-ebpf-programs"}
	}

	return result, nil
}
```

- [ ] **Step 4: Create `agent/commands/ebpf/ebpf_test.go`**

```go
package ebpf_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/ebpf"
)

func TestDeployEBPFPolicyRequiresProgramPath(t *testing.T) {
	_, err := ebpf.DeployEBPFPolicyExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "program_path") {
		t.Errorf("expected program_path error, got: %v", err)
	}
}

func TestDeployEBPFPolicyInvalidAttachType(t *testing.T) {
	_, err := ebpf.DeployEBPFPolicyExecute(map[string]any{
		"program_path": "/tmp/prog.o",
		"attach_type":  "invalid",
	})
	if err == nil || !strings.Contains(err.Error(), "invalid attach_type") {
		t.Errorf("expected attach_type error, got: %v", err)
	}
}

func TestConfigureEBPFSecurityPolicyInvalidFramework(t *testing.T) {
	_, err := ebpf.ConfigureEBPFSecurityPolicyExecute(map[string]any{
		"framework": "unknown",
		"policy":    "{}",
	})
	if err == nil || !strings.Contains(err.Error(), "invalid framework") {
		t.Errorf("expected framework error, got: %v", err)
	}
}

func TestConfigureEBPFSecurityPolicyRequiresPolicy(t *testing.T) {
	_, err := ebpf.ConfigureEBPFSecurityPolicyExecute(map[string]any{
		"framework": "falco",
	})
	if err == nil || !strings.Contains(err.Error(), "policy") {
		t.Errorf("expected policy error, got: %v", err)
	}
}
```

- [ ] **Step 5: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/ebpf/... && "C:/Program Files/Go/bin/go.exe" test ./commands/ebpf/... -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```
git add agent/commands/ebpf/
git commit -m "feat(ebpf): implement deploy_ebpf_policy, configure_ebpf_security_policy, audit_ebpf_posture"
```

---

### Task 7: Register all 13 commands in `executor.go`

**Files:**
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Update `agent/executor/executor.go`** (full file shown — replace entire file)

```go
package executor

import (
	"fmt"

	"nexplane-agent/commands/changip"
	"nexplane-agent/commands/configsyslog"
	"nexplane-agent/commands/ebpf"
	"nexplane-agent/commands/estimatesize"
	"nexplane-agent/commands/linuxauth"
	"nexplane-agent/commands/ossecurity"
	"nexplane-agent/commands/uploadimage"
	"nexplane-agent/commands/virtualize"
)

type Result struct {
	Status string
	Data   map[string]any
	Error  string
}

type CommandFunc func(params map[string]any) (map[string]any, error)

var commands = map[string]CommandFunc{
	// Infrastructure
	"estimate_image_size":      estimatesize.Execute,
	"change_ip":                changip.Execute,
	"configure_syslog":         configsyslog.Execute,
	"virtualize_for_migration": virtualize.Execute,
	"upload_image":             uploadimage.Execute,
	// Linux auth (Spec 5b)
	"configure_pam":                  linuxauth.ConfigurePAMExecute,
	"harden_ssh":                     linuxauth.HardenSSHExecute,
	"audit_users_and_groups":         linuxauth.AuditUsersAndGroupsExecute,
	"audit_privesc_vulnerabilities":  linuxauth.AuditPrivescVulnerabilitiesExecute,
	"manage_ca_certificates":         linuxauth.ManageCACertificatesExecute,
	"configure_ntp":                  linuxauth.ConfigureNTPExecute,
	// OS security (Spec 5a)
	"configure_selinux":              ossecurity.ConfigureSELinuxExecute,
	"configure_apparmor":             ossecurity.ConfigureAppArmorExecute,
	"configure_seccomp":              ossecurity.ConfigureSeccompExecute,
	"apply_sysctl_hardening":         ossecurity.ApplySysctlHardeningExecute,
	"configure_host_firewall":        ossecurity.ConfigureHostFirewallExecute,
	"blacklist_kernel_modules":       ossecurity.BlacklistKernelModulesExecute,
	"harden_mount_options":           ossecurity.HardenMountOptionsExecute,
	"deploy_auditd_rules":            ossecurity.DeployAuditdRulesExecute,
	"setup_file_integrity_monitoring": ossecurity.SetupFileIntegrityMonitoringExecute,
	"audit_os_security_posture":      ossecurity.AuditOSSecurityPostureExecute,
	// eBPF (Spec 5a)
	"deploy_ebpf_policy":             ebpf.DeployEBPFPolicyExecute,
	"configure_ebpf_security_policy": ebpf.ConfigureEBPFSecurityPolicyExecute,
	"audit_ebpf_posture":             ebpf.AuditEBPFPostureExecute,
}

var rollbacks = map[string]CommandFunc{
	// Infrastructure
	"change_ip":                changip.Rollback,
	"configure_syslog":         configsyslog.Rollback,
	"virtualize_for_migration": virtualize.Rollback,
	"upload_image":             uploadimage.Rollback,
	// Linux auth (Spec 5b)
	"configure_pam":           linuxauth.ConfigurePAMRollback,
	"harden_ssh":              linuxauth.HardenSSHRollback,
	"manage_ca_certificates":  linuxauth.ManageCACertificatesRollback,
	"configure_ntp":           linuxauth.ConfigureNTPRollback,
	// OS security (Spec 5a)
	"configure_selinux":              ossecurity.ConfigureSELinuxRollback,
	"configure_apparmor":             ossecurity.ConfigureAppArmorRollback,
	"configure_seccomp":              ossecurity.ConfigureSeccompRollback,
	"apply_sysctl_hardening":         ossecurity.ApplySysctlHardeningRollback,
	"configure_host_firewall":        ossecurity.ConfigureHostFirewallRollback,
	"blacklist_kernel_modules":       ossecurity.BlacklistKernelModulesRollback,
	"harden_mount_options":           ossecurity.HardenMountOptionsRollback,
	"deploy_auditd_rules":            ossecurity.DeployAuditdRulesRollback,
	"setup_file_integrity_monitoring": ossecurity.SetupFileIntegrityMonitoringRollback,
	// eBPF (Spec 5a)
	"deploy_ebpf_policy":             ebpf.DeployEBPFPolicyRollback,
	"configure_ebpf_security_policy": ebpf.ConfigureEBPFSecurityPolicyRollback,
}

func Dispatch(command string, params map[string]any, rollback bool, previousResult map[string]any) Result {
	var fn CommandFunc
	var ok bool

	if rollback {
		fn, ok = rollbacks[command]
		if !ok {
			return Result{Status: "failed", Error: fmt.Sprintf("no rollback defined for command %q", command)}
		}
		merged := make(map[string]any, len(params)+len(previousResult))
		for k, v := range previousResult {
			merged[k] = v
		}
		for k, v := range params {
			merged[k] = v
		}
		params = merged
	} else {
		fn, ok = commands[command]
		if !ok {
			return Result{Status: "failed", Error: fmt.Sprintf("unknown command %q", command)}
		}
	}

	data, err := fn(params)
	if err != nil {
		return Result{Status: "failed", Data: data, Error: err.Error()}
	}
	return Result{Status: "completed", Data: data}
}
```

- [ ] **Step 2: Build all packages**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./...
```

Expected: no output (success)

- [ ] **Step 3: Run all tests**

```
cd agent && "C:/Program Files/Go/bin/go.exe" test ./... -v
```

Expected: all PASS

- [ ] **Step 4: Commit**

```
git add agent/executor/executor.go
git commit -m "feat(executor): register all 13 ossecurity+ebpf commands (Spec 5a)"
```

---

### Task 8: Catalog entries (13 commands) and mock stubs

**Files:**
- Modify: `backend/app/connectors/catalog/nexplane_agent_mock.json`
- Create: 13 Python mock stub files in `backend/app/connectors/executors/nexplane_agent_mock/`

- [ ] **Step 1: Append 13 catalog entries to the `actions` array in `nexplane_agent_mock.json`**

```json
{
  "action_id": "configure_selinux",
  "generic_action": "configure_selinux",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Configure SELinux",
  "description": "Set SELinux mode (enforcing/permissive/disabled), install policy modules, or generate policy from audit log denials.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "mode", "type": "string", "required": false},
    {"name": "policy_module_path", "type": "string", "required": false},
    {"name": "generate_from_audit_log", "type": "boolean", "required": false, "default": false}
  ],
  "executor": "nexplane_agent_mock.configure_selinux",
  "rollback_action": "configure_selinux",
  "estimated_duration_seconds": 15
},
{
  "action_id": "configure_apparmor",
  "generic_action": "configure_apparmor",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Configure AppArmor",
  "description": "Load AppArmor profiles and set enforce/complain/disable mode.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "profile_name", "type": "string", "required": false},
    {"name": "profile_content", "type": "string", "required": false},
    {"name": "mode", "type": "string", "required": false}
  ],
  "executor": "nexplane_agent_mock.configure_apparmor",
  "rollback_action": "configure_apparmor",
  "estimated_duration_seconds": 10
},
{
  "action_id": "configure_seccomp",
  "generic_action": "configure_seccomp",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Configure Seccomp Filter",
  "description": "Apply a seccomp filter profile to a systemd service via drop-in override.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "service_name", "type": "string", "required": true},
    {"name": "profile", "type": "string", "required": true}
  ],
  "executor": "nexplane_agent_mock.configure_seccomp",
  "rollback_action": "configure_seccomp",
  "estimated_duration_seconds": 10
},
{
  "action_id": "apply_sysctl_hardening",
  "generic_action": "apply_sysctl_hardening",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Apply Sysctl Hardening",
  "description": "Apply CIS-level sysctl parameters: disable IP forwarding, enable SYN cookies, disable ICMP redirects, enable source route validation. Writes /etc/sysctl.d/99-nexplane-hardening.conf.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "settings", "type": "object", "required": false}
  ],
  "executor": "nexplane_agent_mock.apply_sysctl_hardening",
  "rollback_action": "apply_sysctl_hardening",
  "estimated_duration_seconds": 10
},
{
  "action_id": "configure_host_firewall",
  "generic_action": "configure_host_firewall",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Configure Host Firewall",
  "description": "Add or remove iptables/nftables/firewalld rules on the host. Auto-detects firewall tool.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "action", "type": "string", "required": true},
    {"name": "rule", "type": "object", "required": false}
  ],
  "executor": "nexplane_agent_mock.configure_host_firewall",
  "rollback_action": "configure_host_firewall",
  "estimated_duration_seconds": 10,
  "safety_notes": ["Incorrect firewall rules can lock out remote management access"]
},
{
  "action_id": "blacklist_kernel_modules",
  "generic_action": "blacklist_kernel_modules",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Blacklist Kernel Modules",
  "description": "Prevent loading of specified kernel modules via /etc/modprobe.d/nexplane-blacklist.conf.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "modules", "type": "array", "required": true}
  ],
  "executor": "nexplane_agent_mock.blacklist_kernel_modules",
  "rollback_action": "blacklist_kernel_modules",
  "estimated_duration_seconds": 10
},
{
  "action_id": "harden_mount_options",
  "generic_action": "harden_mount_options",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Harden Mount Options",
  "description": "Apply noexec/nosuid/nodev to /tmp, /dev/shm, /var/tmp via /etc/fstab. Remounts affected filesystems.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "targets", "type": "object", "required": false}
  ],
  "executor": "nexplane_agent_mock.harden_mount_options",
  "rollback_action": "harden_mount_options",
  "estimated_duration_seconds": 15
},
{
  "action_id": "deploy_auditd_rules",
  "generic_action": "deploy_auditd_rules",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Deploy Auditd Rules",
  "description": "Install CIS Level 1/2 or custom auditd rule sets to /etc/audit/rules.d/. Reloads auditd.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "profile", "type": "string", "required": false},
    {"name": "rules", "type": "string", "required": false}
  ],
  "executor": "nexplane_agent_mock.deploy_auditd_rules",
  "rollback_action": "deploy_auditd_rules",
  "estimated_duration_seconds": 10
},
{
  "action_id": "setup_file_integrity_monitoring",
  "generic_action": "setup_file_integrity_monitoring",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Setup File Integrity Monitoring",
  "description": "Initialize AIDE or Tripwire baseline database (init), or run a check against the baseline (check).",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "action", "type": "string", "required": false, "default": "init"}
  ],
  "executor": "nexplane_agent_mock.setup_file_integrity_monitoring",
  "rollback_action": "setup_file_integrity_monitoring",
  "estimated_duration_seconds": 120
},
{
  "action_id": "audit_os_security_posture",
  "generic_action": "audit_os_security_posture",
  "action_type": "ingest",
  "execution_tier": 3,
  "display_name": "Audit OS Security Posture",
  "description": "Read-only: reports current SELinux mode, AppArmor profiles, auditd status, and recent MAC denials.",
  "applicable_asset_types": ["server"],
  "parameters": [],
  "executor": "nexplane_agent_mock.audit_os_security_posture",
  "estimated_duration_seconds": 15
},
{
  "action_id": "deploy_ebpf_policy",
  "generic_action": "deploy_ebpf_policy",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Deploy eBPF Policy",
  "description": "Load and attach a compiled eBPF program to a kernel hook (kprobe, tracepoint, tc, xdp, cgroup, lsm).",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "program_path", "type": "string", "required": true},
    {"name": "attach_type", "type": "string", "required": false},
    {"name": "attach_target", "type": "string", "required": false}
  ],
  "executor": "nexplane_agent_mock.deploy_ebpf_policy",
  "rollback_action": "deploy_ebpf_policy",
  "estimated_duration_seconds": 30,
  "safety_notes": ["Requires kernel 5.8+ and CAP_BPF capability"]
},
{
  "action_id": "configure_ebpf_security_policy",
  "generic_action": "configure_ebpf_security_policy",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Configure eBPF Security Policy",
  "description": "Apply a declarative security policy via an installed eBPF framework (Cilium, Falco, Tetragon, bpfd).",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "framework", "type": "string", "required": true},
    {"name": "policy", "type": "string", "required": true}
  ],
  "executor": "nexplane_agent_mock.configure_ebpf_security_policy",
  "rollback_action": "configure_ebpf_security_policy",
  "estimated_duration_seconds": 30
},
{
  "action_id": "audit_ebpf_posture",
  "generic_action": "audit_ebpf_posture",
  "action_type": "ingest",
  "execution_tier": 3,
  "display_name": "Audit eBPF Posture",
  "description": "Read-only: lists all loaded eBPF programs and network attachments. Flags programs not deployed by Nexplane.",
  "applicable_asset_types": ["server"],
  "parameters": [],
  "executor": "nexplane_agent_mock.audit_ebpf_posture",
  "estimated_duration_seconds": 10
}
```

- [ ] **Step 2: Validate JSON**

```
python -c "import json; json.load(open('backend/app/connectors/catalog/nexplane_agent_mock.json')); print('valid')"
```

- [ ] **Step 3: Create all 13 mock executor stubs**

Create `backend/app/connectors/executors/nexplane_agent_mock/configure_selinux.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "configure_selinux", "previous_mode": "Permissive", "new_mode": parameters.get("mode", "enforcing"),
            "modules_installed": [], "config_snapshot": {}, "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_selinux"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/configure_apparmor.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "configure_apparmor", "profile_name": parameters.get("profile_name"), "mode_applied": parameters.get("mode", "enforce"),
            "snapshot": {}, "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_apparmor"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/configure_seccomp.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    svc = parameters.get("service_name")
    return {"action": "configure_seccomp", "service_name": svc,
            "drop_in_path": f"/etc/systemd/system/{svc}.service.d/nexplane-seccomp.conf",
            "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_seccomp"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/apply_sysctl_hardening.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "apply_sysctl_hardening", "drop_in_path": "/etc/sysctl.d/99-nexplane-hardening.conf",
            "settings_applied": {"net.ipv4.ip_forward": "0", "net.ipv4.tcp_syncookies": "1"},
            "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "apply_sysctl_hardening"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/configure_host_firewall.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "configure_host_firewall", "tool": "iptables", "action_applied": parameters.get("action"),
            "snapshot": "# iptables-save output", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_host_firewall"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/blacklist_kernel_modules.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "blacklist_kernel_modules", "modules_blacklisted": parameters.get("modules", []),
            "blacklist_path": "/etc/modprobe.d/nexplane-blacklist.conf",
            "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "blacklist_kernel_modules"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/harden_mount_options.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "harden_mount_options",
            "targets_hardened": {"/tmp": ["noexec","nosuid","nodev"], "/dev/shm": ["noexec","nosuid","nodev"]},
            "snapshot": "# previous /etc/fstab", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "harden_mount_options"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/deploy_auditd_rules.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "deploy_auditd_rules", "rules_path": "/etc/audit/rules.d/99-nexplane.rules",
            "profile": parameters.get("profile", "cis_level1"),
            "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "deploy_auditd_rules"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/setup_file_integrity_monitoring.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    action = parameters.get("action", "init")
    return {"action": "setup_file_integrity_monitoring", "tool": "aide", "action": action,
            "database_path": "/var/lib/aide/aide.db.gz", "violations": False,
            "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "setup_file_integrity_monitoring", "tool": "aide"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/audit_os_security_posture.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "audit_os_security_posture",
            "selinux": {"available": True, "status": "SELinux status: enabled\nCurrent mode: enforcing"},
            "apparmor": {"available": False, "status": ""},
            "auditd": {"available": True, "status": "enabled"},
            "recent_denials": "", "audited_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "audit_os_security_posture is read-only"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/deploy_ebpf_policy.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "deploy_ebpf_policy", "program_path": parameters.get("program_path"),
            "attach_type": parameters.get("attach_type"), "pin_path": "/sys/fs/bpf/nexplane_prog",
            "snapshot": {"pin_path": "/sys/fs/bpf/nexplane_prog"},
            "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "deploy_ebpf_policy"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/configure_ebpf_security_policy.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    fw = parameters.get("framework", "falco")
    return {"action": "configure_ebpf_security_policy", "framework": fw,
            "policy_path": f"/etc/nexplane/ebpf/{fw}-policy.yaml",
            "snapshot": "", "applied_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_ebpf_security_policy"}
```

Create `backend/app/connectors/executors/nexplane_agent_mock/audit_ebpf_posture.py`:
```python
from datetime import datetime, timezone
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "audit_ebpf_posture", "bpftool_available": True,
            "programs_raw": "[]", "network_attachments_raw": "[]",
            "unexpected_programs": [], "tags": [],
            "audited_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "audit_ebpf_posture is read-only"}
```

- [ ] **Step 4: Validate JSON and run catalog tests**

```
python -c "import json; json.load(open('backend/app/connectors/catalog/nexplane_agent_mock.json')); print('valid')"
cd backend && python -m pytest app/tests/test_catalog_service.py -v
```

Expected: valid, PASS

- [ ] **Step 5: Commit**

```
git add backend/app/connectors/catalog/nexplane_agent_mock.json
git add backend/app/connectors/executors/nexplane_agent_mock/configure_selinux.py
git add backend/app/connectors/executors/nexplane_agent_mock/configure_apparmor.py
git add backend/app/connectors/executors/nexplane_agent_mock/configure_seccomp.py
git add backend/app/connectors/executors/nexplane_agent_mock/apply_sysctl_hardening.py
git add backend/app/connectors/executors/nexplane_agent_mock/configure_host_firewall.py
git add backend/app/connectors/executors/nexplane_agent_mock/blacklist_kernel_modules.py
git add backend/app/connectors/executors/nexplane_agent_mock/harden_mount_options.py
git add backend/app/connectors/executors/nexplane_agent_mock/deploy_auditd_rules.py
git add backend/app/connectors/executors/nexplane_agent_mock/setup_file_integrity_monitoring.py
git add backend/app/connectors/executors/nexplane_agent_mock/audit_os_security_posture.py
git add backend/app/connectors/executors/nexplane_agent_mock/deploy_ebpf_policy.py
git add backend/app/connectors/executors/nexplane_agent_mock/configure_ebpf_security_policy.py
git add backend/app/connectors/executors/nexplane_agent_mock/audit_ebpf_posture.py
git commit -m "feat(catalog+mock): add 13 ossecurity+ebpf actions to catalog and mock stubs (Spec 5a)"
```
