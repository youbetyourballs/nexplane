package executor_test

import (
	"os"
	"strings"
	"testing"

	"nexplane-agent/executor"
)

func TestDispatchUnknownCommandReturnsError(t *testing.T) {
	result := executor.Dispatch("unknown_command", map[string]any{}, false, map[string]any{})
	if result.Status != "failed" {
		t.Errorf("unknown command should fail, got status %q", result.Status)
	}
	if result.Error == "" {
		t.Error("unknown command should set error message")
	}
}

func TestDispatch_DBAdminCommandsRegistered(t *testing.T) {
	commands := []string{
		"provision_db_user",
		"deprovision_db_user",
		"db_permission_change",
		"configure_db_audit",
		"db_connection_config",
	}
	for _, cmd := range commands {
		result := executor.Dispatch(cmd, map[string]any{}, false, nil)
		// Should fail with a meaningful error (missing params), not "unknown command"
		if result.Status == "failed" && strings.Contains(result.Error, "unknown command") {
			t.Errorf("command %q is not registered in executor", cmd)
		}
	}
}

func TestDispatchKnownCommandSucceeds(t *testing.T) {
	result := executor.Dispatch("estimate_image_size", map[string]any{
		"destination_path": os.TempDir(),
	}, false, map[string]any{})
	if result.Status != "completed" {
		t.Errorf("estimate_image_size should complete, got status=%q error=%q", result.Status, result.Error)
	}
}
