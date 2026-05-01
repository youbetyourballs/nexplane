# Linux Auth, Access & Certificates Implementation Plan (Spec 5b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 6 agent commands for Linux PAM hardening, SSH hardening, user/group audit, privilege escalation audit, CA certificate management, and NTP configuration.

**Architecture:** New `agent/commands/linuxauth/` package. Public API functions in `linuxauth.go` (cross-platform, does param validation), Linux implementations in `*_linux.go` files with `//go:build linux`, non-Linux stubs in `linuxauth_other.go` with `//go:build !linux`. Tests in `linuxauth_test.go` (no build tag — only tests param validation). All 6 commands registered in `executor.go`, cataloged in `nexplane_agent_mock.json`, with Python mock stubs.

**Tech Stack:** Go 1.22+, `os/exec` for shell commands, `os.ReadFile`/`WriteFile` for file management, nexplane-managed-begin/end block pattern for config files.

**Working directory for all commands:** `f:\Nexplane\nexplane\.worktrees\agent-hardening`

---

### Task 1: Package scaffolding

**Files:**
- Create: `agent/commands/linuxauth/linuxauth.go`
- Create: `agent/commands/linuxauth/linuxauth_other.go`
- Create: `agent/commands/linuxauth/linuxauth_test.go`

- [ ] **Step 1: Create `agent/commands/linuxauth/linuxauth.go`**

```go
package linuxauth

import "fmt"

var validPAMProfiles = map[string]bool{
	"cis_level1": true,
	"cis_level2": true,
	"custom":     true,
}

func ConfigurePAMExecute(params map[string]any) (map[string]any, error) {
	if profile, ok := params["profile"].(string); ok && profile != "" {
		if !validPAMProfiles[profile] {
			return nil, fmt.Errorf("invalid profile %q: must be cis_level1, cis_level2, or custom", profile)
		}
	}
	return pamExecuteOS(params)
}

func ConfigurePAMRollback(params map[string]any) (map[string]any, error) {
	return pamRollbackOS(params)
}

func HardenSSHExecute(params map[string]any) (map[string]any, error) {
	return sshExecuteOS(params)
}

func HardenSSHRollback(params map[string]any) (map[string]any, error) {
	return sshRollbackOS(params)
}

func AuditUsersAndGroupsExecute(params map[string]any) (map[string]any, error) {
	return auditUsersOS(params)
}

func AuditPrivescVulnerabilitiesExecute(params map[string]any) (map[string]any, error) {
	return auditPrivescOS(params)
}

func ManageCACertificatesExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action != "install" && action != "remove" {
		return nil, fmt.Errorf("action must be 'install' or 'remove', got %q", action)
	}
	certName, _ := params["cert_name"].(string)
	if certName == "" {
		return nil, fmt.Errorf("cert_name is required")
	}
	if action == "install" {
		if cert, _ := params["certificate"].(string); cert == "" {
			return nil, fmt.Errorf("certificate PEM is required for install")
		}
	}
	return certsExecuteOS(params)
}

func ManageCACertificatesRollback(params map[string]any) (map[string]any, error) {
	return certsRollbackOS(params)
}

func ConfigureNTPExecute(params map[string]any) (map[string]any, error) {
	servers, _ := params["servers"].([]any)
	if len(servers) == 0 {
		return nil, fmt.Errorf("servers list is required and must not be empty")
	}
	return ntpExecuteOS(params)
}

func ConfigureNTPRollback(params map[string]any) (map[string]any, error) {
	return ntpRollbackOS(params)
}
```

- [ ] **Step 2: Create `agent/commands/linuxauth/linuxauth_other.go`**

```go
//go:build !linux

package linuxauth

import "fmt"

func pamExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_pam requires Linux")
}
func pamRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_pam requires Linux")
}
func sshExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_ssh requires Linux")
}
func sshRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_ssh requires Linux")
}
func auditUsersOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_users_and_groups requires Linux")
}
func auditPrivescOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_privesc_vulnerabilities requires Linux")
}
func certsExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("manage_ca_certificates requires Linux")
}
func certsRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("manage_ca_certificates requires Linux")
}
func ntpExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ntp requires Linux")
}
func ntpRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ntp requires Linux")
}
```

- [ ] **Step 3: Create `agent/commands/linuxauth/linuxauth_test.go`**

```go
package linuxauth_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/linuxauth"
)

func TestConfigurePAMInvalidProfile(t *testing.T) {
	_, err := linuxauth.ConfigurePAMExecute(map[string]any{"profile": "not_a_profile"})
	if err == nil {
		t.Error("expected error for invalid profile")
	}
	if err != nil && !strings.Contains(err.Error(), "invalid profile") {
		t.Errorf("expected 'invalid profile' in error, got: %v", err)
	}
}

func TestConfigurePAMValidProfilesPassValidation(t *testing.T) {
	for _, profile := range []string{"cis_level1", "cis_level2", "custom"} {
		_, err := linuxauth.ConfigurePAMExecute(map[string]any{"profile": profile})
		if err != nil && strings.Contains(err.Error(), "invalid profile") {
			t.Errorf("profile %q should pass validation, got: %v", profile, err)
		}
	}
}

func TestConfigurePAMRollbackRequiresSnapshot(t *testing.T) {
	_, err := linuxauth.ConfigurePAMRollback(map[string]any{})
	if err == nil {
		t.Error("expected error when files_snapshot missing")
	}
}

func TestManageCACertificatesInvalidAction(t *testing.T) {
	_, err := linuxauth.ManageCACertificatesExecute(map[string]any{
		"action": "update", "cert_name": "test",
	})
	if err == nil || !strings.Contains(err.Error(), "action must be") {
		t.Errorf("expected 'action must be' error, got: %v", err)
	}
}

func TestManageCACertificatesRequiresCertName(t *testing.T) {
	_, err := linuxauth.ManageCACertificatesExecute(map[string]any{"action": "install"})
	if err == nil || !strings.Contains(err.Error(), "cert_name") {
		t.Errorf("expected cert_name error, got: %v", err)
	}
}

func TestManageCACertificatesInstallRequiresPEM(t *testing.T) {
	_, err := linuxauth.ManageCACertificatesExecute(map[string]any{
		"action": "install", "cert_name": "test",
	})
	if err == nil || !strings.Contains(err.Error(), "certificate PEM") {
		t.Errorf("expected certificate PEM error, got: %v", err)
	}
}

func TestConfigureNTPRequiresServers(t *testing.T) {
	_, err := linuxauth.ConfigureNTPExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "servers") {
		t.Errorf("expected servers error, got: %v", err)
	}
}

func TestConfigureNTPEmptyServersFails(t *testing.T) {
	_, err := linuxauth.ConfigureNTPExecute(map[string]any{"servers": []any{}})
	if err == nil {
		t.Error("expected error for empty servers list")
	}
}

func TestConfigureNTPRollbackRequiresSnapshot(t *testing.T) {
	_, err := linuxauth.ConfigureNTPRollback(map[string]any{})
	if err == nil {
		t.Error("expected error when snapshot missing")
	}
}
```

