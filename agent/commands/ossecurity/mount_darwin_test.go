//go:build darwin

package ossecurity

import (
	"os/exec"
	"testing"
)

func TestMountExecuteOS_DarwinSIPProtected(t *testing.T) {
	execCommandMount = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "/System on / (apfs, local, journaled)")
	}
	t.Cleanup(func() { execCommandMount = exec.Command })

	result, err := mountExecuteOS(map[string]any{
		"path":    "/System",
		"options": []any{"noexec"},
	})
	if err != nil {
		t.Fatalf("mountExecuteOS: %v", err)
	}
	skipped, _ := result["skipped"].(bool)
	if !skipped {
		t.Fatalf("expected skipped=true for SIP-protected path; got: %v", result)
	}
}

func TestMountExecuteOS_DarwinUserVolume(t *testing.T) {
	execCommandMount = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "ok")
	}
	t.Cleanup(func() { execCommandMount = exec.Command })

	result, err := mountExecuteOS(map[string]any{
		"path":    "/Volumes/Data",
		"options": []any{"noexec", "nosuid"},
	})
	if err != nil {
		t.Fatalf("mountExecuteOS: %v", err)
	}
	_ = result
}
