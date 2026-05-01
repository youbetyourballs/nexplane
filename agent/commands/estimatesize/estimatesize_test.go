package estimatesize_test

import (
	"os"
	"testing"

	"nexplane-agent/commands/estimatesize"
)

func TestExecuteReturnsResult(t *testing.T) {
	result, err := estimatesize.Execute(map[string]any{
		"destination_path": os.TempDir(),
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, ok := result["sufficient_space"]; !ok {
		t.Error("result should contain sufficient_space")
	}
	if _, ok := result["destination_available_bytes"]; !ok {
		t.Error("result should contain destination_available_bytes")
	}
}

func TestExecuteUsesDefaultPath(t *testing.T) {
	result, err := estimatesize.Execute(map[string]any{})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["destination_path"] == "" {
		t.Error("result should contain destination_path")
	}
}