- [ ] **Step 4: Verify package builds**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/linuxauth/...
```

Expected: no output (success)

- [ ] **Step 5: Run tests**

```
cd agent && "C:/Program Files/Go/bin/go.exe" test ./commands/linuxauth/... -v
```

Expected: all validation tests PASS

- [ ] **Step 6: Commit**

```
git add agent/commands/linuxauth/
git commit -m "feat(linuxauth): add package scaffolding with param validation and stubs"
```

---

### Task 2: `configure_pam` implementation

**Files:**
- Create: `agent/commands/linuxauth/pam_linux.go`

- [ ] **Step 1: Create `agent/commands/linuxauth/pam_linux.go`**

```go
//go:build linux

package linuxauth

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

type pamVariant struct {
	pwqualityConf string
	failockConf   string
	commonAuth    string
	commonPass    string
	commonAccount string
	pwSo          string // pam_pwquality or pam_cracklib
	failSo        string // pam_faillock or pam_tally2
}

type pamParams struct {
	minLen          int
	complexity      bool
	maxFailed       int
	lockoutDuration int
	remember        int
	sessionTimeout  int
}

func detectPAMVariant() pamVariant {
	v := pamVariant{pwqualityConf: "/etc/security/pwquality.conf"}
	// Detect pw module
	for _, p := range []string{
		"/lib/security/pam_pwquality.so",
		"/lib/x86_64-linux-gnu/security/pam_pwquality.so",
		"/usr/lib64/security/pam_pwquality.so",
	} {
		if _, err := os.Stat(p); err == nil {
			v.pwSo = "pam_pwquality"
			break
		}
	}
	if v.pwSo == "" {
		v.pwSo = "pam_cracklib"
	}
	// Detect fail module
	for _, p := range []string{
		"/lib/security/pam_faillock.so",
		"/lib/x86_64-linux-gnu/security/pam_faillock.so",
		"/usr/lib64/security/pam_faillock.so",
	} {
		if _, err := os.Stat(p); err == nil {
			v.failSo = "pam_faillock"
			v.failockConf = "/etc/security/faillock.conf"
			break
		}
	}
	if v.failSo == "" {
		v.failSo = "pam_tally2"
		v.failockConf = "/etc/security/pam_tally2.conf"
	}
	// Detect Debian vs RHEL layout
	if _, err := os.Stat("/etc/pam.d/system-auth"); err == nil {
		v.commonAuth = "/etc/pam.d/system-auth"
		v.commonPass = "/etc/pam.d/password-auth"
		v.commonAccount = "/etc/pam.d/system-auth"
	} else {
		v.commonAuth = "/etc/pam.d/common-auth"
		v.commonPass = "/etc/pam.d/common-password"
		v.commonAccount = "/etc/pam.d/common-account"
	}
	return v
}

func resolvePAMProfile(profile string, overrides map[string]any) pamParams {
	p := pamParams{minLen: 14, complexity: true, maxFailed: 5, lockoutDuration: 900, remember: 5}
	if profile == "cis_level2" {
		p.minLen = 15
		p.maxFailed = 3
	}
	if v, ok := overrides["min_password_length"]; ok {
		if n, err := anyToInt(v); err == nil {
			p.minLen = n
		}
	}
	if v, ok := overrides["password_complexity"].(string); ok {
		p.complexity = v != "disabled"
	}
	if v, ok := overrides["max_failed_attempts"]; ok {
		if n, err := anyToInt(v); err == nil {
			p.maxFailed = n
		}
	}
	if v, ok := overrides["lockout_duration_seconds"]; ok {
		if n, err := anyToInt(v); err == nil {
			p.lockoutDuration = n
		}
	}
	if v, ok := overrides["remember_passwords"]; ok {
		if n, err := anyToInt(v); err == nil {
			p.remember = n
		}
	}
	if v, ok := overrides["session_timeout_seconds"]; ok {
		if n, err := anyToInt(v); err == nil {
			p.sessionTimeout = n
		}
	}
	return p
}

func pamExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_pam requires root privileges")
	}
	profile, _ := params["profile"].(string)
	if profile == "" {
		profile = "cis_level1"
	}
	overrides := map[string]any{}
	if p, ok := params["params"].(map[string]any); ok {
		overrides = p
	}
	v := detectPAMVariant()
	p := resolvePAMProfile(profile, overrides)

	// Snapshot
	snapshot := map[string]any{}
	for _, path := range []string{v.pwqualityConf, v.failockConf, v.commonAuth, v.commonPass, v.commonAccount} {
		data, _ := os.ReadFile(path)
		snapshot[path] = string(data)
	}

	// Write pwquality.conf
	pwqContent := fmt.Sprintf("minlen = %d\n", p.minLen)
	if p.complexity {
		pwqContent += "dcredit = -1\nucredit = -1\nocredit = -1\nlcredit = -1\n"
	}
	if err := os.WriteFile(v.pwqualityConf, []byte(pwqContent), 0644); err != nil {
		return nil, fmt.Errorf("writing pwquality.conf: %w", err)
	}

	// Write faillock.conf (pam_faillock only)
	if v.failSo == "pam_faillock" {
		failContent := fmt.Sprintf("deny = %d\nunlock_time = %d\n", p.maxFailed, p.lockoutDuration)
		if err := os.WriteFile(v.failockConf, []byte(failContent), 0644); err != nil {
			return nil, fmt.Errorf("writing faillock.conf: %w", err)
		}
	}

	// Update PAM auth stack
	authData, _ := os.ReadFile(v.commonAuth)
	var authBlock string
	if v.failSo == "pam_faillock" {
		authBlock = fmt.Sprintf(
			"auth required pam_faillock.so preauth deny=%d unlock_time=%d\nauth required %s.so\nauth required pam_faillock.so authfail deny=%d unlock_time=%d\n",
			p.maxFailed, p.lockoutDuration, v.pwSo, p.maxFailed, p.lockoutDuration)
	} else {
		authBlock = fmt.Sprintf("auth required pam_tally2.so deny=%d unlock_time=%d\n", p.maxFailed, p.lockoutDuration)
	}
	if err := os.WriteFile(v.commonAuth, []byte(insertManagedBlock(string(authData), authBlock)), 0644); err != nil {
		return nil, fmt.Errorf("writing %s: %w", v.commonAuth, err)
	}

	// Update PAM password stack
	passData, _ := os.ReadFile(v.commonPass)
	passBlock := fmt.Sprintf("password required %s.so\npassword required pam_pwhistory.so remember=%d\n", v.pwSo, p.remember)
	if err := os.WriteFile(v.commonPass, []byte(insertManagedBlock(string(passData), passBlock)), 0644); err != nil {
		return nil, fmt.Errorf("writing %s: %w", v.commonPass, err)
	}

	// Update PAM account stack (pam_faillock only)
	if v.failSo == "pam_faillock" {
		acctData, _ := os.ReadFile(v.commonAccount)
		acctBlock := "account required pam_faillock.so\n"
		if err := os.WriteFile(v.commonAccount, []byte(insertManagedBlock(string(acctData), acctBlock)), 0644); err != nil {
			return nil, fmt.Errorf("writing %s: %w", v.commonAccount, err)
		}
	}

	// Session timeout
	if p.sessionTimeout > 0 {
		tmout := fmt.Sprintf("TMOUT=%d\nreadonly TMOUT\nexport TMOUT\n", p.sessionTimeout)
		if err := os.WriteFile("/etc/profile.d/99-nexplane-timeout.sh", []byte(tmout), 0644); err != nil {
			return nil, fmt.Errorf("writing TMOUT: %w", err)
		}
		snapshot["_session_timeout_written"] = "true"
	}

	return map[string]any{
		"profile_applied": profile,
		"pam_variant":     map[string]string{"pw_module": v.pwSo, "fail_module": v.failSo},
		"params_applied":  map[string]any{"min_len": p.minLen, "max_failed": p.maxFailed, "lockout_secs": p.lockoutDuration, "remember": p.remember},
		"files_snapshot":  snapshot,
		"applied_at":      time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func pamRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["files_snapshot"].(map[string]any)
	if !ok || snapshot == nil {
		return nil, fmt.Errorf("files_snapshot is required for rollback")
	}
	for path, content := range snapshot {
		if path == "_session_timeout_written" {
			os.Remove("/etc/profile.d/99-nexplane-timeout.sh")
			continue
		}
		if err := os.WriteFile(path, []byte(content.(string)), 0644); err != nil {
			return nil, fmt.Errorf("restoring %s: %w", path, err)
		}
	}
	return map[string]any{"rolled_back": true, "restored_files": len(snapshot)}, nil
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

func anyToInt(v any) (int, error) {
	switch t := v.(type) {
	case int:
		return t, nil
	case float64:
		return int(t), nil
	case string:
		return strconv.Atoi(t)
	}
	return 0, fmt.Errorf("cannot convert %T to int", v)
}
```

- [ ] **Step 2: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/linuxauth/... && "C:/Program Files/Go/bin/go.exe" test ./commands/linuxauth/... -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```
git add agent/commands/linuxauth/pam_linux.go
git commit -m "feat(linuxauth): implement configure_pam (PAM hardening)"
```

---

### Task 3: `harden_ssh` implementation

**Files:**
- Create: `agent/commands/linuxauth/ssh_linux.go`

- [ ] **Step 1: Create `agent/commands/linuxauth/ssh_linux.go`**

```go
//go:build linux

package linuxauth

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

var sshDefaults = map[string]string{
	"PermitRootLogin":      "no",
	"PasswordAuthentication": "no",
	"PubkeyAuthentication": "yes",
	"Ciphers":              "chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com",
	"MACs":                 "hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com",
	"KexAlgorithms":        "curve25519-sha256,curve25519-sha256@libssh.org,diffie-hellman-group16-sha512",
	"ClientAliveInterval":  "300",
	"ClientAliveCountMax":  "3",
	"MaxAuthTries":         "4",
	"LoginGraceTime":       "60",
	"X11Forwarding":        "no",
	"PermitEmptyPasswords": "no",
}

var sshParamMap = map[string]string{
	"permit_root_login": "PermitRootLogin", "password_authentication": "PasswordAuthentication",
	"pubkey_authentication": "PubkeyAuthentication", "allowed_ciphers": "Ciphers",
	"allowed_macs": "MACs", "allowed_kex_algorithms": "KexAlgorithms",
	"client_alive_interval": "ClientAliveInterval", "client_alive_count_max": "ClientAliveCountMax",
	"max_auth_tries": "MaxAuthTries", "login_grace_time": "LoginGraceTime",
	"x11_forwarding": "X11Forwarding", "permit_empty_passwords": "PermitEmptyPasswords",
	"port": "Port", "allow_users": "AllowUsers", "allow_groups": "AllowGroups",
}

func sshExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("harden_ssh requires root privileges")
	}
	if _, err := exec.LookPath("sshd"); err != nil {
		return nil, fmt.Errorf("sshd not found: %w", err)
	}

	// Merge settings with defaults
	settings := make(map[string]string)
	for k, v := range sshDefaults {
		settings[k] = v
	}
	if userSettings, ok := params["settings"].(map[string]any); ok {
		for k, v := range userSettings {
			key := k
			if mapped, ok := sshParamMap[k]; ok {
				key = mapped
			}
			settings[key] = fmt.Sprintf("%v", v)
		}
	}

	// Snapshot
	snapshot := map[string]any{}
	if data, err := os.ReadFile("/etc/ssh/sshd_config"); err == nil {
		snapshot["/etc/ssh/sshd_config"] = string(data)
	}
	dropInDir := "/etc/ssh/sshd_config.d"
	if files, err := os.ReadDir(dropInDir); err == nil {
		for _, f := range files {
			p := filepath.Join(dropInDir, f.Name())
			if data, err := os.ReadFile(p); err == nil {
				snapshot[p] = string(data)
			}
		}
	}

	// Ensure drop-in dir exists
	if err := os.MkdirAll(dropInDir, 0755); err != nil {
		return nil, fmt.Errorf("creating sshd_config.d: %w", err)
	}

	// Write drop-in
	dropInPath := "/etc/ssh/sshd_config.d/99-nexplane-hardening.conf"
	var sb strings.Builder
	sb.WriteString("# Nexplane SSH hardening — do not edit manually\n\n")
	for k, v := range settings {
		if v != "" {
			fmt.Fprintf(&sb, "%s %s\n", k, v)
		}
	}
	if err := os.WriteFile(dropInPath, []byte(sb.String()), 0600); err != nil {
		return nil, fmt.Errorf("writing drop-in: %w", err)
	}

	// Validate config
	if out, err := exec.Command("sshd", "-t").CombinedOutput(); err != nil {
		os.Remove(dropInPath)
		return nil, fmt.Errorf("sshd -t failed: %s", out)
	}

	// Reload (preserve existing sessions)
	if err := exec.Command("systemctl", "reload", "sshd").Run(); err != nil {
		exec.Command("systemctl", "reload", "ssh").Run() //nolint
	}

	return map[string]any{
		"settings_applied":      settings,
		"drop_in_path":          dropInPath,
		"sshd_config_snapshot":  snapshot,
		"applied_at":            time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func sshRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["sshd_config_snapshot"].(map[string]any)
	if !ok || snapshot == nil {
		return nil, fmt.Errorf("sshd_config_snapshot is required for rollback")
	}
	os.Remove("/etc/ssh/sshd_config.d/99-nexplane-hardening.conf")
	for path, content := range snapshot {
		os.WriteFile(path, []byte(content.(string)), 0600) //nolint
	}
	if err := exec.Command("systemctl", "reload", "sshd").Run(); err != nil {
		exec.Command("systemctl", "reload", "ssh").Run() //nolint
	}
	return map[string]any{"rolled_back": true}, nil
}
```

- [ ] **Step 2: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/linuxauth/... && "C:/Program Files/Go/bin/go.exe" test ./commands/linuxauth/... -v
```

