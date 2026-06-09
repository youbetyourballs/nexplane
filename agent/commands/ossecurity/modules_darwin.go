//go:build darwin

package ossecurity

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

var execCommandModules = exec.Command

func blacklistExecuteOS(params map[string]any) (map[string]any, error) {
	modulesRaw, _ := params["modules"].([]any)
	modules := make([]string, 0, len(modulesRaw))
	for _, m := range modulesRaw {
		if s, ok := m.(string); ok {
			modules = append(modules, s)
		}
	}

	snapOut, _ := execCommandModules("systemextensionsctl", "list").Output()
	snapshot := strings.TrimSpace(string(snapOut))

	execCommandModules("systemextensionsctl", "developer", "on").Run()

	unloaded := []string{}
	for _, bundleID := range modules {
		out, err := execCommandModules("systemextensionsctl", "uninstall", bundleID).CombinedOutput()
		if err != nil {
			unloaded = append(unloaded, bundleID+":skipped("+strings.TrimSpace(string(out))+")")
			continue
		}
		unloaded = append(unloaded, bundleID)
	}

	return map[string]any{
		"modules_blacklisted": unloaded,
		"snapshot":            snapshot,
		"applied_at":          time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func blacklistRollbackOS(params map[string]any) (map[string]any, error) {
	if out, err := execCommandModules("systemextensionsctl", "reset").CombinedOutput(); err != nil {
		return nil, fmt.Errorf("systemextensionsctl reset: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true}, nil
}
