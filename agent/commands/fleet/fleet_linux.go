//go:build linux

package fleet

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"syscall"
	"time"
)

func restartServiceOS(ctx context.Context, serviceName string) map[string]any {
	out, err := exec.CommandContext(ctx, "systemctl", "restart", serviceName).CombinedOutput()
	if err != nil {
		return map[string]any{"running": false, "output": string(out), "error": err.Error()}
	}
	checkErr := exec.CommandContext(ctx, "systemctl", "is-active", "--quiet", serviceName).Run()
	return map[string]any{"running": checkErr == nil, "output": string(out)}
}

func pushConfigFileOS(params map[string]any) map[string]any {
	filePath, _ := params["file_path"].(string)
	fileContent, _ := params["file_content"].(string)
	backup, _ := params["backup"].(bool)

	if filePath == "" {
		return map[string]any{"error": "file_path is required"}
	}

	var backupPath string
	if backup {
		existing, err := os.ReadFile(filePath)
		if err == nil {
			backupPath = fmt.Sprintf("%s.bak.%d", filePath, time.Now().Unix())
			if writeErr := os.WriteFile(backupPath, existing, 0600); writeErr != nil {
				return map[string]any{"error": fmt.Sprintf("backup failed: %v", writeErr)}
			}
		}
	}

	if err := writeFileAtomically(filePath, fileContent, 0644); err != nil {
		result := map[string]any{"error": err.Error()}
		if backupPath != "" {
			result["backup_path"] = backupPath
		}
		return result
	}
	result := map[string]any{}
	if backupPath != "" {
		result["backup_path"] = backupPath
	}
	return result
}

func healthCheckOS(ctx context.Context, requiredServices []string) map[string]any {
	diskOK := checkDiskFreeLinux()
	loadOK := checkLoadLinux()
	services := make(map[string]bool, len(requiredServices))
	for _, svc := range requiredServices {
		err := exec.CommandContext(ctx, "systemctl", "is-active", "--quiet", svc).Run()
		services[svc] = err == nil
	}

	pass := diskOK && loadOK
	for _, ok := range services {
		if !ok {
			pass = false
			break
		}
	}
	return map[string]any{
		"disk_free_ok":      diskOK,
		"load_ok":           loadOK,
		"reachable":         true,
		"no_pending_reboot": true, // always true on Linux
		"services":          services,
		"pass":              pass,
	}
}

func checkDiskFreeLinux() bool {
	var stat syscall.Statfs_t
	if err := syscall.Statfs("/", &stat); err != nil {
		return false
	}
	total := stat.Blocks * uint64(stat.Bsize)
	free := stat.Bavail * uint64(stat.Bsize)
	if total == 0 {
		return false
	}
	return float64(free)/float64(total) > 0.20
}

func checkLoadLinux() bool {
	data, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return false
	}
	var load1 float64
	fmt.Sscanf(string(data), "%f", &load1)
	// Reuse logical CPU count for comparison; default to 1 if unavailable
	data2, err := os.ReadFile("/proc/cpuinfo")
	cpuCount := 1
	if err == nil {
		for _, b := range data2 {
			if b == '\n' {
				cpuCount++
			}
		}
		cpuCount = cpuCount / 28 // rough: ~28 lines per CPU block
		if cpuCount < 1 {
			cpuCount = 1
		}
	}
	return load1 < float64(cpuCount)*0.80
}

func runPostCommand(ctx context.Context, cmd string) map[string]any {
	out, err := exec.CommandContext(ctx, "sh", "-c", cmd).CombinedOutput()
	if err != nil {
		return map[string]any{"error": fmt.Sprintf("post_command: %v", err), "output": string(out)}
	}
	return map[string]any{"output": string(out)}
}
