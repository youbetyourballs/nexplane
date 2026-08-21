//go:build linux

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package drift

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"sort"
	"strings"
)

func restoreDriftState(params map[string]any) (map[string]any, error) {
	surfaceType, _ := params["surface_type"].(string)
	targetState, ok := params["target_state"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("target_state is required and must be a map")
	}

	switch surfaceType {
	case "ssh_config":
		return restoreSSHConfig(targetState)
	case "sudoers":
		return restoreSudoers(targetState)
	case "cron_jobs":
		return restoreCronJobs(targetState)
	case "firewall_rules":
		return restoreFirewallRules(targetState)
	case "running_services":
		return restoreRunningServices(targetState)
	case "listening_ports", "users_groups":
		// Derived / complex surfaces — not directly restorable by the agent
		return map[string]any{
			"status":       "success",
			"surface_type": surfaceType,
			"note":         surfaceType + " restoration requires manual intervention",
		}, nil
	default:
		return nil, fmt.Errorf("unsupported surface_type for restore: %s", surfaceType)
	}
}

func restoreSSHConfig(state map[string]any) (map[string]any, error) {
	keys := make([]string, 0, len(state))
	for k := range state {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	var sb strings.Builder
	for _, k := range keys {
		fmt.Fprintf(&sb, "%s %v\n", k, state[k])
	}

	if err := os.WriteFile("/etc/ssh/sshd_config", []byte(sb.String()), 0600); err != nil {
		return nil, fmt.Errorf("writing sshd_config: %w", err)
	}
	exec.Command("systemctl", "reload", "sshd").Run() //nolint:errcheck

	return map[string]any{"status": "success", "surface_type": "ssh_config"}, nil
}

func restoreSudoers(state map[string]any) (map[string]any, error) {
	rawEntries, _ := state["entries"].([]any)

	var lines []string
	lines = append(lines,
		"# Restored by Nexplane drift remediation",
		"Defaults\tenv_reset",
		"Defaults\tmail_badpass",
		`Defaults\tsecure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"`,
		"",
	)
	for _, e := range rawEntries {
		if s, ok := e.(string); ok {
			lines = append(lines, s)
		}
	}
	content := strings.Join(lines, "\n") + "\n"

	tmp, err := os.CreateTemp("", "nexplane-sudoers-*")
	if err != nil {
		return nil, fmt.Errorf("creating temp file: %w", err)
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)

	if _, err := tmp.WriteString(content); err != nil {
		tmp.Close()
		return nil, fmt.Errorf("writing temp sudoers: %w", err)
	}
	tmp.Close()

	if out, err := exec.Command("visudo", "-c", "-f", tmpName).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("visudo validation failed: %s: %w", out, err)
	}

	data, _ := os.ReadFile(tmpName)
	if err := os.WriteFile("/etc/sudoers", data, 0440); err != nil {
		return nil, fmt.Errorf("writing /etc/sudoers: %w", err)
	}

	return map[string]any{"status": "success", "surface_type": "sudoers"}, nil
}

func restoreCronJobs(state map[string]any) (map[string]any, error) {
	rawEntries, _ := state["entries"].([]any)

	var lines []string
	lines = append(lines, "# Restored by Nexplane drift remediation")
	for _, e := range rawEntries {
		if s, ok := e.(string); ok {
			lines = append(lines, s)
		}
	}
	content := strings.Join(lines, "\n") + "\n"

	if err := os.WriteFile("/etc/cron.d/nexplane-managed", []byte(content), 0644); err != nil {
		return nil, fmt.Errorf("writing cron file: %w", err)
	}
	return map[string]any{"status": "success", "surface_type": "cron_jobs"}, nil
}

func restoreFirewallRules(state map[string]any) (map[string]any, error) {
	tool, _ := state["tool"].(string)
	rules, _ := state["rules"].(string)

	switch tool {
	case "nftables":
		cmd := exec.Command("nft", "-f", "-")
		cmd.Stdin = strings.NewReader(rules)
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("restoring nftables: %s: %w", out, err)
		}
	default:
		cmd := exec.Command("iptables-restore")
		cmd.Stdin = strings.NewReader(rules)
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("iptables-restore: %s: %w", out, err)
		}
	}
	return map[string]any{"status": "success", "surface_type": "firewall_rules", "tool": tool}, nil
}

func restoreRunningServices(state map[string]any) (map[string]any, error) {
	rawServices, _ := state["services"].([]any)
	targetSet := map[string]bool{}
	for _, s := range rawServices {
		if name, ok := s.(string); ok {
			targetSet[name] = true
		}
	}

	out, _ := exec.Command("systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--no-pager").Output()
	currentSet := map[string]bool{}
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) > 0 {
			currentSet[fields[0]] = true
		}
	}

	var started, stopped []string
	for name := range targetSet {
		if !currentSet[name] {
			exec.Command("systemctl", "start", name).Run() //nolint:errcheck
			started = append(started, name)
		}
	}
	for name := range currentSet {
		if !targetSet[name] {
			exec.Command("systemctl", "stop", name).Run() //nolint:errcheck
			stopped = append(stopped, name)
		}
	}

	return map[string]any{
		"status":       "success",
		"surface_type": "running_services",
		"started":      started,
		"stopped":      stopped,
	}, nil
}
