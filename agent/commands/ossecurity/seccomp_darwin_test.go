//go:build darwin

package ossecurity

import (
	"os"
	"strings"
	"testing"
)

func TestSeccompExecuteOS_DarwinWritesProfile(t *testing.T) {
	tmpDir := t.TempDir()
	originalDir := sandboxProfileDir
	sandboxProfileDir = tmpDir
	t.Cleanup(func() { sandboxProfileDir = originalDir })

	params := map[string]any{
		"service_name": "test-service",
		"profile":      "(version 1)\n(deny default)\n(allow file-read*)",
	}
	result, err := seccompExecuteOS(params)
	if err != nil {
		t.Fatalf("seccompExecuteOS: %v", err)
	}
	profilePath, _ := result["profile_path"].(string)
	if profilePath == "" {
		t.Fatal("expected profile_path in result")
	}
	if _, err := os.Stat(profilePath); err != nil {
		t.Fatalf("profile file not created: %v", err)
	}
	data, _ := os.ReadFile(profilePath)
	if !strings.Contains(string(data), "deny default") {
		t.Fatal("profile content not written")
	}
}

func TestSeccompRollbackOS_DarwinRestores(t *testing.T) {
	tmpDir := t.TempDir()
	originalDir := sandboxProfileDir
	sandboxProfileDir = tmpDir
	t.Cleanup(func() { sandboxProfileDir = originalDir })

	result, err := seccompRollbackOS(map[string]any{
		"service_name": "test-service",
		"snapshot":     "previous-content",
	})
	if err != nil {
		t.Fatalf("seccompRollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Fatal("expected rolled_back=true")
	}
}
