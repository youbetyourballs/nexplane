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

	snapshot := ""
	if data, err := os.ReadFile(auditRulesPath); err == nil {
		snapshot = string(data)
	}

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

	if err := os.MkdirAll("/etc/audit/rules.d", 0750); err != nil {
		return nil, fmt.Errorf("creating rules dir: %w", err)
	}
	if err := os.WriteFile(auditRulesPath, []byte(rulesContent), 0640); err != nil {
		return nil, fmt.Errorf("writing audit rules: %w", err)
	}

	if out, err := exec.Command("augenrules", "--load").CombinedOutput(); err != nil {
		exec.Command("auditctl", "-R", auditRulesPath).Run() //nolint:errcheck
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
	exec.Command("augenrules", "--load").Run() //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}
