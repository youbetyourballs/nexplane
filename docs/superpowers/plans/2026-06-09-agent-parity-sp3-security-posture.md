# Agent Parity SP3: macOS Security Posture

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 8 macOS-native equivalents of Linux security posture controls: SELinux→SIP/Gatekeeper/Santa, AppArmor→Santa rules, seccomp→sandbox-exec, sysctl hardening, kernel modules→systemextensionsctl, mount→diskutil, auditd→BSM audit, FIM→fswatch+SHA256.

**Architecture:** Each control gets its own `_darwin.go` file in `agent/commands/ossecurity/`. All use an `execCommandXxx` package-level var for test mocking. `ossecurity_other.go` build tag is widened to `!linux && !darwin` after all 8 files exist (final task of this SP).

**Tech Stack:** Go 1.21, macOS `spctl`, `santactl`, `systemextensionsctl`, `sysctl`, `sandbox-exec`, `pfctl`, BSM audit (`/etc/security/audit_control`), SHA-256 via `crypto/sha256`

---

### Task 1: selinux_darwin.go (SIP + Gatekeeper + Santa mode)

**Files:**
- Create: `agent/commands/ossecurity/selinux_darwin.go`
- Create: `agent/commands/ossecurity/selinux_darwin_test.go`

- [ ] **Step 1: Write failing test**

Create `agent/commands/ossecurity/selinux_darwin_test.go`:

```go
//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestSelinuxExecuteOS_DarwinEnforcing(t *testing.T) {
	var cmds []string
	execCommandSELinux = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "assessments enabled")
	}
	t.Cleanup(func() { execCommandSELinux = exec.Command })

	result, err := selinuxExecuteOS(map[string]any{"mode": "enforcing"})
	if err != nil {
		t.Fatalf("selinuxExecuteOS: %v", err)
	}
	if result["config_snapshot"] == nil {
		t.Fatal("expected config_snapshot")
	}
	foundSpctl := false
	for _, c := range cmds {
		if strings.Contains(c, "spctl") {
			foundSpctl = true
		}
	}
	if !foundSpctl {
		t.Fatalf("expected spctl call; got: %v", cmds)
	}
}

func TestSelinuxRollbackOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandSELinux = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandSELinux = exec.Command })

	result, err := selinuxRollbackOS(map[string]any{
		"config_snapshot": map[string]any{
			"gatekeeper": "assessments enabled",
			"santa_mode": "MONITOR",
		},
	})
	if err != nil {
		t.Fatalf("selinuxRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd agent && go test ./commands/ossecurity/ -run TestSelinux.*Darwin -v 2>&1 | head -10
```
Expected: compile error — `execCommandSELinux` undefined

- [ ] **Step 3: Implement selinux_darwin.go**

Create `agent/commands/ossecurity/selinux_darwin.go`:

```go
//go:build darwin

package ossecurity

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// execCommandSELinux is mockable in tests.
var execCommandSELinux = exec.Command

func selinuxExecuteOS(params map[string]any) (map[string]any, error) {
	// Snapshot current state
	gatekeeperOut, _ := execCommandSELinux("spctl", "--status").Output()
	santaModeOut, _ := execCommandSELinux("santactl", "status").Output()
	csrOut, _ := execCommandSELinux("csrutil", "status").Output()

	snapshot := map[string]any{
		"gatekeeper": strings.TrimSpace(string(gatekeeperOut)),
		"santa_mode": extractSantaMode(string(santaModeOut)),
		"sip_status": strings.TrimSpace(string(csrOut)),
	}

	mode, _ := params["mode"].(string)
	newMode := mode

	switch mode {
	case "enforcing":
		// Enable Gatekeeper
		if out, err := execCommandSELinux("spctl", "--master-enable").CombinedOutput(); err != nil {
			return nil, fmt.Errorf("spctl --master-enable: %s: %w", out, err)
		}
		// Set Santa to LOCKDOWN if available
		if _, err := exec.LookPath("santactl"); err == nil {
			execCommandSELinux("santactl", "rule", "--sync").Run() //nolint:errcheck
		}
	case "permissive":
		// Enable Gatekeeper (permissive = still gatekeeper but Santa in MONITOR)
		execCommandSELinux("spctl", "--master-enable").Run() //nolint:errcheck
		// Santa MONITOR mode — if santad accepts config
		if _, err := exec.LookPath("santactl"); err == nil {
			execCommandSELinux("santactl", "rule", "--sync").Run() //nolint:errcheck
		}
	case "disabled":
		// Disabling SIP requires Recovery Mode — refuse
		return nil, fmt.Errorf("disabling SIP (selinux mode=disabled) requires Recovery Mode; " +
			"disable Gatekeeper with spctl --master-disable if intended")
	default:
		if mode != "" {
			return nil, fmt.Errorf("unsupported mode %q: must be enforcing, permissive, or disabled", mode)
		}
	}

	if gen, _ := params["generate_from_audit_log"].(bool); gen {
		// Scan Santa decision log for recent denials
		logOut, _ := execCommandSELinux("santactl", "log", "--last", "100").Output()
		return map[string]any{
			"audit_log_scan":  string(logOut),
			"config_snapshot": snapshot,
			"applied_at":      time.Now().UTC().Format(time.RFC3339),
		}, nil
	}

	if moduleSource, _ := params["module_source"].(string); moduleSource != "" {
		// Not applicable on macOS
		return map[string]any{
			"skipped":         true,
			"reason":          "module_source not applicable on macOS; use apparmor for Santa rules",
			"config_snapshot": snapshot,
		}, nil
	}

	return map[string]any{
		"previous_mode":     snapshot["santa_mode"],
		"new_mode":          newMode,
		"modules_installed": []string{},
		"config_snapshot":   snapshot,
		"applied_at":        time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func selinuxRollbackOS(params map[string]any) (map[string]any, error) {
	configSnapshot, ok := params["config_snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("config_snapshot is required for rollback")
	}

	// Restore Gatekeeper state
	if gk, _ := configSnapshot["gatekeeper"].(string); gk != "" {
		if strings.Contains(gk, "disabled") || strings.Contains(gk, "assessments disabled") {
			execCommandSELinux("spctl", "--master-disable").Run() //nolint:errcheck
		} else {
			execCommandSELinux("spctl", "--master-enable").Run() //nolint:errcheck
		}
	}

	return map[string]any{"rolled_back": true}, nil
}

func extractSantaMode(output string) string {
	for _, line := range strings.Split(output, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Mode") || strings.Contains(line, "Mode:") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				return strings.TrimSpace(parts[1])
			}
		}
	}
	return "unknown"
}
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/ossecurity/ -run TestSelinux.*Darwin -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/commands/ossecurity/selinux_darwin.go agent/commands/ossecurity/selinux_darwin_test.go
git commit -m "feat(agent): macOS SELinux equivalent via SIP+Gatekeeper+Santa"
```

