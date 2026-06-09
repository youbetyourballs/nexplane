//go:build darwin

package reboot

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

var execCommandDarwin = exec.Command

func executeOS(params map[string]any) (map[string]any, error) {
	delay := 60
	if d, ok := params["graceful_delay_seconds"].(float64); ok && int(d) > 0 {
		delay = int(d)
	}
	dryRun, _ := params["dry_run"].(bool)

	if !dryRun {
		delayMin := fmt.Sprintf("+%d", (delay+59)/60)
		cmd := execCommandDarwin("shutdown", "-r", delayMin, "Nexplane scheduled reboot")
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("shutdown failed: %w (output: %s)", err, out)
		}
	}

	return map[string]any{
		"action":                 "graceful_reboot",
		"graceful_delay_seconds": delay,
		"scheduled_at":           time.Now().UTC().Format(time.RFC3339),
		"dry_run":                dryRun,
	}, nil
}

func verifyPostRebootOS(params map[string]any) (map[string]any, error) {
	rawSvcs, _ := params["verify_services"].([]any)
	var services []string
	for _, s := range rawSvcs {
		if sv, ok := s.(string); ok && sv != "" {
			services = append(services, sv)
		}
	}

	results := map[string]string{}
	for _, svc := range services {
		out, err := execCommandDarwin("launchctl", "list", svc).Output()
		if err != nil {
			results[svc] = "not-running"
		} else {
			output := strings.TrimSpace(string(out))
			if strings.Contains(output, svc) {
				results[svc] = "running"
			} else {
				results[svc] = "unknown"
			}
		}
	}

	return map[string]any{
		"service_results": results,
		"verified_at":     time.Now().UTC().Format(time.RFC3339),
	}, nil
}
