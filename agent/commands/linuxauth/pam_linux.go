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