- [ ] **Step 3: Commit**

```
git add agent/commands/linuxauth/ssh_linux.go
git commit -m "feat(linuxauth): implement harden_ssh (SSH drop-in hardening)"
```

---

### Task 4: `audit_users_and_groups` implementation

**Files:**
- Create: `agent/commands/linuxauth/users_linux.go`

- [ ] **Step 1: Create `agent/commands/linuxauth/users_linux.go`**

```go
//go:build linux

package linuxauth

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

func auditUsersOS(_ map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("audit_users_and_groups requires root privileges")
	}

	type userEntry struct {
		name  string
		uid   int
		home  string
		shell string
	}

	// Parse /etc/passwd
	var users []userEntry
	if f, err := os.Open("/etc/passwd"); err == nil {
		defer f.Close()
		sc := bufio.NewScanner(f)
		for sc.Scan() {
			line := sc.Text()
			if strings.HasPrefix(line, "#") || line == "" {
				continue
			}
			parts := strings.Split(line, ":")
			if len(parts) < 7 {
				continue
			}
			uid, _ := strconv.Atoi(parts[2])
			users = append(users, userEntry{name: parts[0], uid: uid, home: parts[5], shell: parts[6]})
		}
	}

	// Parse /etc/shadow for empty passwords
	emptyPass := map[string]bool{}
	if f, err := os.Open("/etc/shadow"); err == nil {
		defer f.Close()
		sc := bufio.NewScanner(f)
		for sc.Scan() {
			parts := strings.SplitN(sc.Text(), ":", 3)
			if len(parts) >= 2 && parts[1] == "" {
				emptyPass[parts[0]] = true
			}
		}
	}

	// Parse /etc/group for sudo/wheel members
	sudoMembers := []string{}
	if f, err := os.Open("/etc/group"); err == nil {
		defer f.Close()
		sc := bufio.NewScanner(f)
		for sc.Scan() {
			parts := strings.SplitN(sc.Text(), ":", 4)
			if len(parts) == 4 && (parts[0] == "sudo" || parts[0] == "wheel") {
				if parts[3] != "" {
					sudoMembers = append(sudoMembers, strings.Split(parts[3], ",")...)
				}
			}
		}
	}

	var findings []map[string]any
	for _, u := range users {
		if u.uid == 0 && u.name != "root" {
			findings = append(findings, map[string]any{"user": u.name, "tag": "uid0-non-root",
				"description": fmt.Sprintf("Account %q has UID 0 but is not root", u.name),
			})
		}
		if emptyPass[u.name] {
			findings = append(findings, map[string]any{"user": u.name, "tag": "empty-password",
				"description": fmt.Sprintf("Account %q has empty password", u.name),
			})
		}
		// Service accounts (uid 1-999) with interactive shell
		if u.uid > 0 && u.uid < 1000 && (strings.HasSuffix(u.shell, "/bash") || strings.HasSuffix(u.shell, "/sh")) {
			findings = append(findings, map[string]any{"user": u.name, "tag": "svc-interactive-shell",
				"description": fmt.Sprintf("Service account %q (uid=%d) has interactive shell %q", u.name, u.uid, u.shell),
			})
		}
		// No password expiry (regular users)
		if u.uid >= 1000 {
			out, _ := exec.Command("chage", "-l", u.name).Output()
			if strings.Contains(string(out), "never") {
				findings = append(findings, map[string]any{"user": u.name, "tag": "user-no-expiry",
					"description": fmt.Sprintf("Account %q has no password expiry", u.name),
				})
			}
			// World-writable home
			if fi, err := os.Stat(u.home); err == nil && fi.Mode()&0002 != 0 {
				findings = append(findings, map[string]any{"user": u.name, "tag": "world-writable-home",
					"description": fmt.Sprintf("Home %q is world-writable", u.home),
				})
			}
		}
	}
	if len(sudoMembers) > 0 {
		findings = append(findings, map[string]any{"tag": "sudo-group-members",
			"description": fmt.Sprintf("sudo/wheel members for review: %v", sudoMembers),
			"members":     sudoMembers,
		})
	}

	tags := []string{}
	if len(findings) > 0 {
		tags = append(tags, "users-audit-findings")
	}
	return map[string]any{
		"findings":   findings,
		"total":      len(findings),
		"tags":       tags,
		"audited_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
```

- [ ] **Step 2: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/linuxauth/... && "C:/Program Files/Go/bin/go.exe" test ./commands/linuxauth/... -v
```

- [ ] **Step 3: Commit**

```
git add agent/commands/linuxauth/users_linux.go
git commit -m "feat(linuxauth): implement audit_users_and_groups (read-only ingest)"
```

---

### Task 5: `audit_privesc_vulnerabilities` implementation

**Files:**
- Create: `agent/commands/linuxauth/privesc_linux.go`

- [ ] **Step 1: Create `agent/commands/linuxauth/privesc_linux.go`**

```go
//go:build linux

package linuxauth

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