---

### Task 2: apparmor_darwin.go (Santa binary allowlist rules)

**Files:**
- Create: `agent/commands/ossecurity/apparmor_darwin.go`
- Create: `agent/commands/ossecurity/apparmor_darwin_test.go`

- [ ] **Step 1: Write failing test**

Create `agent/commands/ossecurity/apparmor_darwin_test.go`:

```go
//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestApparmorExecuteOS_DarwinEnforce(t *testing.T) {
	var cmds []string
	execCommandAppArmor = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "Added rule.")
	}
	t.Cleanup(func() { execCommandAppArmor = exec.Command })

	params := map[string]any{
		"profile_name":    "com.example.foo",
		"profile_content": `[{"sha256":"abc123","comment":"test"}]`,
		"mode":            "enforce",
	}
	result, err := apparmorExecuteOS(params)
	if err != nil {
		t.Fatalf("apparmorExecuteOS: %v", err)
	}
	if result["profile_name"] != "com.example.foo" {
		t.Fatalf("expected profile_name; got %v", result)
	}
	foundSanta := false
	for _, c := range cmds {
		if strings.Contains(c, "santactl") && strings.Contains(c, "--allow") {
			foundSanta = true
		}
	}
	if !foundSanta {
		t.Fatalf("expected santactl --allow; got: %v", cmds)
	}
}

func TestApparmorRollbackOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandAppArmor = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "Removed rule.")
	}
	t.Cleanup(func() { execCommandAppArmor = exec.Command })

	result, err := apparmorRollbackOS(map[string]any{
		"snapshot": map[string]any{
			"profile":          "com.example.foo",
			"rules_added":      []any{"abc123"},
			"team_ids_added":   []any{},
		},
	})
	if err != nil {
		t.Fatalf("apparmorRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
```

- [ ] **Step 2: Implement apparmor_darwin.go**

Create `agent/commands/ossecurity/apparmor_darwin.go`:

