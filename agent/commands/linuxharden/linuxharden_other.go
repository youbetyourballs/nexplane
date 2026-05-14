//go:build !linux

package linuxharden

import "fmt"

func seccompLearnExecute(params map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("seccomp_learn is Linux-only")
}

func seccompLearnRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "seccomp_learn_rollback", "status": "no_state_to_revert"}, nil
}

func iptablesLogBaselineExecute(params map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("iptables_log_baseline is Linux-only")
}

func iptablesLogBaselineRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "iptables_log_baseline_rollback", "status": "rules_already_removed"}, nil
}