func auditPrivescOS(_ map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("audit_privesc_vulnerabilities requires root privileges")
	}

	major, minor, patch := linuxKernelVersion()
	var findings []map[string]any

	// CVE-2021-4034 PwnKit
	pwnkitPresent := false
	out, _ := exec.Command("dpkg", "-l", "policykit-1").Output()
	if strings.Contains(string(out), "ii") {
		pwnkitPresent = true // conservative: polkit installed, operator must verify patch level
	} else if out2, _ := exec.Command("rpm", "-q", "polkit").Output(); !strings.Contains(string(out2), "not installed") {
		pwnkitPresent = true
	}
	findings = append(findings, map[string]any{
		"cve_id": "CVE-2021-4034", "severity": "critical", "condition_present": pwnkitPresent,
		"description":  "PwnKit: polkit pkexec local privilege escalation",
		"remediation":  "Upgrade polkit: apt-get upgrade policykit-1 or dnf update polkit",
	})

	// CVE-2022-0847 DirtyPipe
	dirtyPipe := major < 5 || (major == 5 && (minor < 10 || (minor == 10 && patch < 102) || (minor == 15 && patch < 25) || (minor == 16 && patch < 11)))
	findings = append(findings, map[string]any{
		"cve_id": "CVE-2022-0847", "severity": "high", "condition_present": dirtyPipe,
		"description":  fmt.Sprintf("DirtyPipe: kernel %d.%d.%d may be vulnerable", major, minor, patch),
		"remediation":  "Upgrade kernel to 5.16.11+, 5.15.25+, or 5.10.102+",
	})

	// CVE-2016-5195 Dirty COW
	dirtyCOW := major < 4 || (major == 4 && minor < 8)
	findings = append(findings, map[string]any{
		"cve_id": "CVE-2016-5195", "severity": "critical", "condition_present": dirtyCOW,
		"description":  "Dirty COW: kernel < 4.8.3 vulnerable to race condition LPE",
		"remediation":  "Upgrade kernel to 4.8.3 or later",
	})

	// Unexpected SUID binaries
	baseline := map[string]bool{
		"/usr/bin/sudo": true, "/usr/bin/su": true, "/usr/bin/passwd": true,
		"/usr/bin/newgrp": true, "/usr/bin/chsh": true, "/usr/bin/chfn": true,
		"/usr/bin/gpasswd": true, "/bin/ping": true, "/usr/bin/pkexec": true,
		"/usr/lib/openssh/ssh-keysign": true,
		"/usr/lib/dbus-1.0/dbus-daemon-launch-helper": true,
	}
	suidOut, _ := exec.Command("find", "/", "-perm", "-4000", "-o", "-perm", "-2000", "-type", "f").Output()
	for _, line := range strings.Split(strings.TrimSpace(string(suidOut)), "\n") {
		if line == "" || baseline[line] {
			continue
		}
		findings = append(findings, map[string]any{
			"severity": "high", "condition_present": true, "tag": "unexpected-suid",
			"description": fmt.Sprintf("Unexpected SUID/SGID binary: %s", line),
			"remediation": fmt.Sprintf("Review: chmod u-s %s", line),
		})
	}

	// Writable cron dirs
	for _, dir := range []string{"/etc/cron.daily", "/etc/cron.weekly", "/etc/cron.monthly", "/etc/cron.d"} {
		if fi, err := os.Stat(dir); err == nil && fi.Mode()&0002 != 0 {
			findings = append(findings, map[string]any{
				"severity": "high", "condition_present": true, "tag": "writable-cron",
				"description": fmt.Sprintf("World-writable cron dir: %s", dir),
				"remediation": fmt.Sprintf("chmod o-w %s", dir),
			})
		}
	}

	// Writable service files
	svcOut, _ := exec.Command("find", "/etc/systemd", "/lib/systemd", "-perm", "-o+w", "-name", "*.service").Output()
	for _, line := range strings.Split(strings.TrimSpace(string(svcOut)), "\n") {
		if line == "" {
			continue
		}
		findings = append(findings, map[string]any{
			"severity": "high", "condition_present": true, "tag": "writable-service",
			"description": fmt.Sprintf("World-writable service file: %s", line),
			"remediation": fmt.Sprintf("chmod o-w %s", line),
		})
	}

	// Sudo rules review
	sudoOut, _ := exec.Command("sudo", "-l").Output()
	for _, line := range strings.Split(string(sudoOut), "\n") {
		if strings.Contains(line, "NOPASSWD") {
			findings = append(findings, map[string]any{
				"severity": "medium", "condition_present": true, "tag": "sudo-misconfigured",
				"description": fmt.Sprintf("NOPASSWD sudo rule: %s", strings.TrimSpace(line)),
				"remediation": "Review /etc/sudoers and /etc/sudoers.d/",
			})
		}
	}

	// Determine highest severity tag
	highestTag := "privesc-risk:medium"
	for _, f := range findings {
		if present, _ := f["condition_present"].(bool); !present {
			continue
		}
		if f["severity"] == "critical" {
			highestTag = "privesc-risk:critical"
			break
		}
		if f["severity"] == "high" {
			highestTag = "privesc-risk:high"
		}
	}

	return map[string]any{
		"findings":   findings,
		"total":      len(findings),
		"tags":       []string{highestTag},
		"audited_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func linuxKernelVersion() (major, minor, patch int) {
	out, _ := exec.Command("uname", "-r").Output()
	ver := strings.TrimSpace(string(out))
	parts := strings.SplitN(ver, ".", 3)
	if len(parts) >= 1 {
		major, _ = strconv.Atoi(parts[0])
	}
	if len(parts) >= 2 {
		minor, _ = strconv.Atoi(parts[1])
	}
	if len(parts) >= 3 {
		p := parts[2]
		if idx := strings.IndexAny(p, "-+"); idx != -1 {
			p = p[:idx]
		}
		patch, _ = strconv.Atoi(p)
	}
	return
}
```

- [ ] **Step 2: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/linuxauth/... && "C:/Program Files/Go/bin/go.exe" test ./commands/linuxauth/... -v
```

- [ ] **Step 3: Commit**

```
git add agent/commands/linuxauth/privesc_linux.go
git commit -m "feat(linuxauth): implement audit_privesc_vulnerabilities (read-only ingest)"
```

---

### Task 6: `manage_ca_certificates` implementation

**Files:**
- Create: `agent/commands/linuxauth/certs_linux.go`

- [ ] **Step 1: Create `agent/commands/linuxauth/certs_linux.go`**

