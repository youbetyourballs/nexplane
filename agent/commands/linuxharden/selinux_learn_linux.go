//go:build linux

package linuxharden

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func selinuxLearnExecute(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}
	serviceName, _ := params["service_name"].(string)
	if serviceName == "" {
		serviceName = "nginx"
	}

	if _, err := exec.LookPath("sestatus"); err != nil {
		return nil, fmt.Errorf("SELinux not available: sestatus not found in PATH")
	}
	if _, err := exec.LookPath("semanage"); err != nil {
		return nil, fmt.Errorf("semanage not found — install policycoreutils-python-utils")
	}

	// Resolve SELinux type from service name
	selinuxType, err := resolveSelinuxType(serviceName)
	if err != nil {
		return nil, fmt.Errorf("resolving SELinux type for %q: %w", serviceName, err)
	}

	// Set per-type permissive (only this domain, rest of system stays enforcing)
	if out, err := exec.Command("semanage", "permissive", "-a", selinuxType).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("semanage permissive -a %s: %s: %w", selinuxType, out, err)
	}
	defer func() {
		exec.Command("semanage", "permissive", "-d", selinuxType).Run() //nolint:errcheck
	}()

	// Record audit log offset before observation window
	logPath := "/var/log/audit/audit.log"
	startOffset := int64(0)
	if info, err := os.Stat(logPath); err == nil {
		startOffset = info.Size()
	}

	time.Sleep(time.Duration(duration) * time.Second)

	// Collect new AVC denial lines since offset
	avcLines := collectAVCDenials(logPath, startOffset, selinuxType)

	return map[string]any{
		"action":           "selinux_learn",
		"service_name":     serviceName,
		"selinux_type":     selinuxType,
		"duration_seconds": int(duration),
		"avc_lines":        avcLines,
		"avc_count":        len(avcLines),
	}, nil
}

func resolveSelinuxType(serviceName string) (string, error) {
	out, err := exec.Command("systemctl", "show", serviceName, "-P", "SELinuxContext").Output()
	if err == nil {
		context := strings.TrimSpace(string(out))
		parts := strings.Split(context, ":")
		if len(parts) >= 3 && parts[2] != "" {
			return parts[2], nil
		}
	}
	// Fallback: scan ps -eZ for the service name
	psOut, err := exec.Command("ps", "-eZ").Output()
	if err != nil {
		return "", fmt.Errorf("ps -eZ: %w", err)
	}
	for _, line := range strings.Split(string(psOut), "\n") {
		if strings.Contains(line, serviceName) {
			fields := strings.Fields(line)
			if len(fields) > 0 {
				parts := strings.Split(fields[0], ":")
				if len(parts) >= 3 && parts[2] != "" {
					return parts[2], nil
				}
			}
		}
	}
	return "", fmt.Errorf("could not determine SELinux type for %q from systemctl or ps", serviceName)
}

func collectAVCDenials(logPath string, startOffset int64, selinuxType string) []string {
	f, err := os.Open(logPath)
	if err != nil {
		return nil
	}
	defer f.Close()
	f.Seek(startOffset, 0) //nolint:errcheck

	seen := map[string]bool{}
	var lines []string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.Contains(line, "avc:") || !strings.Contains(line, "denied") {
			continue
		}
		if !strings.Contains(line, selinuxType) {
			continue
		}
		if !seen[line] {
			seen[line] = true
			lines = append(lines, line)
		}
	}
	return lines
}

func selinuxLearnRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "selinux_learn_rollback", "status": "no_state_to_revert"}, nil
}
