//go:build windows

package reboot

import (
	"context"
	"fmt"
	"os/exec"
	"time"
)

func executeOS(params map[string]any) (map[string]any, error) {
	delay := 60
	if d, ok := params["graceful_delay_seconds"].(float64); ok && int(d) > 60 {
		delay = int(d)
	}
	dryRun, _ := params["dry_run"].(bool)

	if !dryRun {
		cmd := exec.CommandContext(context.Background(),
			"powershell", "-Command",
			fmt.Sprintf("Restart-Computer -Delay %d -Force", delay))
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("Restart-Computer failed: %w (output: %s)", err, out)
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
		out, err := exec.CommandContext(context.Background(), "sc", "query", svc).CombinedOutput()
		if err != nil {
			results[svc] = "failed: " + string(out)
		} else {
			results[svc] = "running"
		}
	}

	return map[string]any{
		"action":      "verify_post_reboot",
		"services":    results,
		"verified_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