```go
//go:build linux

package linuxauth

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func distroInstallPath(certName string) (string, []string) {
	if _, err := os.Stat("/etc/debian_version"); err == nil {
		return fmt.Sprintf("/usr/local/share/ca-certificates/%s.crt", certName),
			[]string{"update-ca-certificates"}
	}
	return fmt.Sprintf("/etc/pki/ca-trust/source/anchors/%s.crt", certName),
		[]string{"update-ca-trust", "extract"}
}

func certsExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("manage_ca_certificates requires root privileges")
	}
	action, _ := params["action"].(string)
	certName, _ := params["cert_name"].(string)
	certPEM, _ := params["certificate"].(string)

	installPath, updateCmd := distroInstallPath(certName)

	// Snapshot existing cert
	snapshot := ""
	if data, err := os.ReadFile(installPath); err == nil {
		snapshot = string(data)
	}

	var certSubject, certExpiry string

	if action == "install" {
		// Validate PEM via openssl
		tmp, err := os.CreateTemp("", "nexplane-cert-*.pem")
		if err != nil {
			return nil, fmt.Errorf("creating temp file: %w", err)
		}
		defer os.Remove(tmp.Name())
		if _, err := tmp.WriteString(certPEM); err != nil {
			return nil, fmt.Errorf("writing temp cert: %w", err)
		}
		tmp.Close()
		out, err := exec.Command("openssl", "x509", "-noout", "-subject", "-enddate", "-in", tmp.Name()).Output()
		if err != nil {
			return nil, fmt.Errorf("invalid PEM certificate: %w", err)
		}
		for _, line := range strings.Split(string(out), "\n") {
			if strings.HasPrefix(line, "subject=") {
				certSubject = strings.TrimPrefix(line, "subject=")
			}
			if strings.HasPrefix(line, "notAfter=") {
				certExpiry = strings.TrimPrefix(line, "notAfter=")
			}
		}
		if err := os.WriteFile(installPath, []byte(certPEM), 0644); err != nil {
			return nil, fmt.Errorf("writing cert: %w", err)
		}
	} else {
		if _, err := os.Stat(installPath); err != nil {
			return nil, fmt.Errorf("cert not found: %s", installPath)
		}
		if err := os.Remove(installPath); err != nil {
			return nil, fmt.Errorf("removing cert: %w", err)
		}
	}

	cmd := exec.Command(updateCmd[0], updateCmd[1:]...)
	if out, err := cmd.CombinedOutput(); err != nil {
		return nil, fmt.Errorf("trust store update failed: %s: %w", out, err)
	}

	return map[string]any{
		"action": action, "cert_path": installPath, "cert_name": certName,
		"cert_subject": certSubject, "cert_expiry": certExpiry,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func certsRollbackOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	certName, _ := params["cert_name"].(string)
	snapshot, _ := params["snapshot"].(string)

	installPath, updateCmd := distroInstallPath(certName)

	if action == "install" {
		os.Remove(installPath)
	} else {
		if snapshot == "" {
			return nil, fmt.Errorf("snapshot is required to restore removed cert")
		}
		if err := os.WriteFile(installPath, []byte(snapshot), 0644); err != nil {
			return nil, fmt.Errorf("restoring cert: %w", err)
		}
	}

	cmd := exec.Command(updateCmd[0], updateCmd[1:]...)
	if out, err := cmd.CombinedOutput(); err != nil {
		return nil, fmt.Errorf("trust store update failed: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true, "cert_path": installPath}, nil
}
```

- [ ] **Step 2: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/linuxauth/... && "C:/Program Files/Go/bin/go.exe" test ./commands/linuxauth/... -v
```

- [ ] **Step 3: Commit**

```
git add agent/commands/linuxauth/certs_linux.go
git commit -m "feat(linuxauth): implement manage_ca_certificates (Debian+RHEL trust store)"
```

---

### Task 7: `configure_ntp` implementation

**Files:**
- Create: `agent/commands/linuxauth/ntp_linux.go`

- [ ] **Step 1: Create `agent/commands/linuxauth/ntp_linux.go`**

```go
//go:build linux

package linuxauth

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func detectNTPDaemon() (configPath, svcName string) {
	for _, p := range []string{"/etc/chrony.conf", "/etc/chrony/chrony.conf"} {
		if _, err := os.Stat(p); err == nil {
			return p, "chronyd"
		}
	}
	if _, err := os.Stat("/etc/systemd/timesyncd.conf"); err == nil {
		return "/etc/systemd/timesyncd.conf", "systemd-timesyncd"
	}
	return "/etc/ntp.conf", "ntpd"
}

func ntpExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_ntp requires root privileges")
	}

	serversRaw, _ := params["servers"].([]any)
	servers := make([]string, 0, len(serversRaw))
	for _, s := range serversRaw {
		if str, ok := s.(string); ok {
			servers = append(servers, str)
		}
	}

	iburst := true
	if v, ok := params["require_iburst"].(bool); ok {
		iburst = v
	}
	makestep := "1.0 3"
	if v, ok := params["makestep"].(string); ok && v != "" {
		makestep = v
	}

	configPath, svcName := detectNTPDaemon()
	configData, _ := os.ReadFile(configPath)
	snapshot := string(configData)

	// Build server block
	var serverLines []string
	for _, s := range servers {
		line := "server " + s
		if iburst {
			line += " iburst"
		}
		serverLines = append(serverLines, line)
	}
	serverBlock := strings.Join(serverLines, "\n")

	content := snapshot
	if svcName == "chronyd" {
		// Remove existing server/pool lines, keep everything else
		var kept []string
		for _, line := range strings.Split(content, "\n") {
			if strings.HasPrefix(line, "server ") || strings.HasPrefix(line, "pool ") {
				continue
			}
			kept = append(kept, line)
		}
		content = strings.Join(kept, "\n")
		extra := ""
		if !strings.Contains(content, "makestep") {
			extra += "\nmakestep " + makestep
		}
		if !strings.Contains(content, "rtcsync") {
			extra += "\nrtcsync"
		}
		content = insertManagedBlock(content, serverBlock+extra)
	} else if svcName == "systemd-timesyncd" {
		// Upsert NTP= line in [Time] section
		lines := strings.Split(content, "\n")
		ntpLine := "NTP=" + strings.Join(servers, " ")
		replaced := false
		for i, l := range lines {
			if strings.HasPrefix(l, "NTP=") {
				lines[i] = ntpLine
				replaced = true
				break
			}
		}
		if !replaced {
			for i, l := range lines {
				if l == "[Time]" {
					lines = append(lines[:i+1], append([]string{ntpLine}, lines[i+1:]...)...)
					break
				}
			}
		}
		content = strings.Join(lines, "\n")
	} else {
		// ntpd: use managed block
		content = insertManagedBlock(content, serverBlock)
	}

	if err := os.WriteFile(configPath, []byte(content), 0644); err != nil {
		return nil, fmt.Errorf("writing NTP config: %w", err)
	}
	exec.Command("systemctl", "restart", svcName).Run() //nolint

	return map[string]any{
		"daemon": svcName, "config_path": configPath,
		"servers_configured": servers,
		"snapshot":           snapshot,
		"applied_at":         time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ntpRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	configPath, _ := params["config_path"].(string)
	if configPath == "" {
		configPath, _ = detectNTPDaemon()
	}
	if err := os.WriteFile(configPath, []byte(snapshot), 0644); err != nil {
		return nil, fmt.Errorf("restoring NTP config: %w", err)
	}
	_, svcName := detectNTPDaemon()
	exec.Command("systemctl", "restart", svcName).Run() //nolint
	return map[string]any{"rolled_back": true, "config_path": configPath}, nil
}
```

- [ ] **Step 2: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/linuxauth/... && "C:/Program Files/Go/bin/go.exe" test ./commands/linuxauth/... -v
```

