//go:build linux

package linuxharden

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func iptablesLogBaselineExecute(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}
	logPrefix, _ := params["log_prefix"].(string)
	if logPrefix == "" {
		logPrefix = "NEXPLANE-BASELINE"
	}

	// Insert LOG rules before ACCEPT/DROP in INPUT and OUTPUT
	cmds := [][]string{
		{"iptables", "-I", "INPUT", "1", "-j", "LOG", "--log-prefix", logPrefix + "-IN: ", "--log-level", "4"},
		{"iptables", "-I", "OUTPUT", "1", "-j", "LOG", "--log-prefix", logPrefix + "-OUT: ", "--log-level", "4"},
		{"iptables", "-I", "FORWARD", "1", "-j", "LOG", "--log-prefix", logPrefix + "-FWD: ", "--log-level", "4"},
	}
	for _, args := range cmds {
		if out, err := exec.Command(args[0], args[1:]...).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("iptables LOG rule: %s: %w", string(out), err)
		}
	}

	time.Sleep(time.Duration(duration) * time.Second)

	// Collect logged connections
	out, _ := exec.Command("dmesg").Output()
	var connections []string
	for _, line := range strings.Split(string(out), "\n") {
		if strings.Contains(line, logPrefix) {
			connections = append(connections, strings.TrimSpace(line))
		}
	}

	// Remove LOG rules
	cleanCmds := [][]string{
		{"iptables", "-D", "INPUT", "1"},
		{"iptables", "-D", "OUTPUT", "1"},
		{"iptables", "-D", "FORWARD", "1"},
	}
	for _, args := range cleanCmds {
		exec.Command(args[0], args[1:]...).Run()
	}

	return map[string]any{
		"action":           "iptables_log_baseline",
		"duration_seconds": int(duration),
		"connections_seen": connections,
		"connection_count": len(connections),
		"log_prefix":       logPrefix,
	}, nil
}

func iptablesLogBaselineRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "iptables_log_baseline_rollback", "status": "rules_already_removed"}, nil
}
