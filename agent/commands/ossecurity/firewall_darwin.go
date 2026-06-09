//go:build darwin

package ossecurity

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

const socketFilterFW = "/usr/libexec/ApplicationFirewall/socketfilterfw"

var execCommandFirewall = exec.Command

func firewallExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)

	snapOut, _ := execCommandFirewall(socketFilterFW, "--getglobalstate").Output()
	snapshot := strings.TrimSpace(string(snapOut))

	rule, _ := params["rule"].(map[string]any)

	switch action {
	case "add_rule":
		if appPath, _ := rule["app_path"].(string); appPath != "" {
			if out, err := execCommandFirewall(socketFilterFW, "--add", appPath).CombinedOutput(); err != nil {
				return nil, fmt.Errorf("socketfilterfw --add: %s: %w", out, err)
			}
			execCommandFirewall(socketFilterFW, "--unblockapp", appPath).Run() //nolint:errcheck
		} else if port, _ := rule["port"].(string); port != "" {
			proto, _ := rule["protocol"].(string)
			if proto == "" {
				proto = "tcp"
			}
			anchor := fmt.Sprintf("pass in proto %s to any port %s\n", proto, port)
			if err := addPFAnchorRule(anchor); err != nil {
				return nil, err
			}
		}
	case "remove_rule":
		if appPath, _ := rule["app_path"].(string); appPath != "" {
			execCommandFirewall(socketFilterFW, "--remove", appPath).Run() //nolint:errcheck
		}
	case "flush":
		execCommandFirewall(socketFilterFW, "--setglobalstate", "off").Run() //nolint:errcheck
		execCommandFirewall(socketFilterFW, "--setglobalstate", "on").Run()  //nolint:errcheck
		removePFAnchor()
	case "enable":
		if out, err := execCommandFirewall(socketFilterFW, "--setglobalstate", "on").CombinedOutput(); err != nil {
			return nil, fmt.Errorf("socketfilterfw --setglobalstate on: %s: %w", out, err)
		}
		execCommandFirewall(socketFilterFW, "--setstealthmode", "on").Run() //nolint:errcheck
	}

	return map[string]any{
		"tool":       "socketfilterfw",
		"action":     action,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func firewallRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)

	if strings.Contains(snapshot, "disabled") || strings.Contains(snapshot, "State = 0") {
		execCommandFirewall(socketFilterFW, "--setglobalstate", "off").Run() //nolint:errcheck
	} else {
		execCommandFirewall(socketFilterFW, "--setglobalstate", "on").Run() //nolint:errcheck
	}
	removePFAnchor()

	return map[string]any{"rolled_back": true}, nil
}

func addPFAnchorRule(rule string) error {
	content := "anchor \"nexplane\"\n" + rule
	cmd := execCommandFirewall("pfctl", "-a", "nexplane", "-f", "-")
	cmd.Stdin = strings.NewReader(content)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("pfctl anchor: %s: %w", out, err)
	}
	return nil
}

func removePFAnchor() {
	execCommandFirewall("pfctl", "-a", "nexplane", "-F", "rules").Run() //nolint:errcheck
}
