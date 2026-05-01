package executor_test

import (
	"os"
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

func TestDispatchKnownCommandSucceeds(t *testing.T) {
	result := executor.Dispatch("estimate_image_size", map[string]any{
		"destination_path": os.TempDir(),
	}, false, map[string]any{})
	if result.Status != "completed" {
		t.Errorf("estimate_image_size should complete, got status=%q error=%q", result.Status, result.Error)
	}
}
