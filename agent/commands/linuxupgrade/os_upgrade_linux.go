//go:build linux

package linuxupgrade

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func osUpgradePreflightOS(params map[string]any) (map[string]any, error) {
	targetVersion, _ := params["target_version"].(string)

	// Detect current OS
	currentOS := detectCurrentOS()

	// Detect package manager and upgrade tool availability
	pkgMgr := detectPackageManager()
	upgradeTool := ""
	warnings := []string{}

	switch pkgMgr {
	case "apt":
		if _, err := exec.LookPath("do-release-upgrade"); err == nil {
			upgradeTool = "do-release-upgrade"
		} else {
			warnings = append(warnings, "do-release-upgrade not found; install ubuntu-release-upgrader-core")
		}
	case "dnf":
		if _, err := exec.LookPath("leapp"); err == nil {
			upgradeTool = "leapp"
		} else {
			warnings = append(warnings, "leapp not found; install leapp for RHEL/CentOS major upgrades")
		}
	default:
		warnings = append(warnings, fmt.Sprintf("unsupported package manager %q; manual upgrade required", pkgMgr))
	}

	// Check available disk space on /
	diskFreeGB := getDiskFreeGB("/")
	if diskFreeGB < 5 {
		return map[string]any{
			"status":     "blocked",
			"reason":     fmt.Sprintf("insufficient disk space: %.1f GB free, need at least 5 GB", diskFreeGB),
			"current_os": currentOS,
		}, nil
	}
	if diskFreeGB < 10 {
		warnings = append(warnings, fmt.Sprintf("low disk space: %.1f GB free — 10+ GB recommended for safe upgrade", diskFreeGB))
	}

	// List running systemd services
	runningServices := listRunningServices()

	// Estimate upgrade duration
	estimatedMinutes := 60
	if pkgMgr == "dnf" {
		estimatedMinutes = 90 // RHEL leapp upgrades tend to be longer
	}

	return map[string]any{
		"status":                     "ok",
		"current_os":                 currentOS,
		"target_os":                  targetVersion,
		"package_manager":            pkgMgr,
		"upgrade_tool":               upgradeTool,
		"disk_free_gb":               diskFreeGB,
		"running_services":           runningServices,
		"estimated_duration_minutes": estimatedMinutes,
		"warnings":                   warnings,
		"checked_at":                 time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func osUpgradeVerifyOS(params map[string]any) (map[string]any, error) {
	targetVersion, _ := params["target_version"].(string)
	preUpgradeServicesRaw, _ := params["pre_upgrade_services"].([]any)

	// Get current OS version
	newOSVersion := detectCurrentOS()

	// Check kernel
	kernelOut, _ := exec.Command("uname", "-r").Output()
	kernelVersion := strings.TrimSpace(string(kernelOut))

	// Verify OS version matches target (if specified)
	versionOK := true
	if targetVersion != "" && !strings.Contains(newOSVersion, targetVersion) {
		versionOK = false
	}

	// Check which pre-upgrade services are still running
	currentRunning := listRunningServicesSet()
	servicesStopped := []string{}
	servicesOK := []string{}

	for _, svc := range preUpgradeServicesRaw {
		name, ok := svc.(string)
		if !ok {
			continue
		}
		if currentRunning[name] {
			servicesOK = append(servicesOK, name)
		} else {
			servicesStopped = append(servicesStopped, name)
		}
	}

	verified := versionOK && len(servicesStopped) == 0

	return map[string]any{
		"verified":         verified,
		"new_os_version":   newOSVersion,
		"kernel_version":   kernelVersion,
		"version_ok":       versionOK,
		"services_ok":      servicesOK,
		"services_stopped": servicesStopped,
		"verified_at":      time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func detectCurrentOS() string {
	// Try lsb_release first (Ubuntu/Debian)
	if out, err := exec.Command("lsb_release", "-ds").Output(); err == nil {
		return strings.TrimSpace(string(out))
	}
	// Fall back to /etc/os-release
	if out, err := exec.Command("sh", "-c", `. /etc/os-release && echo "$PRETTY_NAME"`).Output(); err == nil {
		v := strings.TrimSpace(string(out))
		if v != "" {
			return v
		}
	}
	// Try /etc/redhat-release
	if out, err := exec.Command("cat", "/etc/redhat-release").Output(); err == nil {
		return strings.TrimSpace(string(out))
	}
	return "unknown"
}

func getDiskFreeGB(path string) float64 {
	out, err := exec.Command("df", "-BG", "--output=avail", path).Output()
	if err != nil {
		return 0
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	if len(lines) < 2 {
		return 0
	}
	val := strings.TrimSuffix(strings.TrimSpace(lines[1]), "G")
	var gb float64
	fmt.Sscanf(val, "%f", &gb)
	return gb
}

func listRunningServices() []string {
	out, err := exec.Command("systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--no-pager").Output()
	if err != nil {
		return nil
	}
	var services []string
	for _, line := range strings.Split(string(out), "\n") {
		fields := strings.Fields(line)
		if len(fields) > 0 {
			services = append(services, fields[0])
		}
	}
	return services
}

func listRunningServicesSet() map[string]bool {
	services := listRunningServices()
	result := make(map[string]bool, len(services))
	for _, s := range services {
		result[s] = true
	}
	return result
}