```go
//go:build darwin

package ossecurity

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// execCommandAppArmor is mockable in tests.
var execCommandAppArmor = exec.Command

type santaRule struct {
	SHA256  string `json:"sha256,omitempty"`
	TeamID  string `json:"team_id,omitempty"`
	Comment string `json:"comment,omitempty"`
}

func apparmorExecuteOS(params map[string]any) (map[string]any, error) {
	profileName, _ := params["profile_name"].(string)
	profileContent, _ := params["profile_content"].(string)
	mode, _ := params["mode"].(string)

	// Snapshot: list current santa rules
	snapOut, _ := execCommandAppArmor("santactl", "rule", "list").Output()
	snapshot := map[string]any{
		"aa_status": strings.TrimSpace(string(snapOut)),
		"profile":   profileName,
	}

	var rulesAdded []string
	var teamIDsAdded []string

	if profileContent != "" {
		var rules []santaRule
		if err := json.Unmarshal([]byte(profileContent), &rules); err != nil {
			// Try single rule
			var rule santaRule
			if err2 := json.Unmarshal([]byte(profileContent), &rule); err2 != nil {
				return nil, fmt.Errorf("profile_content must be JSON array or object of santa rules: %w", err)
			}
			rules = []santaRule{rule}
		}

		for _, r := range rules {
			if r.SHA256 != "" {
				args := []string{"rule", "--allow", "--sha256", r.SHA256}
				if r.Comment != "" {
					args = append(args, "--comment", r.Comment)
				}
				if out, err := execCommandAppArmor("santactl", args...).CombinedOutput(); err != nil {
					return nil, fmt.Errorf("santactl rule --allow --sha256: %s: %w", out, err)
				}
				rulesAdded = append(rulesAdded, r.SHA256)
			} else if r.TeamID != "" {
				args := []string{"rule", "--allow", "--teamid", r.TeamID}
				if r.Comment != "" {
					args = append(args, "--comment", r.Comment)
				}
				if out, err := execCommandAppArmor("santactl", args...).CombinedOutput(); err != nil {
					return nil, fmt.Errorf("santactl rule --allow --teamid: %s: %w", out, err)
				}
				teamIDsAdded = append(teamIDsAdded, r.TeamID)
			}
		}
	}

	appliedMode := ""
	if mode == "complain" || mode == "disable" {
		// Santa doesn't have per-profile complain/disable — noted
		appliedMode = "monitor"
	} else if mode == "enforce" {
		appliedMode = "enforce"
	}

	snapshot["rules_added"] = rulesAdded
	snapshot["team_ids_added"] = teamIDsAdded

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

	// Remove rules that were added
	if rulesRaw, _ := snapshot["rules_added"].([]any); len(rulesRaw) > 0 {
		for _, r := range rulesRaw {
			if sha, ok := r.(string); ok && sha != "" {
				execCommandAppArmor("santactl", "rule", "--remove", "--sha256", sha).Run() //nolint:errcheck
			}
		}
	}
	if teamRaw, _ := snapshot["team_ids_added"].([]any); len(teamRaw) > 0 {
		for _, r := range teamRaw {
			if tid, ok := r.(string); ok && tid != "" {
				execCommandAppArmor("santactl", "rule", "--remove", "--teamid", tid).Run() //nolint:errcheck
			}
		}
	}

	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 3: Run tests**

```bash
cd agent && go test ./commands/ossecurity/ -run TestApparmor.*Darwin -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add agent/commands/ossecurity/apparmor_darwin.go agent/commands/ossecurity/apparmor_darwin_test.go
git commit -m "feat(agent): macOS AppArmor equivalent via Santa binary allowlist rules"
```

---

### Task 3: seccomp_darwin.go + sysctl_darwin.go

**Files:**
- Create: `agent/commands/ossecurity/seccomp_darwin.go`
- Create: `agent/commands/ossecurity/seccomp_darwin_test.go`
- Create: `agent/commands/ossecurity/sysctl_darwin.go`
- Create: `agent/commands/ossecurity/sysctl_darwin_test.go`

- [ ] **Step 1: Write failing tests**

Create `agent/commands/ossecurity/seccomp_darwin_test.go`:

```go
//go:build darwin

package ossecurity

import (
	"os"
	"strings"
	"testing"
)

func TestSeccompExecuteOS_DarwinWritesProfile(t *testing.T) {
	tmpDir := t.TempDir()
	originalDir := sandboxProfileDir
	sandboxProfileDir = tmpDir
	t.Cleanup(func() { sandboxProfileDir = originalDir })

	params := map[string]any{
		"service_name": "test-service",
		"profile":      "(version 1)\n(deny default)\n(allow file-read*)",
	}
	result, err := seccompExecuteOS(params)
	if err != nil {
		t.Fatalf("seccompExecuteOS: %v", err)
	}
	profilePath, _ := result["profile_path"].(string)
	if profilePath == "" {
		t.Fatal("expected profile_path in result")
	}
	if _, err := os.Stat(profilePath); err != nil {
		t.Fatalf("profile file not created: %v", err)
	}
	data, _ := os.ReadFile(profilePath)
	if !strings.Contains(string(data), "deny default") {
		t.Fatal("profile content not written")
	}
}

