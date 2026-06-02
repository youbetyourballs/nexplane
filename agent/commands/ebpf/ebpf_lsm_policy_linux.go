//go:build linux

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const lsmRulesPath = "/etc/audit/rules.d/nexplane-lsm.rules"

func ConfigureEbpfLsmExecute(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if mode == "" {
		return nil, fmt.Errorf("mode is required (audit or enforce)")
	}
	if mode != "audit" && mode != "enforce" {
		return nil, fmt.Errorf("mode must be 'audit' or 'enforce'")
	}

	snapshot := ""
	if data, err := os.ReadFile(lsmRulesPath); err == nil {
		snapshot = string(data)
	}

	profile, _ := params["profile"].(map[string]any)
	events, _ := profile["events"].([]any)

	var rules []string
	for _, e := range events {
		ev, ok := e.(map[string]any)
		if !ok {
			continue
		}
		raw, _ := ev["raw"].(string)
		syscall := "open"
		for _, part := range strings.Fields(raw) {
			if strings.HasPrefix(part, "syscall=") {
				syscall = strings.TrimPrefix(part, "syscall=")
				break
			}
		}
		rules = append(rules, fmt.Sprintf("-a always,exit -F arch=b64 -S %s -k nexplane-lsm", syscall))
	}

	rulesContent := strings.Join(rules, "\n") + "\n"
	os.MkdirAll("/etc/audit/rules.d", 0755) //nolint:errcheck
	if err := os.WriteFile(lsmRulesPath, []byte(rulesContent), 0644); err != nil {
		return nil, fmt.Errorf("writing auditd rules: %w", err)
	}
	exec.Command("augenrules", "--load").Run() //nolint:errcheck

	return map[string]any{
		"mode":        mode,
		"rules_count": len(rules),
		"rules_path":  lsmRulesPath,
		"snapshot":    snapshot,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ConfigureEbpfLsmRollback(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	if snapshot == "" {
		os.Remove(lsmRulesPath)
	} else {
		os.WriteFile(lsmRulesPath, []byte(snapshot), 0644) //nolint:errcheck
	}
	exec.Command("augenrules", "--load").Run() //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}
