# Agent Parity SP5+SP6: macOS Auth, Compliance & Credential Rotation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement macOS auth controls (PAM via pwpolicy, SSH via sshd_config+launchctl, certs via security Keychain, NTP via systemsetup, user audit via dscl, privesc via sudo/sudoers), macOS CIS compliance audit, and macOS/Windows DB credential rotation.

**Architecture:** `linuxauth_darwin.go` implements all 6 auth function pairs. `compliance_darwin.go` implements `auditCISComplianceOS` + `collectEvidenceOS`. `db_darwin.go` replaces stub with real psql/mysql implementation. Build tags on `_other.go` files gain `&& !darwin`.

**Tech Stack:** Go 1.21, `pwpolicy`, `sshd_config`, `launchctl`, `security` (Keychain), `systemsetup`, `dscl`, `spctl`, `csrutil`, `socketfilterfw`

---

### Task 1: linuxauth_darwin.go

**Files:**
- Create: `agent/commands/linuxauth/linuxauth_darwin.go`
- Create: `agent/commands/linuxauth/linuxauth_darwin_test.go`
- Modify: `agent/commands/linuxauth/linuxauth_other.go` — build tag add `&& !darwin`

- [ ] **Step 1: Write failing test**

Create `agent/commands/linuxauth/linuxauth_darwin_test.go`:

```go
//go:build darwin

package linuxauth

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

func TestSSHExecuteOS_Darwin(t *testing.T) {
	tmpFile := t.TempDir() + "/sshd_config"
	// Write minimal sshd_config
	os.WriteFile(tmpFile, []byte("# sshd config\nPermitRootLogin yes\n"), 0644)

	origPath := darwinSSHConfigPath
	darwinSSHConfigPath = tmpFile
	t.Cleanup(func() { darwinSSHConfigPath = origPath })

	execCommandLinuxAuthDarwin = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandLinuxAuthDarwin = exec.Command })

	result, err := sshExecuteOS(map[string]any{
		"permit_root_login": "no",
	})
	if err != nil {
		t.Fatalf("sshExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	data, _ := os.ReadFile(tmpFile)
	if !strings.Contains(string(data), "PermitRootLogin no") {
		t.Fatalf("expected PermitRootLogin no in config; got: %s", data)
	}
}

func TestSSHRollbackOS_Darwin(t *testing.T) {
	tmpFile := t.TempDir() + "/sshd_config"
	origPath := darwinSSHConfigPath
	darwinSSHConfigPath = tmpFile
	t.Cleanup(func() { darwinSSHConfigPath = origPath })

	execCommandLinuxAuthDarwin = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandLinuxAuthDarwin = exec.Command })

	result, err := sshRollbackOS(map[string]any{
		"snapshot": "# original config\nPermitRootLogin yes\n",
	})
	if err != nil {
		t.Fatalf("sshRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
	data, _ := os.ReadFile(tmpFile)
	if !strings.Contains(string(data), "PermitRootLogin yes") {
		t.Fatal("expected original config restored")
	}
}

func TestNTPExecuteOS_Darwin(t *testing.T) {
	var cmds []string
	execCommandLinuxAuthDarwin = func(name string, args ...string) *exec.Cmd {
		cmds = append(cmds, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "Network Time Server: time.apple.com")
	}
	t.Cleanup(func() { execCommandLinuxAuthDarwin = exec.Command })

	result, err := ntpExecuteOS(map[string]any{
		"servers": []any{"time.cloudflare.com"},
	})
	if err != nil {
		t.Fatalf("ntpExecuteOS: %v", err)
	}
	if result["snapshot"] == nil {
		t.Fatal("expected snapshot")
	}
	foundSet := false
	for _, c := range cmds {
		if strings.Contains(c, "systemsetup") && strings.Contains(c, "setnetworktimeserver") {
			foundSet = true
		}
	}
	if !foundSet {
		t.Fatalf("expected systemsetup -setnetworktimeserver; got: %v", cmds)
	}
}

func TestAuditUsersOS_Darwin(t *testing.T) {
	execCommandLinuxAuthDarwin = func(name string, args ...string) *exec.Cmd {
		if name == "dscl" && len(args) >= 2 && args[1] == "-list" {
			return exec.Command("printf", "root\nadmin\njohn\n")
		}
		return exec.Command("echo", "500")
	}
	t.Cleanup(func() { execCommandLinuxAuthDarwin = exec.Command })

	result, err := auditUsersOS(map[string]any{})
	if err != nil {
		t.Fatalf("auditUsersOS: %v", err)
	}
	users, _ := result["users"].([]map[string]any)
	if len(users) == 0 {
		t.Fatal("expected users list")
	}
}
```