- [ ] **Step 3: Commit**

```
git add agent/commands/linuxauth/ntp_linux.go
git commit -m "feat(linuxauth): implement configure_ntp (chrony/timesyncd/ntpd)"
```

---

### Task 8: Register all 6 commands in `executor.go`

**Files:**
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Add linuxauth import and register commands**

In `agent/executor/executor.go`, add the import and entries:

```go
import (
    // existing imports ...
    "nexplane-agent/commands/linuxauth"
)

var commands = map[string]CommandFunc{
    // existing entries ...
    "configure_pam":                   linuxauth.ConfigurePAMExecute,
    "harden_ssh":                      linuxauth.HardenSSHExecute,
    "audit_users_and_groups":          linuxauth.AuditUsersAndGroupsExecute,
    "audit_privesc_vulnerabilities":   linuxauth.AuditPrivescVulnerabilitiesExecute,
    "manage_ca_certificates":          linuxauth.ManageCACertificatesExecute,
    "configure_ntp":                   linuxauth.ConfigureNTPExecute,
}

var rollbacks = map[string]CommandFunc{
    // existing entries ...
    "configure_pam":          linuxauth.ConfigurePAMRollback,
    "harden_ssh":             linuxauth.HardenSSHRollback,
    "manage_ca_certificates": linuxauth.ManageCACertificatesRollback,
    "configure_ntp":          linuxauth.ConfigureNTPRollback,
}
```

The full updated `agent/executor/executor.go` should look like:

```go
package executor

import (
	"fmt"

	"nexplane-agent/commands/changip"
	"nexplane-agent/commands/configsyslog"
	"nexplane-agent/commands/estimatesize"
	"nexplane-agent/commands/linuxauth"
	"nexplane-agent/commands/uploadimage"
	"nexplane-agent/commands/virtualize"
)

// Result is the outcome of a command execution.
type Result struct {
	Status string
	Data   map[string]any
	Error  string
}

// CommandFunc is the signature every command must implement.
type CommandFunc func(params map[string]any) (map[string]any, error)

var commands = map[string]CommandFunc{
	"estimate_image_size":              estimatesize.Execute,
	"change_ip":                        changip.Execute,
	"configure_syslog":                 configsyslog.Execute,
	"virtualize_for_migration":         virtualize.Execute,
	"upload_image":                     uploadimage.Execute,
	"configure_pam":                    linuxauth.ConfigurePAMExecute,
	"harden_ssh":                       linuxauth.HardenSSHExecute,
	"audit_users_and_groups":           linuxauth.AuditUsersAndGroupsExecute,
	"audit_privesc_vulnerabilities":    linuxauth.AuditPrivescVulnerabilitiesExecute,
	"manage_ca_certificates":           linuxauth.ManageCACertificatesExecute,
	"configure_ntp":                    linuxauth.ConfigureNTPExecute,
}

var rollbacks = map[string]CommandFunc{
	"change_ip":               changip.Rollback,
	"configure_syslog":        configsyslog.Rollback,
	"virtualize_for_migration": virtualize.Rollback,
	"upload_image":            uploadimage.Rollback,
	"configure_pam":           linuxauth.ConfigurePAMRollback,
	"harden_ssh":              linuxauth.HardenSSHRollback,
	"manage_ca_certificates":  linuxauth.ManageCACertificatesRollback,
	"configure_ntp":           linuxauth.ConfigureNTPRollback,
}

// Dispatch routes a command to its implementation.
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

- [ ] **Step 2: Build entire agent**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./...
```

Expected: no output (success)

- [ ] **Step 3: Run all tests**

```
cd agent && "C:/Program Files/Go/bin/go.exe" test ./... -v
```

Expected: all packages PASS

- [ ] **Step 4: Commit**

```
git add agent/executor/executor.go
git commit -m "feat(executor): register 6 linuxauth commands and rollbacks"
```

---

### Task 9: Catalog entries in `nexplane_agent_mock.json`

**Files:**
- Modify: `backend/app/connectors/catalog/nexplane_agent_mock.json`

- [ ] **Step 1: Add 6 catalog entries to the `actions` array**

Append these 6 entries to the `actions` array in `backend/app/connectors/catalog/nexplane_agent_mock.json`:

```json
{
  "action_id": "configure_pam",
  "generic_action": "configure_pam",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Configure PAM Hardening",
  "description": "Applies CIS/STIG PAM policies: password complexity, account lockout, session timeout. Supports pam_pwquality and pam_faillock (modern) or pam_cracklib and pam_tally2 (legacy).",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "profile", "type": "string", "required": false, "default": "cis_level1"},
    {"name": "params", "type": "object", "required": false}
  ],
  "executor": "nexplane_agent_mock.configure_pam",
  "rollback_action": "configure_pam",
  "estimated_duration_seconds": 15,
  "safety_notes": [
    "Misconfigured PAM can lock out all users including root — test in non-production first",
    "lockout_duration_seconds applies to all accounts; ensure break-glass access exists"
  ]
},
{
  "action_id": "harden_ssh",
  "generic_action": "harden_ssh",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Harden SSH Configuration",
  "description": "Writes /etc/ssh/sshd_config.d/99-nexplane-hardening.conf with secure defaults: no root login, key-only auth, cipher/MAC allowlist, idle timeout. Validates with sshd -t before reloading.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "settings", "type": "object", "required": false}
  ],
  "executor": "nexplane_agent_mock.harden_ssh",
  "rollback_action": "harden_ssh",
  "estimated_duration_seconds": 10,
  "safety_notes": [
    "Disabling password_authentication requires key-based auth to be working first",
    "Changing SSH port requires updating external firewall rules before reloading"
  ]
},
{
  "action_id": "audit_users_and_groups",
  "generic_action": "audit_users_and_groups",
  "action_type": "ingest",
  "execution_tier": 3,
  "display_name": "Audit Users and Groups",
  "description": "Read-only audit: surfaces accounts with no password expiry, UID 0 non-root accounts, service accounts with interactive shells, empty passwords, and sudo group members.",
  "applicable_asset_types": ["server", "identity"],
  "parameters": [],
  "executor": "nexplane_agent_mock.audit_users_and_groups",
  "estimated_duration_seconds": 10
},
{
  "action_id": "audit_privesc_vulnerabilities",
  "generic_action": "audit_privesc_vulnerabilities",
  "action_type": "ingest",
  "execution_tier": 3,
  "display_name": "Audit Privilege Escalation Vulnerabilities",
  "description": "Detection-only check for known Linux LPE conditions: PwnKit (CVE-2021-4034), DirtyPipe (CVE-2022-0847), Dirty COW (CVE-2016-5195), unexpected SUID binaries, writable cron/service files, misconfigured sudo rules.",
  "applicable_asset_types": ["server"],
  "parameters": [],
  "executor": "nexplane_agent_mock.audit_privesc_vulnerabilities",
  "estimated_duration_seconds": 30
},
{
  "action_id": "manage_ca_certificates",
  "generic_action": "manage_ca_certificates",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Manage CA Certificates",
  "description": "Installs or removes CA certificates in the OS trust store. Detects Debian (update-ca-certificates) vs RHEL (update-ca-trust extract). Validates PEM before installing.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "action", "type": "string", "required": true},
    {"name": "certificate", "type": "string", "required": false},
    {"name": "cert_name", "type": "string", "required": true}
  ],
  "executor": "nexplane_agent_mock.manage_ca_certificates",
  "rollback_action": "manage_ca_certificates",
  "estimated_duration_seconds": 10,
  "safety_notes": [
    "Installing an untrusted CA certificate compromises TLS security for all processes on this host"
  ]
},
{
  "action_id": "configure_ntp",
  "generic_action": "configure_ntp",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Configure NTP Time Sources",
  "description": "Configures NTP servers via chrony, systemd-timesyncd, or ntpd (auto-detected). Critical for audit log timestamp integrity. Supports iburst and chrony makestep.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "servers", "type": "array", "required": true},
    {"name": "require_iburst", "type": "boolean", "required": false, "default": true},
    {"name": "makestep", "type": "string", "required": false, "default": "1.0 3"}
  ],
  "executor": "nexplane_agent_mock.configure_ntp",
  "rollback_action": "configure_ntp",
  "estimated_duration_seconds": 10,
  "safety_notes": [
    "NTP misconfiguration causes clock drift which invalidates audit log timestamps"
  ]
}
```

