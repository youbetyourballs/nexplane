package uploadimage_test

import (
	"testing"

	"nexplane-agent/commands/uploadimage"
)

func TestExecuteRequiresImagePath(t *testing.T) {
	_, err := uploadimage.Execute(map[string]any{
		"destination_uri": "s3://bucket/key",
	})
	if err == nil {
		t.Error("expected error for missing image_path")
	}
}

func TestExecuteRequiresDestinationURI(t *testing.T) {
	_, err := uploadimage.Execute(map[string]any{
		"image_path": "/tmp/test.img",
	})
	if err == nil {
		t.Error("expected error for missing destination_uri")
	}
}

func TestExecuteRejectsUnsupportedScheme(t *testing.T) {
	_, err := uploadimage.Execute(map[string]any{
		"image_path":      "/tmp/test.img",
		"destination_uri": "ftp://bucket/key",
	})
	if err == nil {
		t.Error("expected error for unsupported scheme")
	}
}

func TestRollbackRequiresDestinationURI(t *testing.T) {
	_, err := uploadimage.Rollback(map[string]any{})
	if err == nil {
		t.Error("expected error for missing destination_uri in rollback")
	}
}
