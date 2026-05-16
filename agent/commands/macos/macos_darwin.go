//go:build darwin

package macos

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func run(name string, args ...string) (string, error) {
	out, err := exec.Command(name, args...).CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func runTimeout(timeout time.Duration, name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	done := make(chan struct{})
	var out []byte
	var err error
	go func() {
		out, err = cmd.CombinedOutput()
		close(done)
	}()
	select {
	case <-done:
		return strings.TrimSpace(string(out)), err
	case <-time.After(timeout):
		cmd.Process.Kill()
		return "", fmt.Errorf("command timed out after %s", timeout)
	}
}

// filevaultStatus runs fdesetup status and returns enabled flag + raw status string.
func filevaultStatus(_ map[string]any) (map[string]any, error) {
	out, err := run("fdesetup", "status")
	if err != nil {
		return nil, fmt.Errorf("fdesetup status: %s: %w", out, err)
	}
	enabled := strings.Contains(strings.ToLower(out), "filevault is on")
	return map[string]any{"enabled": enabled, "status": out}, nil
}

// filevaultEnable enables FileVault and returns the generated recovery key.
func filevaultEnable(_ map[string]any) (map[string]any, error) {
	out, err := run("fdesetup", "enable", "-outputplist", "-")
	if err != nil {
		return nil, fmt.Errorf("fdesetup enable: %s: %w", out, err)
	}
	// Extract recovery key from plist output (key is in <string>XXXX-XXXX-...</string> after RecoveryKey)
	key := ""
	lines := strings.Split(out, "\n")
	for i, line := range lines {
		if strings.Contains(line, "RecoveryKey") && i+1 < len(lines) {
			val := lines[i+1]
			val = strings.TrimSpace(val)
			val = strings.TrimPrefix(val, "<string>")
			val = strings.TrimSuffix(val, "</string>")
			key = val
			break
		}
	}
	return map[string]any{"recovery_key": key, "plist": out}, nil
}

// gatekeeperStatus returns whether Gatekeeper is enabled.
func gatekeeperStatus(_ map[string]any) (map[string]any, error) {
	out, err := run("spctl", "--status")
	if err != nil {
		// spctl --status exits non-zero on some macOS versions even when working
		if !strings.Contains(out, "assessments") {
			return nil, fmt.Errorf("spctl --status: %s: %w", out, err)
		}
	}
	enabled := strings.Contains(strings.ToLower(out), "assessments enabled")
	return map[string]any{"enabled": enabled, "status": out}, nil
}

// gatekeeperEnable enables Gatekeeper via spctl --master-enable.
func gatekeeperEnable(_ map[string]any) (map[string]any, error) {
	out, err := run("spctl", "--master-enable")
	if err != nil {
		return nil, fmt.Errorf("spctl --master-enable: %s: %w", out, err)
	}
	return map[string]any{"enabled": true, "output": out}, nil
}

// gatekeeperDisable disables Gatekeeper via spctl --master-disable.
func gatekeeperDisable(_ map[string]any) (map[string]any, error) {
	out, err := run("spctl", "--master-disable")
	if err != nil {
		return nil, fmt.Errorf("spctl --master-disable: %s: %w", out, err)
	}
	return map[string]any{"enabled": false, "output": out}, nil
}

// softwareupdateList lists available macOS software updates.
func softwareupdateList(_ map[string]any) (map[string]any, error) {
	out, err := run("softwareupdate", "--list", "--all")
	if err != nil && !strings.Contains(out, "Software Update found") && !strings.Contains(out, "No new software available") {
		return nil, fmt.Errorf("softwareupdate --list: %s: %w", out, err)
	}
	updates := parseUpdates(out)
	return map[string]any{"updates": updates, "raw": out}, nil
}

// parseUpdates parses softwareupdate --list output into a slice of label/size maps.
func parseUpdates(out string) []map[string]any {
	var updates []map[string]any
	lines := strings.Split(out, "\n")
	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "* Label:") {
			label := strings.TrimPrefix(trimmed, "* Label:")
			label = strings.TrimSpace(label)
			size := ""
			// Look ahead for Size line
			for j := i + 1; j < len(lines) && j < i+5; j++ {
				next := strings.TrimSpace(lines[j])
				if strings.HasPrefix(next, "Size:") {
					size = strings.TrimPrefix(next, "Size:")
					size = strings.TrimSpace(size)
					break
				}
			}
			updates = append(updates, map[string]any{"label": label, "size": size})
		}
	}
	return updates
}

// softwareupdateInstall installs all or a specific labeled update.
func softwareupdateInstall(params map[string]any) (map[string]any, error) {
	const timeout = 30 * time.Minute
	label, _ := params["label"].(string)
	var out string
	var err error
	if label != "" {
		out, err = runTimeout(timeout, "softwareupdate", "--install", label)
	} else {
		out, err = runTimeout(timeout, "softwareupdate", "--install", "--all")
	}
	if err != nil {
		return nil, fmt.Errorf("softwareupdate --install: %s: %w", out, err)
	}
	return map[string]any{"output": out}, nil
}

// profilesList lists installed configuration profiles by running profiles list -output stdout-xml.
func profilesList(_ map[string]any) (map[string]any, error) {
	out, err := run("profiles", "list", "-output", "stdout-xml")
	if err != nil {
		return nil, fmt.Errorf("profiles list: %s: %w", out, err)
	}
	// Return the raw plist; structured parsing would require an xml library
	// which adds no value for the dispatch/audit use case.
	return map[string]any{"profiles_plist": out}, nil
}

// launchctlList lists running launchd services.
func launchctlList(_ map[string]any) (map[string]any, error) {
	out, err := run("launchctl", "list")
	if err != nil {
		return nil, fmt.Errorf("launchctl list: %s: %w", out, err)
	}
	services := parseLaunchctl(out)
	return map[string]any{"services": services, "raw": out}, nil
}

// parseLaunchctl parses tab-separated launchctl list output.
func parseLaunchctl(out string) []map[string]any {
	var services []map[string]any
	lines := strings.Split(out, "\n")
	for i, line := range lines {
		if i == 0 { // header
			continue
		}
		parts := strings.Fields(line)
		if len(parts) < 3 {
			continue
		}
		pid := parts[0]
		status := parts[1]
		label := parts[2]
		services = append(services, map[string]any{"pid": pid, "status": status, "label": label})
	}
	return services
}

// macosSysinfo returns macOS version and hardware info.
func macosSysinfo(_ map[string]any) (map[string]any, error) {
	swVers, err := run("sw_vers")
	if err != nil {
		return nil, fmt.Errorf("sw_vers: %s: %w", swVers, err)
	}
	profiler, err := run("system_profiler", "SPHardwareDataType")
	if err != nil {
		return nil, fmt.Errorf("system_profiler: %s: %w", profiler, err)
	}

	result := map[string]any{
		"os_version": "",
		"build":      "",
		"model":      "",
		"serial":     "",
	}

	for _, line := range strings.Split(swVers, "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(parts[1])
		switch key {
		case "ProductVersion":
			result["os_version"] = val
		case "BuildVersion":
			result["build"] = val
		}
	}

	for _, line := range strings.Split(profiler, "\n") {
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(parts[1])
		switch key {
		case "Model Name", "Model Identifier":
			if result["model"] == "" {
				result["model"] = val
			}
		case "Serial Number (system)":
			result["serial"] = val
		}
	}

	return result, nil
}
