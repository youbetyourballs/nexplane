//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func firewallExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_host_firewall requires root privileges")
	}
	tool := detectFirewallTool()
	snapshot := captureFirewallSnapshot(tool)
	action, _ := params["action"].(string)

	switch tool {
	case "nftables":
		return nftablesAction(action, params, snapshot)
	case "firewalld":
		return firewalldAction(action, params, snapshot)
	default:
		return iptablesAction(action, params, snapshot)
	}
}

func detectFirewallTool() string {
	if _, err := exec.LookPath("firewall-cmd"); err == nil {
		if exec.Command("systemctl", "is-active", "firewalld").Run() == nil {
			return "firewalld"
		}
	}
	if _, err := exec.LookPath("nft"); err == nil {
		return "nftables"
	}
	return "iptables"
}

func captureFirewallSnapshot(tool string) string {
	switch tool {
	case "nftables":
		out, _ := exec.Command("nft", "list", "ruleset").Output()
		return string(out)
	case "firewalld":
		out, _ := exec.Command("firewall-cmd", "--list-all").Output()
		return string(out)
	default:
		out, _ := exec.Command("iptables-save").Output()
		return string(out)
	}
}

func iptablesAction(action string, params map[string]any, snapshot string) (map[string]any, error) {
	rule, _ := params["rule"].(map[string]any)
	var args []string
	switch action {
	case "add_rule":
		args = buildIPTablesArgs("-A", rule)
	case "remove_rule":
		args = buildIPTablesArgs("-D", rule)
	case "flush":
		args = []string{"-F"}
	}
	if len(args) > 0 {
		if out, err := exec.Command("iptables", args...).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("iptables %v: %s: %w", args, out, err)
		}
	}
	return map[string]any{
		"tool": "iptables", "action": action,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func buildIPTablesArgs(flag string, rule map[string]any) []string {
	if rule == nil {
		return nil
	}
	chain, _ := rule["chain"].(string)
	if chain == "" {
		chain = "INPUT"
	}
	args := []string{flag, chain}
	if proto, _ := rule["protocol"].(string); proto != "" {
		args = append(args, "-p", proto)
	}
	if port, _ := rule["port"].(string); port != "" {
		args = append(args, "--dport", port)
	}
	if src, _ := rule["source"].(string); src != "" {
		args = append(args, "-s", src)
	}
	actionType, _ := rule["action_type"].(string)
	if actionType == "" {
		actionType = "ACCEPT"
	}
	args = append(args, "-j", strings.ToUpper(actionType))
	return args
}

func nftablesAction(action string, params map[string]any, snapshot string) (map[string]any, error) {
	if action == "flush" {
		exec.Command("nft", "flush", "ruleset").Run() //nolint:errcheck
	}
	return map[string]any{
		"tool": "nftables", "action": action,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func firewalldAction(action string, params map[string]any, snapshot string) (map[string]any, error) {
	rule, _ := params["rule"].(map[string]any)
	switch action {
	case "add_rule":
		if port, _ := rule["port"].(string); port != "" {
			proto, _ := rule["protocol"].(string)
			if proto == "" {
				proto = "tcp"
			}
			exec.Command("firewall-cmd", "--permanent", "--add-port="+port+"/"+proto).Run()   //nolint:errcheck
			exec.Command("firewall-cmd", "--reload").Run()                                      //nolint:errcheck
		}
	case "remove_rule":
		if port, _ := rule["port"].(string); port != "" {
			proto, _ := rule["protocol"].(string)
			if proto == "" {
				proto = "tcp"
			}
			exec.Command("firewall-cmd", "--permanent", "--remove-port="+port+"/"+proto).Run() //nolint:errcheck
			exec.Command("firewall-cmd", "--reload").Run()                                      //nolint:errcheck
		}
	case "flush":
		exec.Command("firewall-cmd", "--permanent", "--complete-reload").Run() //nolint:errcheck
	}
	return map[string]any{
		"tool": "firewalld", "action": action,
		"snapshot": snapshot, "applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func firewallRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok || snapshot == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	tool, _ := params["tool"].(string)
	switch tool {
	case "nftables":
		cmd := exec.Command("nft", "-f", "-")
		cmd.Stdin = strings.NewReader(snapshot)
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("restoring nftables: %s: %w", out, err)
		}
	case "firewalld":
		exec.Command("firewall-cmd", "--complete-reload").Run() //nolint:errcheck
	default:
		cmd := exec.Command("iptables-restore")
		cmd.Stdin = strings.NewReader(snapshot)
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("iptables-restore: %s: %w", out, err)
		}
	}
	return map[string]any{"rolled_back": true}, nil
}
