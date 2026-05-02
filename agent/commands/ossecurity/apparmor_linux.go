//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

func apparmorExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_apparmor requires root privileges")
	}
	if _, err := exec.LookPath("aa-status"); err != nil {
		return nil, fmt.Errorf("AppArmor not available: aa-status not found")
	}

	profileName, _ := params["profile_name"].(string)
	profileContent, _ := params["profile_content"].(string)
	mode, _ := params["mode"].(string)

	statusOut, _ := exec.Command("aa-status", "--json").Output()
	snapshot := map[string]any{
		"aa_status": string(statusOut),
		"profile":   profileName,
	}

	if profileContent != "" && profileName != "" {
		profilePath := filepath.Join("/etc/apparmor.d", profileName)
		if existing, err := os.ReadFile(profilePath); err == nil {
			snapshot["previous_profile_content"] = string(existing)
		}
		if err := os.WriteFile(profilePath, []byte(profileContent), 0644); err != nil {
			return nil, fmt.Errorf("writing AppArmor profile: %w", err)
		}
		if out, err := exec.Command("apparmor_parser", "-r", profilePath).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("apparmor_parser: %s: %w", out, err)
		}
	}

	appliedMode := ""
	if mode != "" && profileName != "" {
		var cmd *exec.Cmd
		switch mode {
		case "enforce":
			cmd = exec.Command("aa-enforce", profileName)
		case "complain":
			cmd = exec.Command("aa-complain", profileName)
		case "disable":
			cmd = exec.Command("aa-disable", profileName)
		}
		if cmd != nil {
			if out, err := cmd.CombinedOutput(); err != nil {
				return nil, fmt.Errorf("aa-%s %s: %s: %w", mode, profileName, out, err)
			}
			appliedMode = mode
		}
	}

	return map[string]any{
		"profile_name": profileName,
		"mode_applied": appliedMode,
		"snapshot":     snapshot,
		"applied_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func apparmorRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	profileName, _ := snapshot["profile"].(string)
	if prev, ok := snapshot["previous_profile_content"].(string); ok && profileName != "" {
		profilePath := filepath.Join("/etc/apparmor.d", profileName)
		if prev == "" {
			os.Remove(profilePath)
		} else {
			os.WriteFile(profilePath, []byte(prev), 0644)            //nolint:errcheck
			exec.Command("apparmor_parser", "-r", profilePath).Run() //nolint:errcheck
		}
	}
	return map[string]any{"rolled_back": true}, nil
}
