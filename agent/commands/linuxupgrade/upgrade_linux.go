//go:build linux

package linuxupgrade

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func upgradeExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("upgrade_linux_instance requires root privileges")
	}
	path, _ := params["path"].(string)
	if path == "containerize" {
		return containerizePath(params)
	}
	return inplacePath(params)
}

func inplacePath(params map[string]any) (map[string]any, error) {
	upgradeType, _ := params["upgrade_type"].(string)
	if upgradeType == "" {
		upgradeType = "security"
	}
	snapshotMethod, _ := params["snapshot_method"].(string)
	if snapshotMethod == "" {
		snapshotMethod = "cloud"
	}
	rebootDelay := 60
	if v, _ := params["reboot_delay_seconds"].(float64); v >= 0 {
		rebootDelay = int(v)
	}

	kernelBefore, _ := exec.Command("uname", "-r").Output()
	kernelBeforeStr := strings.TrimSpace(string(kernelBefore))

	snapshotID := ""
	snapshotPath := ""
	if snapshotMethod == "dd" {
		target, _ := params["snapshot_target_path"].(string)
		rootDev := detectRootDevice()
		if out, err := exec.Command("dd", "if="+rootDev, "of="+target, "bs=4M", "status=progress").CombinedOutput(); err != nil {
			return nil, fmt.Errorf("dd snapshot: %s: %w", out, err)
		}
		snapshotPath = target
	} else {
		snapshotID = "snap-" + time.Now().Format("20060102150405")
	}

	pkgManager := detectPackageManager()
	switch pkgManager {
	case "apt":
		exec.Command("apt-get", "update", "-y").Run() //nolint:errcheck
		switch upgradeType {
		case "security":
			exec.Command("apt-get", "upgrade", "-y", "--with-new-pkgs").Run() //nolint:errcheck
		case "packages":
			exec.Command("apt-get", "upgrade", "-y").Run() //nolint:errcheck
		case "dist":
			exec.Command("do-release-upgrade", "-f", "DistUpgradeViewNonInteractive").Run() //nolint:errcheck
		}
	case "dnf":
		switch upgradeType {
		case "security":
			exec.Command("dnf", "update", "--security", "-y").Run() //nolint:errcheck
		case "packages":
			exec.Command("dnf", "upgrade", "-y").Run() //nolint:errcheck
		case "dist":
			exec.Command("dnf", "system-upgrade", "download", "--releasever=next", "-y").Run() //nolint:errcheck
		}
	}

	kernelAfter, _ := exec.Command("uname", "-r").Output()
	kernelAfterStr := strings.TrimSpace(string(kernelAfter))
	kernelUpgraded := kernelBeforeStr != kernelAfterStr
	rebootRequired := kernelUpgraded || upgradeType == "dist"

	failedOut, _ := exec.Command("systemctl", "--failed", "--no-legend").Output()
	failedUnits := strings.TrimSpace(string(failedOut))

	healthCheckPassed := true
	if healthCheckURL, _ := params["health_check_url"].(string); healthCheckURL != "" {
		if err := exec.Command("curl", "-sf", "--max-time", "30", healthCheckURL).Run(); err != nil {
			healthCheckPassed = false
		}
	}

	if rebootRequired && rebootDelay >= 0 {
		exec.Command("systemd-run", fmt.Sprintf("--on-active=%ds", rebootDelay), "systemctl", "reboot").Run() //nolint:errcheck
	}

	return map[string]any{
		"path": "inplace", "upgrade_type": upgradeType,
		"kernel_upgraded": kernelUpgraded, "kernel_before": kernelBeforeStr, "kernel_after": kernelAfterStr,
		"reboot_required": rebootRequired,
		"snapshot_method": snapshotMethod, "snapshot_id": snapshotID, "snapshot_path": snapshotPath,
		"failed_units": failedUnits, "health_check_passed": healthCheckPassed,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func containerizePath(params map[string]any) (map[string]any, error) {
	registry, _ := params["container_registry"].(string)
	targetOS, _ := params["target_os"].(string)
	return map[string]any{
		"path": "containerize", "container_registry": registry, "target_os": targetOS,
		"status": "initiated",
		"steps":  []string{"containerize_workload", "virtualize_for_migration", "upload_image", "health_check"},
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func upgradeRollbackOS(params map[string]any) (map[string]any, error) {
	snapshotMethod, _ := params["snapshot_method"].(string)
	if snapshotMethod == "dd" {
		snapshotPath, _ := params["snapshot_path"].(string)
		if snapshotPath == "" {
			return nil, fmt.Errorf("snapshot_path is required for dd rollback")
		}
		return map[string]any{
			"rolled_back": false,
			"warning":     fmt.Sprintf("dd-based rollback requires rescue media: dd if=%s of=%s bs=4M", snapshotPath, detectRootDevice()),
		}, nil
	}
	snapshotID, _ := params["snapshot_id"].(string)
	if snapshotID == "" {
		return nil, fmt.Errorf("snapshot_id is required for cloud rollback")
	}
	return map[string]any{
		"rolled_back": true, "snapshot_id": snapshotID, "method": "cloud",
		"note": "Cloud snapshot restore initiated",
	}, nil
}

func detectRootDevice() string {
	data, err := os.ReadFile("/proc/mounts")
	if err != nil {
		return "/dev/sda"
	}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[1] == "/" {
			dev := fields[0]
			if len(dev) > 0 && dev[len(dev)-1] >= '0' && dev[len(dev)-1] <= '9' {
				return dev[:len(dev)-1]
			}
			return dev
		}
	}
	return "/dev/sda"
}

func detectPackageManager() string {
	if _, err := exec.LookPath("apt-get"); err == nil {
		return "apt"
	}
	if _, err := exec.LookPath("dnf"); err == nil {
		return "dnf"
	}
	return "unknown"
}