- [ ] **Step 2: Validate JSON**

```
python -c "import json; json.load(open('backend/app/connectors/catalog/nexplane_agent_mock.json')); print('valid')"
```

Expected: `valid`

- [ ] **Step 3: Commit**

```
git add backend/app/connectors/catalog/nexplane_agent_mock.json
git commit -m "feat(catalog): add 6 linuxauth actions to nexplane_agent_mock catalog"
```

---

### Task 10: Mock executor stubs

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent_mock/configure_pam.py`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/harden_ssh.py`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/audit_users_and_groups.py`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/audit_privesc_vulnerabilities.py`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/manage_ca_certificates.py`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/configure_ntp.py`

- [ ] **Step 1: Create `configure_pam.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "configure_pam",
        "profile_applied": parameters.get("profile", "cis_level1"),
        "pam_variant": {"pw_module": "pam_pwquality", "fail_module": "pam_faillock"},
        "params_applied": {"min_len": 14, "max_failed": 5, "lockout_secs": 900, "remember": 5},
        "files_snapshot": {
            "/etc/security/pwquality.conf": "# previous content",
            "/etc/pam.d/common-auth": "# previous content",
        },
        "applied": True,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_pam", "restored_files": 5}
```

- [ ] **Step 2: Create `harden_ssh.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "harden_ssh",
        "settings_applied": {
            "PermitRootLogin": "no",
            "PasswordAuthentication": "no",
            "PubkeyAuthentication": "yes",
            "MaxAuthTries": "4",
        },
        "drop_in_path": "/etc/ssh/sshd_config.d/99-nexplane-hardening.conf",
        "sshd_config_snapshot": {"/etc/ssh/sshd_config": "# previous sshd_config"},
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "harden_ssh"}
```

- [ ] **Step 3: Create `audit_users_and_groups.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "audit_users_and_groups",
        "findings": [
            {"user": "backup", "tag": "svc-interactive-shell",
             "description": "Service account 'backup' (uid=34) has interactive shell '/bin/bash'"},
        ],
        "total": 1,
        "tags": ["users-audit-findings"],
        "audited_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "audit_users_and_groups is read-only"}
```

- [ ] **Step 4: Create `audit_privesc_vulnerabilities.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "audit_privesc_vulnerabilities",
        "findings": [
            {"cve_id": "CVE-2021-4034", "severity": "critical", "condition_present": True,
             "description": "PwnKit: polkit pkexec local privilege escalation",
             "remediation": "Upgrade polkit package"},
            {"cve_id": "CVE-2022-0847", "severity": "high", "condition_present": False,
             "description": "DirtyPipe: kernel 5.19.0 not vulnerable",
             "remediation": "No action needed"},
        ],
        "total": 2,
        "tags": ["privesc-risk:critical"],
        "audited_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "audit_privesc_vulnerabilities is read-only"}
```

- [ ] **Step 5: Create `manage_ca_certificates.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    action = parameters.get("action", "install")
    cert_name = parameters.get("cert_name", "unknown")
    return {
        "action": action,
        "cert_path": f"/usr/local/share/ca-certificates/{cert_name}.crt",
        "cert_name": cert_name,
        "cert_subject": "CN=Example CA, O=Example Corp",
        "cert_expiry": "Dec 31 23:59:59 2030 GMT",
        "snapshot": "# previous cert or empty",
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "manage_ca_certificates",
            "cert_path": execution_result.get("cert_path")}
```

- [ ] **Step 6: Create `configure_ntp.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "configure_ntp",
        "daemon": "chronyd",
        "config_path": "/etc/chrony.conf",
        "servers_configured": parameters.get("servers", ["time.cloudflare.com"]),
        "snapshot": "# previous chrony.conf content",
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "configure_ntp",
            "config_path": execution_result.get("config_path")}
```

- [ ] **Step 7: Run backend catalog tests**

```
cd backend && python -m pytest app/tests/test_catalog_service.py -v
```

Expected: PASS

- [ ] **Step 8: Commit**

```
git add backend/app/connectors/executors/nexplane_agent_mock/configure_pam.py
git add backend/app/connectors/executors/nexplane_agent_mock/harden_ssh.py
git add backend/app/connectors/executors/nexplane_agent_mock/audit_users_and_groups.py
git add backend/app/connectors/executors/nexplane_agent_mock/audit_privesc_vulnerabilities.py
git add backend/app/connectors/executors/nexplane_agent_mock/manage_ca_certificates.py
git add backend/app/connectors/executors/nexplane_agent_mock/configure_ntp.py
git commit -m "feat(mock): add 6 linuxauth mock executor stubs"
```
