//go:build windows

package fleet

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func restartServiceOS(ctx context.Context, serviceName string) map[string]any {
	restartCmd := fmt.Sprintf("Restart-Service -Name '%s' -Force", serviceName)
	out, err := exec.CommandContext(ctx, "powershell", "-Command", restartCmd).CombinedOutput()
	if err != nil {
		return map[string]any{"running": false, "output": string(out), "error": err.Error()}
	}
	checkCmd := fmt.Sprintf("(Get-Service -Name '%s').Status", serviceName)
	checkOut, checkErr := exec.CommandContext(ctx, "powershell", "-Command", checkCmd).CombinedOutput()
	running := checkErr == nil && strings.TrimSpace(string(checkOut)) == "Running"
	return map[string]any{"running": running, "output": string(checkOut)}
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
	diskOK := checkDiskFreeWindows()
	loadOK := checkLoadWindows(ctx)
	noPendingReboot := checkNoPendingRebootWindows()
	services := make(map[string]bool, len(requiredServices))
	for _, svc := range requiredServices {
		_, err := exec.CommandContext(ctx, "sc", "query", svc).Output()
		services[svc] = err == nil
	}

	pass := diskOK && loadOK && noPendingReboot
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
		"no_pending_reboot": noPendingReboot,
		"services":          services,
		"pass":              pass,
	}
}

func checkDiskFreeWindows() bool {
	out, err := exec.Command("powershell", "-Command",
		"(Get-PSDrive C | Select-Object -ExpandProperty Free) / (Get-PSDrive C | Select-Object -ExpandProperty Used + (Get-PSDrive C | Select-Object -ExpandProperty Free))").Output()
	if err != nil {
		return true // assume ok if we can't check
	}
	var ratio float64
	fmt.Sscanf(strings.TrimSpace(string(out)), "%f", &ratio)
	return ratio > 0.20
}

func checkLoadWindows(ctx context.Context) bool {
	out, err := exec.CommandContext(ctx, "powershell", "-Command",
		"(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average").Output()
	if err != nil {
		return true
	}
	var load float64
	fmt.Sscanf(strings.TrimSpace(string(out)), "%f", &load)
	return load < 80.0
}

func checkNoPendingRebootWindows() bool {
	out, err := exec.Command("reg", "query",
		`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager`,
		"/v", "PendingFileRenameOperations").Output()
	return err != nil || len(strings.TrimSpace(string(out))) == 0
}

func runPostCommand(ctx context.Context, cmd string) map[string]any {
	out, err := exec.CommandContext(ctx, "powershell", "-Command", cmd).CombinedOutput()
	if err != nil {
		return map[string]any{"error": fmt.Sprintf("post_command: %v", err), "output": string(out)}
	}
	return map[string]any{"output": string(out)}
}
