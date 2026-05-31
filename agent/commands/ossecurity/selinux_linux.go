//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func selinuxExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_selinux requires root privileges")
	}
	if _, err := exec.LookPath("sestatus"); err != nil {
		return nil, fmt.Errorf("SELinux not available: sestatus not found")
	}

	currentModeOut, _ := exec.Command("getenforce").Output()
	currentMode := strings.TrimSpace(string(currentModeOut))
	configData, _ := os.ReadFile("/etc/selinux/config")
	snapshot := map[string]any{
		"previous_mode":     currentMode,
		"selinux_config":    string(configData),
		"modules_installed": []string{},
	}

	newMode := currentMode
	modulesInstalled := []string{}

	if mode, ok := params["mode"].(string); ok && mode != "" {
		modeInt := map[string]string{"enforcing": "1", "permissive": "0", "disabled": "0"}
		if code, ok := modeInt[mode]; ok {
			if out, err := exec.Command("setenforce", code).CombinedOutput(); err != nil {
				return nil, fmt.Errorf("setenforce: %s: %w", out, err)
			}
		}
		config := string(configData)
		var lines []string
		for _, l := range strings.Split(config, "\n") {
			if strings.HasPrefix(l, "SELINUX=") {
				lines = append(lines, "SELINUX="+mode)
			} else {
				lines = append(lines, l)
			}
		}
		if err := os.WriteFile("/etc/selinux/config", []byte(strings.Join(lines, "\n")), 0644); err != nil {
			return nil, fmt.Errorf("updating selinux config: %w", err)
		}
		newMode = mode
	}

	if modulePath, ok := params["policy_module_path"].(string); ok && modulePath != "" {
		out, err := exec.Command("semodule", "-i", modulePath).CombinedOutput()
		if err != nil {
			return nil, fmt.Errorf("semodule -i: %s: %w", out, err)
		}
		parts := strings.Split(modulePath, "/")
		name := strings.TrimSuffix(parts[len(parts)-1], ".pp")
		name = strings.TrimSuffix(name, ".te")
		modulesInstalled = append(modulesInstalled, name)
		snapshot["modules_installed"] = modulesInstalled
	}

	if gen, _ := params["generate_from_audit_log"].(bool); gen {
		if _, err := exec.LookPath("ausearch"); err != nil {
			return nil, fmt.Errorf("ausearch not found (required for generate_from_audit_log)")
		}
		ausearchOut, err := exec.Command("ausearch", "-m", "avc", "-ts", "recent").Output()
		if err == nil && len(ausearchOut) > 0 {
			cmd := exec.Command("audit2allow", "-M", "nexplane_generated")
			cmd.Stdin = strings.NewReader(string(ausearchOut))
			if out, err := cmd.CombinedOutput(); err != nil {
				return nil, fmt.Errorf("audit2allow: %s: %w", out, err)
			}
			if out, err := exec.Command("semodule", "-i", "nexplane_generated.pp").CombinedOutput(); err != nil {
				return nil, fmt.Errorf("semodule -i nexplane_generated.pp: %s: %w", out, err)
			}
			modulesInstalled = append(modulesInstalled, "nexplane_generated")
			snapshot["modules_installed"] = modulesInstalled
		}
	}

	if moduleSource, ok := params["module_source"].(string); ok && moduleSource != "" {
		svcName, _ := params["service_name"].(string)
		if svcName == "" {
			svcName = "nexplane"
		}
		moduleName, _ := params["module_name"].(string)
		if moduleName == "" {
			moduleName = "nexplane-" + svcName
		}
		// Substitute {service_name} placeholder if still present
		moduleName = strings.ReplaceAll(moduleName, "{service_name}", svcName)
		moduleSource = strings.ReplaceAll(moduleSource, "{service_name}", svcName)

		tePath := "/tmp/" + moduleName + ".te"
		modPath := "/tmp/" + moduleName + ".mod"
		ppPath := "/tmp/" + moduleName + ".pp"
		defer os.Remove(tePath)  //nolint:errcheck
		defer os.Remove(modPath) //nolint:errcheck
		defer os.Remove(ppPath)  //nolint:errcheck

		if err := os.WriteFile(tePath, []byte(moduleSource), 0644); err != nil {
			return nil, fmt.Errorf("writing .te file: %w", err)
		}
		if out, err := exec.Command("checkmodule", "-M", "-m", "-o", modPath, tePath).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("checkmodule: %s: %w", out, err)
		}
		if out, err := exec.Command("semodule_package", "-o", ppPath, "-m", modPath).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("semodule_package: %s: %w", out, err)
		}
		if out, err := exec.Command("semodule", "-i", ppPath).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("semodule -i %s: %s: %w", ppPath, out, err)
		}
		modulesInstalled = append(modulesInstalled, moduleName)
		snapshot["modules_installed"] = modulesInstalled
	}

	return map[string]any{
		"previous_mode":     currentMode,
		"new_mode":          newMode,
		"modules_installed": modulesInstalled,
		"config_snapshot":   snapshot,
		"applied_at":        time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func selinuxRollbackOS(params map[string]any) (map[string]any, error) {
	configSnapshot, ok := params["config_snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("config_snapshot is required for rollback")
	}
	if prevMode, _ := configSnapshot["previous_mode"].(string); prevMode != "" {
		modeInt := map[string]string{"Enforcing": "1", "Permissive": "0", "enforcing": "1", "permissive": "0"}
		if code, ok := modeInt[prevMode]; ok {
			exec.Command("setenforce", code).Run() //nolint:errcheck
		}
	}
	if configData, _ := configSnapshot["selinux_config"].(string); configData != "" {
		os.WriteFile("/etc/selinux/config", []byte(configData), 0644) //nolint:errcheck
	}
	if modules, _ := configSnapshot["modules_installed"].([]any); len(modules) > 0 {
		for _, m := range modules {
			if name, ok := m.(string); ok && name != "" {
				exec.Command("semodule", "-r", name).Run() //nolint:errcheck
			}
		}
	}
	return map[string]any{"rolled_back": true}, nil
}
