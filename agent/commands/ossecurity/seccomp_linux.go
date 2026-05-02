//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

func seccompExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_seccomp requires root privileges")
	}
	serviceName, _ := params["service_name"].(string)
	profile, _ := params["profile"].(string)

	dropInDir := fmt.Sprintf("/etc/systemd/system/%s.service.d", serviceName)
	if err := os.MkdirAll(dropInDir, 0755); err != nil {
		return nil, fmt.Errorf("creating drop-in dir: %w", err)
	}
	dropInPath := filepath.Join(dropInDir, "nexplane-seccomp.conf")

	snapshot := ""
	if data, err := os.ReadFile(dropInPath); err == nil {
		snapshot = string(data)
	}

	seccompDir := "/etc/nexplane/seccomp"
	if err := os.MkdirAll(seccompDir, 0755); err != nil {
		return nil, fmt.Errorf("creating seccomp dir: %w", err)
	}
	profilePath := filepath.Join(seccompDir, serviceName+".json")
	if err := os.WriteFile(profilePath, []byte(profile), 0644); err != nil {
		return nil, fmt.Errorf("writing seccomp profile: %w", err)
	}

	dropInContent := fmt.Sprintf("[Service]\nSeccompFilter=%s\nSystemCallErrorNumber=EPERM\n", profilePath)
	if err := os.WriteFile(dropInPath, []byte(dropInContent), 0644); err != nil {
		return nil, fmt.Errorf("writing drop-in: %w", err)
	}

	return map[string]any{
		"service_name": serviceName,
		"drop_in_path": dropInPath,
		"profile_path": profilePath,
		"snapshot":     snapshot,
		"applied_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func seccompRollbackOS(params map[string]any) (map[string]any, error) {
	serviceName, _ := params["service_name"].(string)
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	dropInPath := fmt.Sprintf("/etc/systemd/system/%s.service.d/nexplane-seccomp.conf", serviceName)
	profilePath := fmt.Sprintf("/etc/nexplane/seccomp/%s.json", serviceName)
	os.Remove(profilePath)
	if snapshot == "" {
		os.Remove(dropInPath)
	} else {
		os.WriteFile(dropInPath, []byte(snapshot), 0644) //nolint:errcheck
	}
	return map[string]any{"rolled_back": true, "service": serviceName}, nil
}
