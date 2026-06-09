//go:build darwin

package ossecurity

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

var execCommandSELinux = exec.Command

func selinuxExecuteOS(params map[string]any) (map[string]any, error) {
	gatekeeperOut, _ := execCommandSELinux("spctl", "--status").Output()
	santaModeOut, _ := execCommandSELinux("santactl", "status").Output()
	csrOut, _ := execCommandSELinux("csrutil", "status").Output()

	snapshot := map[string]any{
		"gatekeeper": strings.TrimSpace(string(gatekeeperOut)),
		"santa_mode": extractSantaMode(string(santaModeOut)),
		"sip_status": strings.TrimSpace(string(csrOut)),
	}

	mode, _ := params["mode"].(string)

	switch mode {
	case "enforcing":
		if out, err := execCommandSELinux("spctl", "--master-enable").CombinedOutput(); err != nil {
			return nil, fmt.Errorf("spctl --master-enable: %s: %w", out, err)
		}
		if _, err := exec.LookPath("santactl"); err == nil {
			execCommandSELinux("santactl", "rule", "--sync").Run()
		}
	case "permissive":
		execCommandSELinux("spctl", "--master-enable").Run()
		if _, err := exec.LookPath("santactl"); err == nil {
			execCommandSELinux("santactl", "rule", "--sync").Run()
		}
	case "disabled":
		return nil, fmt.Errorf("disabling SIP requires Recovery Mode; use spctl --master-disable for Gatekeeper only")
	default:
		if mode != "" {
			return nil, fmt.Errorf("unsupported mode %q: must be enforcing, permissive, or disabled", mode)
		}
	}

	if gen, _ := params["generate_from_audit_log"].(bool); gen {
		logOut, _ := execCommandSELinux("santactl", "log", "--last", "100").Output()
		return map[string]any{
			"audit_log_scan":  string(logOut),
			"config_snapshot": snapshot,
			"applied_at":      time.Now().UTC().Format(time.RFC3339),
		}, nil
	}

	if moduleSource, _ := params["module_source"].(string); moduleSource != "" {
		return map[string]any{
			"skipped":         true,
			"reason":          "module_source not applicable on macOS; use apparmor for Santa rules",
			"config_snapshot": snapshot,
		}, nil
	}

	return map[string]any{
		"previous_mode":     snapshot["santa_mode"],
		"new_mode":          mode,
		"modules_installed": []string{},
		"config_snapshot":   snapshot,
		"applied_at":        time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func selinuxRollbackOS(params map[string]any) (map[string]any, error) {
	configSnapshot, ok := params["config_snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("config_snapshot is required for rollback")
	}
	if gk, _ := configSnapshot["gatekeeper"].(string); gk != "" {
		if strings.Contains(gk, "disabled") || strings.Contains(gk, "assessments disabled") {
			execCommandSELinux("spctl", "--master-disable").Run()
		} else {
			execCommandSELinux("spctl", "--master-enable").Run()
		}
	}
	return map[string]any{"rolled_back": true}, nil
}

func extractSantaMode(output string) string {
	for _, line := range strings.Split(output, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Mode") || strings.Contains(line, "Mode:") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				return strings.TrimSpace(parts[1])
			}
		}
	}
	return "unknown"
}
