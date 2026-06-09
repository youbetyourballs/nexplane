//go:build darwin

package ossecurity

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

var sandboxProfileDir = "/private/etc/nexplane/sandbox"

func seccompExecuteOS(params map[string]any) (map[string]any, error) {
	serviceName, _ := params["service_name"].(string)
	if serviceName == "" {
		return nil, fmt.Errorf("service_name is required")
	}
	profile, _ := params["profile"].(string)
	if profile == "" {
		return nil, fmt.Errorf("profile (sandbox SBPL content) is required")
	}

	if err := os.MkdirAll(sandboxProfileDir, 0755); err != nil {
		return nil, fmt.Errorf("creating sandbox profile dir: %w", err)
	}

	profilePath := filepath.Join(sandboxProfileDir, serviceName+".sb")

	snapshot := ""
	if data, err := os.ReadFile(profilePath); err == nil {
		snapshot = string(data)
	}

	if err := os.WriteFile(profilePath, []byte(profile), 0644); err != nil {
		return nil, fmt.Errorf("writing sandbox profile: %w", err)
	}

	return map[string]any{
		"service_name": serviceName,
		"profile_path": profilePath,
		"snapshot":     snapshot,
		"note":         "Apply with: sandbox-exec -f " + profilePath + " <command>",
		"applied_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func seccompRollbackOS(params map[string]any) (map[string]any, error) {
	serviceName, _ := params["service_name"].(string)
	if serviceName == "" {
		return nil, fmt.Errorf("service_name is required for rollback")
	}
	snapshot, _ := params["snapshot"].(string)
	profilePath := filepath.Join(sandboxProfileDir, serviceName+".sb")

	if snapshot == "" {
		os.Remove(profilePath)
	} else {
		if err := os.WriteFile(profilePath, []byte(snapshot), 0644); err != nil {
			return nil, fmt.Errorf("restoring sandbox profile: %w", err)
		}
	}
	return map[string]any{"rolled_back": true}, nil
}
