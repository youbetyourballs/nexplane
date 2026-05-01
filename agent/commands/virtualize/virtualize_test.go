package virtualize_test

import (
	"os"
	"path/filepath"
	"testing"

	"nexplane-agent/commands/virtualize"
)

func TestExecuteRequiresImagePath(t *testing.T) {
	_, err := virtualize.Execute(map[string]any{
		"target_mode": "static",
	})
	if err == nil {
		t.Error("expected error for missing image_path")
	}
}

func TestExecuteRequiresTargetMode(t *testing.T) {
	_, err := virtualize.Execute(map[string]any{
		"image_path": filepath.Join(os.TempDir(), "test-nexplane.img"),
	})
	if err == nil {
		t.Error("expected error for missing target_mode")
	}
}

func TestRollbackDeletesImageFile(t *testing.T) {
	f, err := os.CreateTemp("", "nexplane-test-*.img")
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
	imagePath := f.Name()

	result, err := virtualize.Rollback(map[string]any{
		"image_path": imagePath,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["deleted"] != true {
		t.Error("expected deleted=true")
	}
	if _, err := os.Stat(imagePath); !os.IsNotExist(err) {
		os.Remove(imagePath)
		t.Error("expected image file to be deleted")
	}
}