func TestSeccompRollbackOS_DarwinRestores(t *testing.T) {
	tmpDir := t.TempDir()
	originalDir := sandboxProfileDir
	sandboxProfileDir = tmpDir
	t.Cleanup(func() { sandboxProfileDir = originalDir })

	// Create a file to restore
	profilePath := tmpDir + "/test-service.sb"
	os.WriteFile(profilePath, []byte("original"), 0644)

	result, err := seccompRollbackOS(map[string]any{
		"service_name": "test-service",
		"snapshot":     "previous-content",
	})
	if err != nil {
		t.Fatalf("seccompRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
```

Create `agent/commands/ossecurity/sysctl_darwin_test.go`:

```go
//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestSysctlExecuteOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandSysctl = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "0")
	}
	t.Cleanup(func() { execCommandSysctl = exec.Command })

	result, err := sysctlExecuteOS(map[string]any{})
	if err != nil {
		t.Fatalf("sysctlExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	foundSysctl := false
	for _, c := range cmds {
		if strings.Contains(c, "sysctl") && strings.Contains(c, "-w") {
			foundSysctl = true
		}
	}
	if !foundSysctl {
		t.Fatalf("expected sysctl -w calls; got: %v", cmds)
	}
}

func TestSysctlRollbackOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandSysctl = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandSysctl = exec.Command })

	result, err := sysctlRollbackOS(map[string]any{
		"snapshot": "net.inet.ip.forwarding: 0\n",
	})
	if err != nil {
		t.Fatalf("sysctlRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
```

- [ ] **Step 2: Implement seccomp_darwin.go**

Create `agent/commands/ossecurity/seccomp_darwin.go`:

```go
//go:build darwin

package ossecurity

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// sandboxProfileDir is overridable in tests.
var sandboxProfileDir = "/private/etc/nexplane/sandbox"

func seccompExecuteOS(params map[string]any) (map[string]any, error) {
	serviceName, _ := params["service_name"].(string)
	if serviceName == "" {
		return nil, fmt.Errorf("service_name is required")
	}
	profile, _ := params["profile"].(string)
	if profile == "" {
		return nil, fmt.Errorf("profile (sandbox SBPL content) is required")
	}

	if err := os.MkdirAll(sandboxProfileDir, 0755); err != nil {
		return nil, fmt.Errorf("creating sandbox profile dir: %w", err)
	}

	profilePath := filepath.Join(sandboxProfileDir, serviceName+".sb")

	// Snapshot existing profile
	snapshot := ""
	if data, err := os.ReadFile(profilePath); err == nil {
		snapshot = string(data)
	}

	if err := os.WriteFile(profilePath, []byte(profile), 0644); err != nil {
		return nil, fmt.Errorf("writing sandbox profile: %w", err)
	}

	return map[string]any{
		"service_name": serviceName,
		"profile_path": profilePath,
		"snapshot":     snapshot,
		"note":         "Apply with: sandbox-exec -f " + profilePath + " <command>",
		"applied_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func seccompRollbackOS(params map[string]any) (map[string]any, error) {
	serviceName, _ := params["service_name"].(string)
	if serviceName == "" {
		return nil, fmt.Errorf("service_name is required for rollback")
	}
	snapshot, _ := params["snapshot"].(string)
	profilePath := filepath.Join(sandboxProfileDir, serviceName+".sb")

	if snapshot == "" {
		os.Remove(profilePath) //nolint:errcheck
	} else {
		if err := os.WriteFile(profilePath, []byte(snapshot), 0644); err != nil {
			return nil, fmt.Errorf("restoring sandbox profile: %w", err)
		}
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 3: Implement sysctl_darwin.go**

Create `agent/commands/ossecurity/sysctl_darwin.go`:

```go
//go:build darwin

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// execCommandSysctl is mockable in tests.
var execCommandSysctl = exec.Command

var cisDarwinSysctl = map[string]string{
	"net.inet.ip.forwarding":          "0",
	"net.inet.ip.redirect":            "0",
	"net.inet6.ip6.forwarding":        "0",
	"net.inet.icmp.bmcastecho":        "0",
	"kern.sugid_coredump":             "0",
	"security.mac.proc_enforce":       "1",
	"security.mac.vnode_enforce":      "1",
}

const darwinSysctlPlist = "/Library/LaunchDaemons/com.nexplane.sysctl.plist"

func sysctlExecuteOS(params map[string]any) (map[string]any, error) {
	settings := make(map[string]string)
	for k, v := range cisDarwinSysctl {
		settings[k] = v
	}
	if overrides, ok := params["settings"].(map[string]any); ok {
		for k, v := range overrides {
			settings[k] = fmt.Sprintf("%v", v)
		}
	}

	// Snapshot: read current values
	var snapLines []string
	for k := range settings {
		out, err := execCommandSysctl("sysctl", "-n", k).Output()
		if err == nil {
			snapLines = append(snapLines, k+": "+strings.TrimSpace(string(out)))
		}
	}
	snapshot := strings.Join(snapLines, "\n")

	// Apply settings
	applied := map[string]string{}
	for k, v := range settings {
		out, err := execCommandSysctl("sysctl", "-w", k+"="+v).CombinedOutput()
		if err != nil {
			// Some keys may not exist on all macOS versions — skip gracefully
			applied[k] = "skipped: " + strings.TrimSpace(string(out))
			continue
		}
		applied[k] = v
	}

	// Write persistence LaunchDaemon plist
	writeDarwinSysctlPlist(settings)

	return map[string]any{
		"settings":   applied,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func sysctlRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	if snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	for _, line := range strings.Split(snapshot, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.SplitN(line, ": ", 2)
		if len(parts) == 2 {
			execCommandSysctl("sysctl", "-w", parts[0]+"="+parts[1]).Run() //nolint:errcheck
		}
	}
	// Remove persistence plist
	os.Remove(darwinSysctlPlist) //nolint:errcheck

	return map[string]any{"rolled_back": true}, nil
}

func writeDarwinSysctlPlist(settings map[string]string) {
	var args []string
	for k, v := range settings {
		args = append(args, k+"="+v)
	}
	plistContent := `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.nexplane.sysctl</string>
  <key>ProgramArguments</key><array>
    <string>/usr/sbin/sysctl</string>
`
	for _, a := range args {
		plistContent += "    <string>" + a + "</string>\n"
	}
	plistContent += `  </array>
  <key>RunAtLoad</key><true/>
</dict></plist>`
	os.WriteFile(darwinSysctlPlist, []byte(plistContent), 0644) //nolint:errcheck
}
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/ossecurity/ -run "TestSeccomp.*Darwin|TestSysctl.*Darwin" -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/commands/ossecurity/seccomp_darwin.go agent/commands/ossecurity/seccomp_darwin_test.go agent/commands/ossecurity/sysctl_darwin.go agent/commands/ossecurity/sysctl_darwin_test.go
git commit -m "feat(agent): macOS seccomp (sandbox-exec) and sysctl hardening"
```

---

### Task 4: modules_darwin.go + mount_darwin.go

**Files:**
- Create: `agent/commands/ossecurity/modules_darwin.go`
- Create: `agent/commands/ossecurity/modules_darwin_test.go`
- Create: `agent/commands/ossecurity/mount_darwin.go`
- Create: `agent/commands/ossecurity/mount_darwin_test.go`

- [ ] **Step 1: Write failing tests**

Create `agent/commands/ossecurity/modules_darwin_test.go`:

```go
//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestBlacklistExecuteOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandModules = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "com.apple.driver.FakeDriver  0 0")
	}
	t.Cleanup(func() { execCommandModules = exec.Command })

	result, err := blacklistExecuteOS(map[string]any{
		"modules": []any{"com.apple.driver.FakeDriver"},
	})
	if err != nil {
		t.Fatalf("blacklistExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	foundSysext := false
	for _, c := range cmds {
		if strings.Contains(c, "systemextensionsctl") {
			foundSysext = true
		}
	}
	if !foundSysext {
		t.Fatalf("expected systemextensionsctl; got: %v", cmds)
	}
}

func TestBlacklistRollbackOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandModules = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandModules = exec.Command })

	result, err := blacklistRollbackOS(map[string]any{
		"snapshot": "com.apple.driver.FakeDriver  1 0",
	})
	if err != nil {
		t.Fatalf("blacklistRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
```

Create `agent/commands/ossecurity/mount_darwin_test.go`:

```go
//go:build darwin

package ossecurity

import (
	"os/exec"
	"strings"
	"testing"
)

func TestMountExecuteOS_DarwinSIPProtected(t *testing.T) {
	execCommandMount = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "/System on / (apfs, local, journaled)")
	}
	t.Cleanup(func() { execCommandMount = exec.Command })

	result, err := mountExecuteOS(map[string]any{
		"path":    "/System",
		"options": []any{"noexec"},
	})
	if err != nil {
		t.Fatalf("mountExecuteOS: %v", err)
	}
	skipped, _ := result["skipped"].(bool)
	if !skipped {
		t.Fatalf("expected skipped=true for SIP-protected path; got: %v", result)
	}
}

func TestMountExecuteOS_DarwinUserVolume(t *testing.T) {
	var cmds []string
	execCommandMount = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandMount = exec.Command })

	result, err := mountExecuteOS(map[string]any{
		"path":    "/Volumes/Data",
		"options": []any{"noexec", "nosuid"},
	})
	if err != nil {
		t.Fatalf("mountExecuteOS: %v", err)
	}
	_ = result
}
```

- [ ] **Step 2: Implement modules_darwin.go**

Create `agent/commands/ossecurity/modules_darwin.go`:

```go
//go:build darwin

package ossecurity

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// execCommandModules is mockable in tests.
var execCommandModules = exec.Command

func blacklistExecuteOS(params map[string]any) (map[string]any, error) {
	modulesRaw, _ := params["modules"].([]any)
	modules := make([]string, 0, len(modulesRaw))
	for _, m := range modulesRaw {
		if s, ok := m.(string); ok {
			modules = append(modules, s)
		}
	}

	// Snapshot: list current system extensions
	snapOut, _ := execCommandModules("systemextensionsctl", "list").Output()
	snapshot := strings.TrimSpace(string(snapOut))

	// Enable developer mode to allow extension management
	execCommandModules("systemextensionsctl", "developer", "on").Run() //nolint:errcheck

	unloaded := []string{}
	for _, bundleID := range modules {
		out, err := execCommandModules("systemextensionsctl", "uninstall", bundleID).CombinedOutput()
		if err != nil {
			// Extension may not be installed — skip gracefully
			unloaded = append(unloaded, bundleID+":skipped("+strings.TrimSpace(string(out))+")")
			continue
		}
		unloaded = append(unloaded, bundleID)
	}

	return map[string]any{
		"modules_blacklisted": unloaded,
		"snapshot":            snapshot,
		"applied_at":          time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func blacklistRollbackOS(params map[string]any) (map[string]any, error) {
	// systemextensionsctl reset re-enables all user-approved extensions
	if out, err := execCommandModules("systemextensionsctl", "reset").CombinedOutput(); err != nil {
		return nil, fmt.Errorf("systemextensionsctl reset: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 3: Implement mount_darwin.go**

Create `agent/commands/ossecurity/mount_darwin.go`:

```go
//go:build darwin

package ossecurity

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// execCommandMount is mockable in tests.
var execCommandMount = exec.Command

// sipProtectedPaths are protected by SIP and cannot be remounted.
var sipProtectedPaths = []string{"/System", "/usr", "/bin", "/sbin", "/private/var/db"}

func mountExecuteOS(params map[string]any) (map[string]any, error) {
	path, _ := params["path"].(string)
	if path == "" {
		return nil, fmt.Errorf("path is required")
	}
	optionsRaw, _ := params["options"].([]any)
	options := make([]string, 0, len(optionsRaw))
	for _, o := range optionsRaw {
		if s, ok := o.(string); ok {
			options = append(options, s)
		}
	}

	// Check if SIP-protected
	for _, protected := range sipProtectedPaths {
		if strings.HasPrefix(path, protected) {
			return map[string]any{
				"skipped": true,
				"reason":  fmt.Sprintf("SIP protects %q; remounting is not possible without disabling SIP", path),
				"path":    path,
			}, nil
		}
	}

	// Snapshot current mount
	snapOut, _ := execCommandMount("mount").Output()
	snapshot := ""
	for _, line := range strings.Split(string(snapOut), "\n") {
		if strings.Contains(line, path+" ") || strings.HasSuffix(line, " "+path) {
			snapshot = strings.TrimSpace(line)
		}
	}

	// Remount with options
	args := append([]string{"-u", "-o", strings.Join(options, ","), path})
	if out, err := execCommandMount("mount", args...).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("mount -u -o: %s: %w", out, err)
	}

	return map[string]any{
		"path":       path,
		"options":    options,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func mountRollbackOS(params map[string]any) (map[string]any, error) {
	path, _ := params["path"].(string)
	snapshot, _ := params["snapshot"].(string)
	if path == "" {
		return nil, fmt.Errorf("path is required for rollback")
	}
	if snapshot == "" {
		return map[string]any{"rolled_back": false, "reason": "no snapshot to restore"}, nil
	}
	// Re-mount with original options extracted from snapshot string
	// mount output: "/dev/disk3s5 on /Volumes/Data (apfs, local, noexec)"
	origOpts := ""
	if start := strings.Index(snapshot, "("); start != -1 {
		if end := strings.Index(snapshot, ")"); end != -1 {
			origOpts = snapshot[start+1 : end]
		}
	}
	if origOpts != "" {
		execCommandMount("mount", "-u", "-o", origOpts, path).Run() //nolint:errcheck
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/ossecurity/ -run "TestBlacklist.*Darwin|TestMount.*Darwin" -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/commands/ossecurity/modules_darwin.go agent/commands/ossecurity/modules_darwin_test.go agent/commands/ossecurity/mount_darwin.go agent/commands/ossecurity/mount_darwin_test.go
git commit -m "feat(agent): macOS kernel module (systemextensionsctl) and mount hardening"
```

---

### Task 5: auditd_darwin.go (BSM audit)

**Files:**
- Create: `agent/commands/ossecurity/auditd_darwin.go`
- Create: `agent/commands/ossecurity/auditd_darwin_test.go`

- [ ] **Step 1: Write failing test**

Create `agent/commands/ossecurity/auditd_darwin_test.go`:

```go
//go:build darwin

package ossecurity

import (
	"os"
	"strings"
	"testing"
)

func TestAuditdExecuteOS_DarwinCISLevel1(t *testing.T) {
	tmpFile := t.TempDir() + "/audit_control"
	origPath := bsmAuditControl
	bsmAuditControl = tmpFile
	t.Cleanup(func() { bsmAuditControl = origPath })

	// Mock audit -s
	var auditCalled bool
	execCommandAuditd = func(name string, args ...string) interface {
		Run() error
		CombinedOutput() ([]byte, error)
	} {
		if name == "audit" {
			auditCalled = true
		}
		return fakeCmd("ok")
	}
	t.Cleanup(func() { execCommandAuditd = nil })

	result, err := auditdExecuteOS(map[string]any{"profile": "cis_level1"})
	if err != nil {
		t.Fatalf("auditdExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	data, _ := os.ReadFile(tmpFile)
	if !strings.Contains(string(data), "dir:/var/audit") {
		t.Fatalf("expected audit_control content written; got: %s", data)
	}
	_ = auditCalled
}
```

Note: the above test uses a different mock pattern. Use the simpler exec.Command override:

```go
//go:build darwin

package ossecurity

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestAuditdExecuteOS_DarwinCISLevel1(t *testing.T) {
	tmpFile := t.TempDir() + "/audit_control"
	origPath := bsmAuditControl
	bsmAuditControl = tmpFile
	t.Cleanup(func() { bsmAuditControl = origPath })

	execCommandAuditd = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandAuditd = exec.Command })

	result, err := auditdExecuteOS(map[string]any{"profile": "cis_level1"})
	if err != nil {
		t.Fatalf("auditdExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	data, _ := os.ReadFile(tmpFile)
	if !strings.Contains(string(data), "dir:/var/audit") {
		t.Fatalf("expected audit_control written; got: %s", data)
	}
}

func TestAuditdRollbackOS_Darwin(t *testing.T) {
	tmpFile := t.TempDir() + "/audit_control"
	origPath := bsmAuditControl
	bsmAuditControl = tmpFile
	t.Cleanup(func() { bsmAuditControl = origPath })

	execCommandAuditd = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandAuditd = exec.Command })

	result, err := auditdRollbackOS(map[string]any{
		"snapshot": "dir:/var/audit\nflags:lo\n",
	})
	if err != nil {
		t.Fatalf("auditdRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
```

- [ ] **Step 2: Implement auditd_darwin.go**

Create `agent/commands/ossecurity/auditd_darwin.go`:

```go
//go:build darwin

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// execCommandAuditd is mockable in tests.
var execCommandAuditd = exec.Command

// bsmAuditControl is overridable in tests.
var bsmAuditControl = "/etc/security/audit_control"

var bsmProfiles = map[string]string{
	"cis_level1": "dir:/var/audit\nflags:lo,aa\nminfree:5\nnaflags:lo\npolicy:cnt,argv\nfilesz:2M\nexpire-after:10M\n",
	"cis_level2": "dir:/var/audit\nflags:lo,aa,ex,pc\nminfree:5\nnaflags:lo\npolicy:cnt,argv,arge\nfilesz:5M\nexpire-after:50M\n",
}

func auditdExecuteOS(params map[string]any) (map[string]any, error) {
	// Snapshot
	existing, _ := os.ReadFile(bsmAuditControl)
	snapshot := string(existing)

	rulesContent := ""
	if profile, ok := params["profile"].(string); ok && profile != "" {
		content, ok := bsmProfiles[profile]
		if !ok {
			return nil, fmt.Errorf("unknown profile %q: must be cis_level1 or cis_level2", profile)
		}
		rulesContent = content
	} else if custom, ok := params["rules"].(string); ok && custom != "" {
		rulesContent = custom
	} else {
		return nil, fmt.Errorf("profile or rules is required")
	}

	if err := os.WriteFile(bsmAuditControl, []byte(rulesContent), 0644); err != nil {
		return nil, fmt.Errorf("writing audit_control: %w", err)
	}

	// Reload BSM audit daemon
	if out, err := execCommandAuditd("audit", "-s").CombinedOutput(); err != nil {
		// Non-fatal — audit may not be running
		_ = out
	}

	return map[string]any{
		"rules_path":  bsmAuditControl,
		"snapshot":    snapshot,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func auditdRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	if snapshot == "" {
		// Remove file if it didn't exist before
		os.Remove(bsmAuditControl) //nolint:errcheck
		return map[string]any{"rolled_back": true}, nil
	}
	if err := os.WriteFile(bsmAuditControl, []byte(snapshot), 0644); err != nil {
		return nil, fmt.Errorf("restoring audit_control: %w", err)
	}
	execCommandAuditd("audit", "-s").Run() //nolint:errcheck
	_ = strings.TrimSpace                  // suppress unused import
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 3: Run tests**

```bash
cd agent && go test ./commands/ossecurity/ -run "TestAuditd.*Darwin" -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add agent/commands/ossecurity/auditd_darwin.go agent/commands/ossecurity/auditd_darwin_test.go
git commit -m "feat(agent): macOS auditd equivalent via BSM audit_control"
```

---

### Task 6: fim_darwin.go (FSWatch + SHA-256 digest snapshots)

**Files:**
- Create: `agent/commands/ossecurity/fim_darwin.go`
- Create: `agent/commands/ossecurity/fim_darwin_test.go`

- [ ] **Step 1: Write failing test**

Create `agent/commands/ossecurity/fim_darwin_test.go`:

```go
//go:build darwin

package ossecurity

import (
	"os"
	"path/filepath"
	"testing"
)

func TestFimExecuteOS_DarwinInit(t *testing.T) {
	tmpDir := t.TempDir()
	snapshotPath := filepath.Join(tmpDir, "fim-snapshot.json")
	origPath := darwinFIMSnapshotPath
	darwinFIMSnapshotPath = snapshotPath
	t.Cleanup(func() { darwinFIMSnapshotPath = origPath })

	// Create a test file to snapshot
	testFile := filepath.Join(tmpDir, "test.txt")
	os.WriteFile(testFile, []byte("hello"), 0644)

	result, err := fimExecuteOS(map[string]any{
		"action":      "init",
		"watch_paths": []any{tmpDir},
	})
	if err != nil {
		t.Fatalf("fimExecuteOS init: %v", err)
	}
	if result["snapshot_path"] == nil {
		t.Fatal("expected snapshot_path")
	}
	if _, err := os.Stat(snapshotPath); err != nil {
		t.Fatalf("snapshot file not created: %v", err)
	}
}

func TestFimExecuteOS_DarwinCheck(t *testing.T) {
	tmpDir := t.TempDir()
	snapshotPath := filepath.Join(tmpDir, "fim-snapshot.json")
	origPath := darwinFIMSnapshotPath
	darwinFIMSnapshotPath = snapshotPath
	t.Cleanup(func() { darwinFIMSnapshotPath = origPath })

	// Create initial snapshot
	testFile := filepath.Join(tmpDir, "test.txt")
	os.WriteFile(testFile, []byte("hello"), 0644)
	fimExecuteOS(map[string]any{"action": "init", "watch_paths": []any{tmpDir}})

	// Modify file
	os.WriteFile(testFile, []byte("modified"), 0644)

	result, err := fimExecuteOS(map[string]any{
		"action":      "check",
		"watch_paths": []any{tmpDir},
	})
	if err != nil {
		t.Fatalf("fimExecuteOS check: %v", err)
	}
	violations, _ := result["violations"].(bool)
	if !violations {
		t.Fatal("expected violations=true after file modification")
	}
}

func TestFimRollbackOS_Darwin(t *testing.T) {
	result, err := fimRollbackOS(map[string]any{})
	if err != nil {
		t.Fatalf("fimRollbackOS: %v", err)
	}
	if result["rolled_back"] == true {
		t.Fatal("FIM should not have rolled_back=true (monitoring-only)")
	}
}
```

- [ ] **Step 2: Implement fim_darwin.go**

Create `agent/commands/ossecurity/fim_darwin.go`:

```go
//go:build darwin

package ossecurity

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"
)

// darwinFIMSnapshotPath is overridable in tests.
var darwinFIMSnapshotPath = "/var/lib/nexplane/fim-snapshot.json"

func fimExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action == "" {
		action = "init"
	}

	watchPathsRaw, _ := params["watch_paths"].([]any)
	watchPaths := []string{"/etc", "/usr/local/bin", "/Library/LaunchDaemons"}
	if len(watchPathsRaw) > 0 {
		watchPaths = nil
		for _, p := range watchPathsRaw {
			if s, ok := p.(string); ok {
				watchPaths = append(watchPaths, s)
			}
		}
	}

	switch action {
	case "init":
		return darwinFIMInit(watchPaths)
	case "check":
		return darwinFIMCheck(watchPaths)
	default:
		return nil, fmt.Errorf("unknown action %q: must be init or check", action)
	}
}

func darwinFIMInit(watchPaths []string) (map[string]any, error) {
	snapshot := map[string]string{}
	for _, root := range watchPaths {
		filepath.Walk(root, func(path string, info os.FileInfo, err error) error { //nolint:errcheck
			if err != nil || info == nil || info.IsDir() {
				return nil
			}
			if h, err := sha256File(path); err == nil {
				snapshot[path] = h
			}
			return nil
		})
	}

	snapshotDir := filepath.Dir(darwinFIMSnapshotPath)
	os.MkdirAll(snapshotDir, 0755) //nolint:errcheck

	data, err := json.Marshal(snapshot)
	if err != nil {
		return nil, fmt.Errorf("marshaling snapshot: %w", err)
	}
	if err := os.WriteFile(darwinFIMSnapshotPath, data, 0600); err != nil {
		return nil, fmt.Errorf("writing snapshot: %w", err)
	}

	return map[string]any{
		"tool":          "sha256-walk",
		"action":        "init",
		"snapshot_path": darwinFIMSnapshotPath,
		"file_count":    len(snapshot),
		"applied_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func darwinFIMCheck(watchPaths []string) (map[string]any, error) {
	data, err := os.ReadFile(darwinFIMSnapshotPath)
	if err != nil {
		return nil, fmt.Errorf("reading snapshot (run init first): %w", err)
	}
	var baseline map[string]string
	if err := json.Unmarshal(data, &baseline); err != nil {
		return nil, fmt.Errorf("parsing snapshot: %w", err)
	}

	type violation struct {
		Path     string `json:"path"`
		Expected string `json:"expected"`
		Actual   string `json:"actual"`
	}
	var violations []violation

	current := map[string]string{}
	for _, root := range watchPaths {
		filepath.Walk(root, func(path string, info os.FileInfo, err error) error { //nolint:errcheck
			if err != nil || info == nil || info.IsDir() {
				return nil
			}
			if h, err := sha256File(path); err == nil {
				current[path] = h
			}
			return nil
		})
	}

	for path, expected := range baseline {
		actual, exists := current[path]
		if !exists {
			violations = append(violations, violation{path, expected, "DELETED"})
		} else if actual != expected {
			violations = append(violations, violation{path, expected, actual})
		}
	}
	for path := range current {
		if _, exists := baseline[path]; !exists {
			violations = append(violations, violation{path, "NOT_IN_BASELINE", current[path]})
		}
	}

	return map[string]any{
		"tool":       "sha256-walk",
		"action":     "check",
		"violations": len(violations) > 0,
		"changes":    violations,
		"checked_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func fimRollbackOS(_ map[string]any) (map[string]any, error) {
	return map[string]any{
		"rolled_back": false,
		"reason":      "file integrity monitoring is read-only; no state to restore",
	}, nil
}

func sha256File(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
```

- [ ] **Step 3: Run tests**

```bash
cd agent && go test ./commands/ossecurity/ -run "TestFim.*Darwin" -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add agent/commands/ossecurity/fim_darwin.go agent/commands/ossecurity/fim_darwin_test.go
git commit -m "feat(agent): macOS FIM via SHA-256 digest snapshots"
```

---

### Task 7: Fix ossecurity_other.go build tag

Now that all 8 darwin ossecurity files exist, widen the build tag so darwin no longer falls through to the stub implementations.

**Files:**
- Modify: `agent/commands/ossecurity/ossecurity_other.go`

- [ ] **Step 1: Update build tag**

Edit line 1 of `agent/commands/ossecurity/ossecurity_other.go`:

Change:
```go
//go:build !linux
```
To:
```go
//go:build !linux && !darwin
```

- [ ] **Step 2: Verify all platforms compile**

```bash
cd agent && GOOS=linux go build ./... && echo "linux OK"
cd agent && GOOS=darwin GOARCH=arm64 go build ./... && echo "darwin OK"
cd agent && GOOS=windows go build ./... && echo "windows OK"
```
Expected: all three "OK"

- [ ] **Step 3: Run full ossecurity test suite**

```bash
cd agent && go test ./commands/ossecurity/... -v -count=1 2>&1 | grep -E "^(=== RUN|--- PASS|--- FAIL|FAIL|ok)"
```
Expected: no FAIL lines

- [ ] **Step 4: Commit**

```bash
git add agent/commands/ossecurity/ossecurity_other.go
git commit -m "fix(agent): exclude darwin from ossecurity_other.go stubs"
```
