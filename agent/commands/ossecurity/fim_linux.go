//go:build linux

package ossecurity

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func fimExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action == "" {
		action = "init"
	}
	tool := detectFIMTool()
	switch tool {
	case "aide":
		return aideAction(action)
	case "tripwire":
		return tripwireAction(action)
	default:
		return nil, fmt.Errorf("no file integrity monitoring tool found (aide or tripwire required)")
	}
}

func detectFIMTool() string {
	if _, err := exec.LookPath("aide"); err == nil {
		return "aide"
	}
	if _, err := exec.LookPath("tripwire"); err == nil {
		return "tripwire"
	}
	return ""
}

func aideAction(action string) (map[string]any, error) {
	switch action {
	case "init":
		if out, err := exec.Command("aide", "--init").CombinedOutput(); err != nil {
			return nil, fmt.Errorf("aide --init: %s: %w", out, err)
		}
		exec.Command("mv", "/var/lib/aide/aide.db.new.gz", "/var/lib/aide/aide.db.gz").Run() //nolint:errcheck
		return map[string]any{
			"tool": "aide", "action": "init",
			"database_path": "/var/lib/aide/aide.db.gz",
			"applied_at":    time.Now().UTC().Format(time.RFC3339),
		}, nil
	case "check":
		out, err := exec.Command("aide", "--check").CombinedOutput()
		return map[string]any{
			"tool": "aide", "action": "check",
			"output":     string(out),
			"violations": err != nil,
			"checked_at": time.Now().UTC().Format(time.RFC3339),
		}, nil
	default:
		return nil, fmt.Errorf("unknown action %q: must be init or check", action)
	}
}

func tripwireAction(action string) (map[string]any, error) {
	switch action {
	case "init":
		if out, err := exec.Command("tripwire", "--init").CombinedOutput(); err != nil {
			return nil, fmt.Errorf("tripwire --init: %s: %w", out, err)
		}
		return map[string]any{
			"tool": "tripwire", "action": "init",
			"applied_at": time.Now().UTC().Format(time.RFC3339),
		}, nil
	case "check":
		out, _ := exec.Command("tripwire", "--check").CombinedOutput()
		return map[string]any{
			"tool": "tripwire", "action": "check",
			"output":     string(out),
			"violations": strings.Contains(string(out), "Violations:"),
			"checked_at": time.Now().UTC().Format(time.RFC3339),
		}, nil
	default:
		return nil, fmt.Errorf("unknown action %q: must be init or check", action)
	}
}

func fimRollbackOS(params map[string]any) (map[string]any, error) {
	tool := detectFIMTool()
	switch tool {
	case "aide":
		exec.Command("rm", "-f", "/var/lib/aide/aide.db.gz").Run() //nolint:errcheck
	case "tripwire":
		exec.Command("bash", "-c", "rm -f /var/lib/tripwire/*.twd").Run() //nolint:errcheck
	}
	return map[string]any{"rolled_back": true, "tool": tool}, nil
}