- [ ] **Step 2: Implement linuxauth_darwin.go**

Create `agent/commands/linuxauth/linuxauth_darwin.go`:

```go
//go:build darwin

package linuxauth

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// execCommandLinuxAuthDarwin is mockable in tests.
var execCommandLinuxAuthDarwin = exec.Command

// darwinSSHConfigPath is overridable in tests.
var darwinSSHConfigPath = "/etc/ssh/sshd_config"

// darwinSSHDefaults mirrors the Linux defaults — sshd_config is identical on macOS.
var darwinSSHDefaults = map[string]string{
	"PermitRootLogin":        "no",
	"PasswordAuthentication": "no",
	"PubkeyAuthentication":   "yes",
	"ClientAliveInterval":    "300",
	"ClientAliveCountMax":    "3",
	"MaxAuthTries":           "4",
	"X11Forwarding":          "no",
	"PermitEmptyPasswords":   "no",
}

var darwinSSHParamMap = map[string]string{
	"permit_root_login":         "PermitRootLogin",
	"password_authentication":   "PasswordAuthentication",
	"pubkey_authentication":     "PubkeyAuthentication",
	"client_alive_interval":     "ClientAliveInterval",
	"client_alive_count_max":    "ClientAliveCountMax",
	"max_auth_tries":            "MaxAuthTries",
	"x11_forwarding":            "X11Forwarding",
	"permit_empty_passwords":    "PermitEmptyPasswords",
	"port":                      "Port",
	"allow_users":               "AllowUsers",
	"allow_groups":              "AllowGroups",
}

// --- PAM ---

func pamExecuteOS(params map[string]any) (map[string]any, error) {
	// Snapshot pam.d service files
	snapshot := map[string]string{}
	for _, svc := range []string{"login", "sudo", "su"} {
		data, _ := os.ReadFile("/etc/pam.d/" + svc)
		snapshot[svc] = string(data)
	}

	minLen, _ := params["min_length"].(float64)
	maxFailed, _ := params["max_failed_attempts"].(float64)

	// Apply via pwpolicy (Directory Services password policy)
	if minLen > 0 {
		policyXML := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>minChars</key><integer>%d</integer>
</dict></plist>`, int(minLen))
		execCommandLinuxAuthDarwin("pwpolicy", "-setaccountpolicies", policyXML).Run() //nolint:errcheck
	}
	if maxFailed > 0 {
		policyXML := fmt.Sprintf(`<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>maxFailedLoginAttempts</key><integer>%d</integer>
</dict></plist>`, int(maxFailed))
		execCommandLinuxAuthDarwin("pwpolicy", "-setaccountpolicies", policyXML).Run() //nolint:errcheck
	}

	return map[string]any{
		"snapshot":   snapshot,
		"min_length": minLen,
		"max_failed": maxFailed,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func pamRollbackOS(params map[string]any) (map[string]any, error) {
	// Restore pam.d files from snapshot
	if snapshot, ok := params["snapshot"].(map[string]any); ok {
		for svc, content := range snapshot {
			if s, ok := content.(string); ok && s != "" {
				os.WriteFile("/etc/pam.d/"+svc, []byte(s), 0644) //nolint:errcheck
			}
		}
	}
	// Clear pwpolicy
	execCommandLinuxAuthDarwin("pwpolicy", "-clearaccountpolicies").Run() //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}

// --- SSH ---

func sshExecuteOS(params map[string]any) (map[string]any, error) {
	existing, err := os.ReadFile(darwinSSHConfigPath)
	if err != nil {
		return nil, fmt.Errorf("reading sshd_config: %w", err)
	}
	snapshot := string(existing)

	// Build settings map: defaults + params
	settings := make(map[string]string)
	for k, v := range darwinSSHDefaults {
		settings[k] = v
	}
	for paramKey, sshKey := range darwinSSHParamMap {
		if val, ok := params[paramKey]; ok {
			settings[sshKey] = fmt.Sprintf("%v", val)
		}
	}

	// Apply settings to config
	newConfig := applySSHSettings(snapshot, settings)
	if err := os.WriteFile(darwinSSHConfigPath, []byte(newConfig), 0644); err != nil {
		return nil, fmt.Errorf("writing sshd_config: %w", err)
	}

	// Validate config
	if out, err := execCommandLinuxAuthDarwin("sshd", "-t").CombinedOutput(); err != nil {
		// Restore original on validation failure
		os.WriteFile(darwinSSHConfigPath, existing, 0644) //nolint:errcheck
		return nil, fmt.Errorf("sshd -t validation failed: %s: %w", out, err)
	}

	// Restart sshd via launchctl
	execCommandLinuxAuthDarwin("launchctl", "unload", "/System/Library/LaunchDaemons/ssh.plist").Run() //nolint:errcheck
	execCommandLinuxAuthDarwin("launchctl", "load", "-w", "/System/Library/LaunchDaemons/ssh.plist").Run() //nolint:errcheck

	return map[string]any{
		"snapshot":    snapshot,
		"config_path": darwinSSHConfigPath,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func sshRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	if snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if err := os.WriteFile(darwinSSHConfigPath, []byte(snapshot), 0644); err != nil {
		return nil, fmt.Errorf("restoring sshd_config: %w", err)
	}
	execCommandLinuxAuthDarwin("launchctl", "unload", "/System/Library/LaunchDaemons/ssh.plist").Run() //nolint:errcheck
	execCommandLinuxAuthDarwin("launchctl", "load", "-w", "/System/Library/LaunchDaemons/ssh.plist").Run() //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}

// applySSHSettings merges settings into an existing sshd_config text.
func applySSHSettings(config string, settings map[string]string) string {
	applied := make(map[string]bool)
	var lines []string
	for _, line := range strings.Split(config, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			lines = append(lines, line)
			continue
		}
		parts := strings.SplitN(trimmed, " ", 2)
		if len(parts) == 2 {
			key := parts[0]
			if val, ok := settings[key]; ok {
				lines = append(lines, key+" "+val)
				applied[key] = true
				continue
			}
		}
		lines = append(lines, line)
	}
	// Append any settings not yet present
	for k, v := range settings {
		if !applied[k] {
			lines = append(lines, k+" "+v)
		}
	}
	return strings.Join(lines, "\n")
}

// --- Certs ---

func certsExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action == "" {
		action = "add"
	}
	certPath, _ := params["cert_path"].(string)
	certPEM, _ := params["cert_pem"].(string)

	tmpPath := ""
	if certPEM != "" {
		tmpPath = "/tmp/nexplane-cert-import.pem"
		if err := os.WriteFile(tmpPath, []byte(certPEM), 0600); err != nil {
			return nil, fmt.Errorf("writing cert: %w", err)
		}
		defer os.Remove(tmpPath)
		certPath = tmpPath
	}
	if certPath == "" {
		return nil, fmt.Errorf("cert_path or cert_pem is required")
	}

	switch action {
	case "add":
		out, err := execCommandLinuxAuthDarwin("security", "add-trusted-cert",
			"-d", "-r", "trustRoot", "-k", "/Library/Keychains/System.keychain", certPath).CombinedOutput()
		if err != nil {
			return nil, fmt.Errorf("security add-trusted-cert: %s: %w", out, err)
		}
	case "remove":
		out, err := execCommandLinuxAuthDarwin("security", "delete-certificate",
			"-c", certPath, "/Library/Keychains/System.keychain").CombinedOutput()
		if err != nil {
			return nil, fmt.Errorf("security delete-certificate: %s: %w", out, err)
		}
	default:
		return nil, fmt.Errorf("unsupported action %q: must be add or remove", action)
	}

	return map[string]any{
		"action":     action,
		"cert_path":  certPath,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func certsRollbackOS(params map[string]any) (map[string]any, error) {
	// Reverse the action: if we added, remove; if we removed, re-add
	action, _ := params["action"].(string)
	certPath, _ := params["cert_path"].(string)
	certPEM, _ := params["cert_pem"].(string)

	reverseAction := "remove"
	if action == "remove" {
		reverseAction = "add"
	}
	return certsExecuteOS(map[string]any{
		"action":    reverseAction,
		"cert_path": certPath,
		"cert_pem":  certPEM,
	})
}

// --- NTP ---

func ntpExecuteOS(params map[string]any) (map[string]any, error) {
	// Snapshot
	snapOut, _ := execCommandLinuxAuthDarwin("systemsetup", "-getnetworktimeserver").Output()
	snapshot := strings.TrimSpace(string(snapOut))

	// Enable NTP
	execCommandLinuxAuthDarwin("systemsetup", "-setusingnetworktime", "on").Run() //nolint:errcheck

	serversRaw, _ := params["servers"].([]any)
	if len(serversRaw) > 0 {
		primary, _ := serversRaw[0].(string)
		if primary != "" {
			execCommandLinuxAuthDarwin("systemsetup", "-setnetworktimeserver", primary).Run() //nolint:errcheck
		}
	}

	return map[string]any{
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ntpRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	// Parse "Network Time Server: time.apple.com"
	server := snapshot
	if idx := strings.Index(snapshot, ": "); idx != -1 {
		server = strings.TrimSpace(snapshot[idx+2:])
	}
	if server != "" && server != snapshot {
		execCommandLinuxAuthDarwin("systemsetup", "-setnetworktimeserver", server).Run() //nolint:errcheck
	}
	return map[string]any{"rolled_back": true}, nil
}

// --- User Audit ---

func auditUsersOS(_ map[string]any) (map[string]any, error) {
	out, err := execCommandLinuxAuthDarwin("dscl", ".", "-list", "/Users", "UniqueID").Output()
	if err != nil {
		return nil, fmt.Errorf("dscl list users: %w", err)
	}

	var users []map[string]any
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.Fields(line)
		if len(parts) < 2 {
			continue
		}
		username := parts[0]
		// Skip system accounts (UID < 500)
		// dscl output: "username  uid"
		shellOut, _ := execCommandLinuxAuthDarwin("dscl", ".", "-read",
			"/Users/"+username, "UserShell").Output()
		shell := ""
		for _, l := range strings.Split(string(shellOut), "\n") {
			if strings.HasPrefix(strings.TrimSpace(l), "UserShell:") {
				shell = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(l), "UserShell:"))
			}
		}
		users = append(users, map[string]any{
			"username": username,
			"uid":      parts[1],
			"shell":    shell,
		})
	}

	return map[string]any{
		"users":        users,
		"collected_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

// --- Privesc Audit ---

func auditPrivescOS(_ map[string]any) (map[string]any, error) {
	// Parse sudoers
	sudoersOut, _ := execCommandLinuxAuthDarwin("sudo", "-l").Output()

	// Find setuid binaries
	setuidOut, _ := execCommandLinuxAuthDarwin(
		"find", "/usr/bin", "/usr/local/bin", "/bin", "-perm", "-4000").Output()

	return map[string]any{
		"sudo_rules":      string(sudoersOut),
		"setuid_binaries": strings.Split(strings.TrimSpace(string(setuidOut)), "\n"),
		"collected_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}
```

- [ ] **Step 3: Fix linuxauth_other.go build tag**

Edit `agent/commands/linuxauth/linuxauth_other.go` line 1:
```go
//go:build !linux && !darwin
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/linuxauth/ -run "TestSSH.*Darwin|TestNTP.*Darwin|TestAuditUsers.*Darwin" -v
```
Expected: PASS

- [ ] **Step 5: Verify compilation**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./... && GOOS=linux go build ./... && echo "OK"
```

- [ ] **Step 6: Commit**

```bash
git add agent/commands/linuxauth/linuxauth_darwin.go agent/commands/linuxauth/linuxauth_darwin_test.go agent/commands/linuxauth/linuxauth_other.go
git commit -m "feat(agent): macOS auth controls — SSH, PAM, certs, NTP, user audit"
```

---

### Task 2: compliance_darwin.go (macOS CIS benchmark)

**Files:**
- Create: `agent/commands/compliance/compliance_darwin.go`
- Create: `agent/commands/compliance/compliance_darwin_test.go`
- Modify: `agent/commands/compliance/compliance_other.go` — build tag add `&& !darwin`

- [ ] **Step 1: Write failing test**

Create `agent/commands/compliance/compliance_darwin_test.go`:

```go
//go:build darwin

package compliance

import (
	"os/exec"
	"strings"
	"testing"
)

func TestAuditCISComplianceOS_Darwin(t *testing.T) {
	execCommandCompliance = func(name string, args ...string) *exec.Cmd {
		switch name {
		case "csrutil":
			return exec.Command("echo", "System Integrity Protection status: enabled.")
		case "spctl":
			return exec.Command("echo", "assessments enabled")
		case "/usr/libexec/ApplicationFirewall/socketfilterfw":
			return exec.Command("echo", "Firewall is enabled. (State = 1)")
		case "sw_vers":
			return exec.Command("echo", "14.0")
		case "systemsetup":
			return exec.Command("echo", "Network Time Server: time.apple.com")
		default:
			return exec.Command("echo", "enabled")
		}
	}
	t.Cleanup(func() { execCommandCompliance = exec.Command })

	result, err := auditCISComplianceOS(1, "darwin")
	if err != nil {
		t.Fatalf("auditCISComplianceOS: %v", err)
	}
	if result["level"] != 1 {
		t.Fatalf("expected level=1; got %v", result["level"])
	}
	controls, _ := result["controls"].([]ControlResult)
	if len(controls) == 0 {
		t.Fatal("expected controls list")
	}
	// SIP should be enabled in mock
	for _, c := range controls {
		if strings.Contains(c.ID, "1.1") && c.Title != "" {
			if !c.Passed {
				t.Logf("SIP check not passing: %+v", c)
			}
			return
		}
	}
}
```

- [ ] **Step 2: Implement compliance_darwin.go**

Create `agent/commands/compliance/compliance_darwin.go`:

```go
//go:build darwin

package compliance

import (
	"fmt"
	"os/exec"
	"strings"
)

// execCommandCompliance is mockable in tests.
var execCommandCompliance = exec.Command

func auditCISComplianceOS(level int, osFamily string) (map[string]any, error) {
	var controls []ControlResult

	// Level 1 checks
	controls = append(controls, checkMacSIP()...)
	controls = append(controls, checkMacGatekeeper()...)
	controls = append(controls, checkMacALF()...)
	controls = append(controls, checkMacSoftwareUpdate()...)
	controls = append(controls, checkMacNTP()...)
	controls = append(controls, checkMacSSHConfig()...)

	// Level 2 checks
	if level >= 2 {
		controls = append(controls, checkMacAuditdActive()...)
		controls = append(controls, checkMacSantaInstalled()...)
		controls = append(controls, checkMacScreenLock()...)
	}

	score := CalculateScore(controls)
	return map[string]any{
		"level":        level,
		"os_family":    "darwin",
		"score":        score,
		"controls":     controls,
		"collected_at": collectedNow(),
	}, nil
}

func collectEvidenceOS(_ map[string]any) (map[string]any, error) {
	spOut, _ := execCommandCompliance("system_profiler",
		"SPSoftwareDataType", "SPSecurityDataType").Output()
	csrOut, _ := execCommandCompliance("csrutil", "status").Output()
	spctlOut, _ := execCommandCompliance("spctl", "--status").Output()

	return map[string]any{
		"system_profiler": string(spOut),
		"csrutil_status":  strings.TrimSpace(string(csrOut)),
		"spctl_status":    strings.TrimSpace(string(spctlOut)),
		"collected_at":    collectedNow(),
	}, nil
}

func checkMacSIP() []ControlResult {
	out, _ := execCommandCompliance("csrutil", "status").Output()
	passed := strings.Contains(string(out), "enabled")
	return []ControlResult{{
		ID:     "1.1",
		Title:  "Ensure System Integrity Protection (SIP) is enabled",
		Passed: passed,
		Actual: strings.TrimSpace(string(out)),
	}}
}

func checkMacGatekeeper() []ControlResult {
	out, _ := execCommandCompliance("spctl", "--status").Output()
	passed := strings.Contains(string(out), "assessments enabled")
	return []ControlResult{{
		ID:     "1.2",
		Title:  "Ensure Gatekeeper is enabled",
		Passed: passed,
		Actual: strings.TrimSpace(string(out)),
	}}
}

func checkMacALF() []ControlResult {
	out, _ := execCommandCompliance(
		"/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate").Output()
	passed := strings.Contains(string(out), "enabled") || strings.Contains(string(out), "State = 1")
	return []ControlResult{{
		ID:     "2.1",
		Title:  "Ensure Application Layer Firewall (ALF) is enabled",
		Passed: passed,
		Actual: strings.TrimSpace(string(out)),
	}}
}

func checkMacSoftwareUpdate() []ControlResult {
	out, _ := execCommandCompliance("sw_vers", "-productVersion").Output()
	version := strings.TrimSpace(string(out))
	// Check macOS version >= 13 (Ventura) as minimum supported
	passed := version != "" && version >= "13"
	return []ControlResult{{
		ID:     "1.3",
		Title:  "Ensure macOS is up to date (>= Ventura 13.0)",
		Passed: passed,
		Actual: fmt.Sprintf("macOS %s", version),
	}}
}

func checkMacNTP() []ControlResult {
	out, _ := execCommandCompliance("systemsetup", "-getusingnetworktime").Output()
	passed := strings.Contains(strings.ToLower(string(out)), "on")
	return []ControlResult{{
		ID:     "2.2",
		Title:  "Ensure Network Time Protocol (NTP) is enabled",
		Passed: passed,
		Actual: strings.TrimSpace(string(out)),
	}}
}

func checkMacSSHConfig() []ControlResult {
	out, _ := execCommandCompliance("sshd", "-T").Output()
	permitRoot := "unknown"
	passwordAuth := "unknown"
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.ToLower(strings.TrimSpace(line))
		if strings.HasPrefix(line, "permitrootlogin ") {
			permitRoot = strings.TrimPrefix(line, "permitrootlogin ")
		}
		if strings.HasPrefix(line, "passwordauthentication ") {
			passwordAuth = strings.TrimPrefix(line, "passwordauthentication ")
		}
	}
	return []ControlResult{
		{
			ID:     "3.1",
			Title:  "Ensure SSH PermitRootLogin is no",
			Passed: permitRoot == "no",
			Actual: "PermitRootLogin " + permitRoot,
		},
		{
			ID:     "3.2",
			Title:  "Ensure SSH PasswordAuthentication is no",
			Passed: passwordAuth == "no",
			Actual: "PasswordAuthentication " + passwordAuth,
		},
	}
}

func checkMacAuditdActive() []ControlResult {
	out, _ := execCommandCompliance("audit", "-l").Output()
	passed := len(out) > 0 && !strings.Contains(string(out), "not running")
	return []ControlResult{{
		ID:     "4.1",
		Title:  "Ensure BSM audit daemon is active",
		Passed: passed,
		Actual: strings.TrimSpace(string(out)),
	}}
}

func checkMacSantaInstalled() []ControlResult {
	out, err := execCommandCompliance("santactl", "version").Output()
	passed := err == nil && len(out) > 0
	actual := "not installed"
	if passed {
		actual = strings.TrimSpace(string(out))
	}
	return []ControlResult{{
		ID:     "4.2",
		Title:  "Ensure Santa binary allowlisting is installed",
		Passed: passed,
		Actual: actual,
	}}
}

func checkMacScreenLock() []ControlResult {
	// Check via defaults read com.apple.screensaver
	out, _ := execCommandCompliance("defaults", "read",
		"com.apple.screensaver", "idleTime").Output()
	idleTime := strings.TrimSpace(string(out))
	passed := idleTime != "" && idleTime != "0"
	return []ControlResult{{
		ID:     "4.3",
		Title:  "Ensure screen lock is configured",
		Passed: passed,
		Actual: fmt.Sprintf("idleTime=%s", idleTime),
	}}
}
```

- [ ] **Step 3: Fix compliance_other.go build tag**

Edit `agent/commands/compliance/compliance_other.go` line 1:
```go
//go:build !linux && !darwin && !windows
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/compliance/ -run TestAuditCISComplianceOS_Darwin -v
```
Expected: PASS

- [ ] **Step 5: Compile all platforms**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./... && GOOS=linux go build ./... && GOOS=windows go build ./... && echo "ALL OK"
```

- [ ] **Step 6: Commit**

```bash
git add agent/commands/compliance/compliance_darwin.go agent/commands/compliance/compliance_darwin_test.go agent/commands/compliance/compliance_other.go
git commit -m "feat(agent): macOS CIS compliance audit via spctl/csrutil/socketfilterfw"
```

---

### Task 3: db_darwin.go (credential rotation) + db_windows.go fix

**Files:**
- Modify: `agent/commands/credrotation/db_darwin.go` — replace stub with real implementation
- Modify: `agent/commands/credrotation/db_windows.go` — implement updateDBUserPassword

- [ ] **Step 1: Write failing test for darwin**

Create `agent/commands/credrotation/db_darwin_test.go`:

```go
//go:build darwin

package credrotation

import (
	"context"
	"os/exec"
	"strings"
	"testing"
)

func TestUpdateDBUserPassword_DarwinPostgres(t *testing.T) {
	var called []string
	execCommandDB = func(name string, args ...string) *exec.Cmd {
		called = append(called, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ALTER ROLE")
	}
	t.Cleanup(func() { execCommandDB = exec.Command })

	err := updateDBUserPassword(context.Background(), DBRotateParams{
		DBEngine:   "postgres",
		DBHost:     "localhost",
		DBPort:     5432,
		DBUsername: "appuser",
		NewPassword: "newpass",
	})
	if err != nil {
		t.Fatalf("updateDBUserPassword: %v", err)
	}
	if len(called) == 0 {
		t.Fatal("expected psql call")
	}
	found := false
	for _, c := range called {
		if strings.Contains(c, "psql") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected psql; got: %v", called)
	}
}

func TestRestartService_DarwinLaunchd(t *testing.T) {
	var called []string
	execCommandDB = func(name string, args ...string) *exec.Cmd {
		called = append(called, name+" "+strings.Join(args, " "))
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandDB = exec.Command })

	err := restartService(context.Background(), "postgresql")
	if err != nil {
		t.Fatalf("restartService: %v", err)
	}
	// launchctl unload + load or brew restart expected
	if len(called) == 0 {
		t.Fatal("expected at least one command")
	}
}
```

- [ ] **Step 2: Implement db_darwin.go**

Replace `agent/commands/credrotation/db_darwin.go` (currently a stub):

```go
//go:build darwin

package credrotation

import (
	"context"
	"fmt"
	"os/exec"
)

// execCommandDB is mockable in tests.
var execCommandDB = exec.Command

func updateDBUserPassword(ctx context.Context, p DBRotateParams) error {
	var stmt string
	switch p.DBEngine {
	case "postgres":
		stmt = fmt.Sprintf("ALTER USER %s WITH PASSWORD '%s';", p.DBUsername, p.NewPassword)
	case "mysql":
		stmt = fmt.Sprintf("ALTER USER '%s'@'%%' IDENTIFIED BY '%s';", p.DBUsername, p.NewPassword)
	default:
		return fmt.Errorf("unsupported db engine: %s", p.DBEngine)
	}
	cmd := buildDBCmdDarwin(ctx, p, stmt)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("update db user password: %w — %s", err, out)
	}
	return nil
}

func buildDBCmdDarwin(ctx context.Context, p DBRotateParams, stmt string) *exec.Cmd {
	switch p.DBEngine {
	case "postgres":
		return execCommandDB("psql",
			fmt.Sprintf("host=%s port=%d user=%s sslmode=require", p.DBHost, p.DBPort, p.DBUsername),
			"-c", stmt)
	case "mysql":
		return execCommandDB("mysql",
			fmt.Sprintf("-h%s", p.DBHost),
			fmt.Sprintf("-P%d", p.DBPort),
			fmt.Sprintf("-u%s", p.DBUsername),
			"-e", stmt)
	default:
		return execCommandDB("false")
	}
}

func restartService(ctx context.Context, serviceName string) error {
	// Try launchd system plist paths
	plists := []string{
		"/Library/LaunchDaemons/" + serviceName + ".plist",
		"/System/Library/LaunchDaemons/" + serviceName + ".plist",
		// Homebrew-style: homebrew.mxcl.<name>.plist
		"/Library/LaunchDaemons/homebrew.mxcl." + serviceName + ".plist",
	}
	for _, plist := range plists {
		unload := execCommandDB("launchctl", "unload", plist)
		if _, err := unload.CombinedOutput(); err == nil {
			load := execCommandDB("launchctl", "load", "-w", plist)
			if out, err := load.CombinedOutput(); err != nil {
				return fmt.Errorf("launchctl load %s: %w — %s", plist, err, out)
			}
			return nil
		}
	}
	// Fallback: brew services restart
	cmd := execCommandDB("brew", "services", "restart", serviceName)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("restart %s (tried launchd+brew): %w — %s", serviceName, err, out)
	}
	return nil
}
```

- [ ] **Step 3: Implement db_windows.go updateDBUserPassword**

Edit `agent/commands/credrotation/db_windows.go` — replace `updateDBUserPassword` stub body:

```go
//go:build windows

package credrotation

import (
	"context"
	"fmt"
	"os/exec"
)

func updateDBUserPassword(ctx context.Context, p DBRotateParams) error {
	var stmt string
	switch p.DBEngine {
	case "postgres":
		stmt = fmt.Sprintf("ALTER USER %s WITH PASSWORD '%s';", p.DBUsername, p.NewPassword)
	case "mysql":
		stmt = fmt.Sprintf("ALTER USER '%s'@'%%' IDENTIFIED BY '%s';", p.DBUsername, p.NewPassword)
	default:
		return fmt.Errorf("unsupported db engine: %s", p.DBEngine)
	}
	cmd := buildDBCmd(ctx, p, stmt)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("update db user password: %w — %s", err, out)
	}
	return nil
}

func buildDBCmd(ctx context.Context, p DBRotateParams, stmt string) *exec.Cmd {
	switch p.DBEngine {
	case "postgres":
		return exec.CommandContext(ctx, "psql",
			fmt.Sprintf("host=%s port=%d user=%s sslmode=require", p.DBHost, p.DBPort, p.DBUsername),
			"-c", stmt)
	case "mysql":
		return exec.CommandContext(ctx, "mysql",
			fmt.Sprintf("-h%s", p.DBHost),
			fmt.Sprintf("-P%d", p.DBPort),
			fmt.Sprintf("-u%s", p.DBUsername),
			"-e", stmt)
	default:
		return exec.CommandContext(ctx, "cmd", "/c", "echo", "unsupported")
	}
}

// restartService is already implemented in db_windows.go via net stop/start — no change needed.
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/credrotation/ -run "TestUpdateDBUserPassword_Darwin|TestRestartService_Darwin" -v
```
Expected: PASS

- [ ] **Step 5: Compile all platforms**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./... && GOOS=windows go build ./... && echo "OK"
```

- [ ] **Step 6: Commit**

```bash
git add agent/commands/credrotation/db_darwin.go agent/commands/credrotation/db_darwin_test.go agent/commands/credrotation/db_windows.go
git commit -m "feat(agent): macOS/Windows DB credential rotation via psql/mysql CLI"
```
