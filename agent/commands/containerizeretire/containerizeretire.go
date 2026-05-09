package containerizeretire

import (
	"fmt"
	"os/exec"
	"strings"
)

// ContainerizeRetireExecute stops and disables the legacy systemd service.
// Parameters:
//   - systemd_unit (string, required): e.g. "myapp.service"
//   - dry_run (bool, optional): if true, skip actual systemctl calls
func ContainerizeRetireExecute(params map[string]any) (map[string]any, error) {
	unit, ok := params["systemd_unit"].(string)
	if !ok || unit == "" {
		return nil, fmt.Errorf("missing required parameter: systemd_unit")
	}
	unit = strings.TrimSuffix(unit, ".service") + ".service"

	dryRun, _ := params["dry_run"].(bool)

	result := map[string]any{
		"action":           "containerize_retire",
		"systemd_unit":     unit,
		"dry_run":          dryRun,
		"service_stopped":  false,
		"service_disabled": false,
	}

	if dryRun {
		result["note"] = "dry_run: no changes made"
		return result, nil
	}

	stopOut, err := exec.Command("systemctl", "stop", unit).CombinedOutput()
	if err != nil {
		return result, fmt.Errorf("systemctl stop %s failed: %w — output: %s", unit, err, stopOut)
	}
	result["service_stopped"] = true

	disableOut, err := exec.Command("systemctl", "disable", unit).CombinedOutput()
	if err != nil {
		result["disable_warning"] = fmt.Sprintf("systemctl disable failed: %s", disableOut)
	} else {
		result["service_disabled"] = true
	}

	return result, nil
}

// ContainerizeRetireRollback restarts the legacy service.
func ContainerizeRetireRollback(params map[string]any) (map[string]any, error) {
	unit, ok := params["systemd_unit"].(string)
	if !ok || unit == "" {
		return map[string]any{"rolled_back": false, "note": "no systemd_unit in rollback params"}, nil
	}
	unit = strings.TrimSuffix(unit, ".service") + ".service"

	out, err := exec.Command("systemctl", "start", unit).CombinedOutput()
	if err != nil {
		return map[string]any{
			"rolled_back": false,
			"error":       fmt.Sprintf("systemctl start %s failed: %s", unit, out),
		}, nil
	}
	return map[string]any{
		"rolled_back":       true,
		"systemd_unit":      unit,
		"service_restarted": true,
	}, nil
}
