//go:build darwin

package fleet

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

var execCommandFleetDarwin = exec.Command

func restartServiceOS(ctx context.Context, serviceName string) map[string]any {
	labels := []string{
		"system/homebrew.mxcl." + serviceName,
		"system/com." + serviceName + "." + serviceName,
		"system/com.apple." + serviceName,
	}
	for _, label := range labels {
		out, err := execCommandFleetDarwin("launchctl", "kickstart", "-k", label).CombinedOutput()
		if err == nil {
			return map[string]any{"running": true, "output": string(out), "label": label}
		}
	}
	out2, err2 := execCommandFleetDarwin("brew", "services", "restart", serviceName).CombinedOutput()
	if err2 != nil {
		return map[string]any{
			"running": false,
			"error":   fmt.Sprintf("launchctl and brew restart failed: %s", out2),
		}
	}
	return map[string]any{"running": true, "output": string(out2)}
}

func pushConfigFileOS(params map[string]any) map[string]any {
	filePath, _ := params["file_path"].(string)
	fileContent, _ := params["file_content"].(string)
	backup, _ := params["backup"].(bool)

	if filePath == "" {
		return map[string]any{"error": "file_path is required"}
	}
	if backup {
		if existing, err := os.ReadFile(filePath); err == nil {
			backupPath := fmt.Sprintf("%s.bak.%d", filePath, time.Now().Unix())
			if err := os.WriteFile(backupPath, existing, 0600); err != nil {
				return map[string]any{"error": fmt.Sprintf("backup failed: %v", err)}
			}
		}
	}
	if err := os.WriteFile(filePath, []byte(fileContent), 0644); err != nil {
		return map[string]any{"error": err.Error()}
	}
	return map[string]any{"pushed": true, "file_path": filePath}
}

func healthCheckOS(_ context.Context, endpoints []string) map[string]any {
	results := map[string]string{}
	for _, ep := range endpoints {
		_, err := execCommandFleetDarwin("curl", "-sf", "--max-time", "5", ep).Output()
		if err != nil {
			results[ep] = "unhealthy"
		} else {
			results[ep] = "healthy"
		}
	}
	return map[string]any{"results": results, "healthy": true}
}

func runPostCommand(_ context.Context, command string) map[string]any {
	cmd := execCommandFleetDarwin("/bin/sh", "-c", command)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return map[string]any{"ran": false, "error": err.Error(), "output": string(out)}
	}
	return map[string]any{"ran": true, "output": string(out)}
}
