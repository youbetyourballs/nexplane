//go:build linux

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const (
	lsmRulesPath = "/etc/audit/rules.d/nexplane-lsm.rules"
	lsmModeFile  = "/etc/nexplane/ebpf/ebpf_lsm-mode"
)

func ConfigureEbpfLsmExecute(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if mode == "" {
		return nil, fmt.Errorf("mode is required (audit or enforce)")
	}
	if mode != "audit" && mode != "enforce" {
		return nil, fmt.Errorf("mode must be 'audit' or 'enforce'")
	}

	os.MkdirAll("/etc/nexplane/ebpf", 0755)    //nolint:errcheck
	os.MkdirAll("/etc/audit/rules.d", 0755)    //nolint:errcheck

	profile, _ := params["profile"].(map[string]any)
	rules, _ := profile["rules"].([]any)

	var auditRules []string
	for _, r := range rules {
		rule, ok := r.(map[string]any)
		if !ok {
			continue
		}
		syscall, _ := rule["syscall"].(string)
		if syscall == "" {
			continue
		}
		auditRules = append(auditRules, fmt.Sprintf("-a always,exit -F arch=b64 -S %s -k nexplane-lsm", syscall))
	}

	rulesContent := strings.Join(auditRules, "\n") + "\n"
	if err := os.WriteFile(lsmRulesPath, []byte(rulesContent), 0644); err != nil {
		return nil, fmt.Errorf("writing auditd rules: %w", err)
	}
	exec.Command("augenrules", "--load").Run() //nolint:errcheck

	// Detect kernel LSM from /sys
	kernelLSM := detectKernelLSM()

	// Record current mode for promote command
	os.WriteFile(lsmModeFile, []byte(mode), 0644) //nolint:errcheck

	return map[string]any{
		"mode":        mode,
		"rules_count": len(auditRules),
		"snapshot_id": lsmRulesPath,
		"kernel_lsm":  kernelLSM,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ConfigureEbpfLsmRollback(params map[string]any) (map[string]any, error) {
	os.Remove(lsmRulesPath)
	exec.Command("augenrules", "--load").Run() //nolint:errcheck
	os.WriteFile(lsmModeFile, []byte(""), 0644) //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}

func detectKernelLSM() string {
	data, err := os.ReadFile("/sys/kernel/security/lsm")
	if err == nil {
		return strings.TrimSpace(string(data))
	}
	// Fallback: check which LSM modules are active
	if _, err := os.Stat("/sys/kernel/security/apparmor"); err == nil {
		return "apparmor"
	}
	if _, err := os.Stat("/sys/kernel/security/selinux"); err == nil {
		return "selinux"
	}
	return "unknown"
}
