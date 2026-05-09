package containerizeretire_test

import (
	"testing"

	"nexplane-agent/commands/containerizeretire"
)

func TestContainerizeRetireExecute_MissingUnit(t *testing.T) {
	_, err := containerizeretire.ContainerizeRetireExecute(map[string]any{})
	if err == nil {
		t.Error("expected error for missing systemd_unit parameter")
	}
}

func TestContainerizeRetireExecute_DryRun(t *testing.T) {
	result, err := containerizeretire.ContainerizeRetireExecute(map[string]any{
		"systemd_unit": "nexplane-smoketest.service",
		"dry_run":      true,
	})
	if err != nil {
		t.Fatalf("dry_run should not fail: %v", err)
	}
	if result["action"] != "containerize_retire" {
		t.Errorf("expected action=containerize_retire, got: %v", result["action"])
	}
	if result["dry_run"] != true {
		t.Errorf("expected dry_run=true in result")
	}
	if result["service_stopped"] == true {
		t.Error("service should not be stopped in dry_run mode")
	}
}

func TestContainerizeRetireExecute_UnitNormalization(t *testing.T) {
	result, err := containerizeretire.ContainerizeRetireExecute(map[string]any{
		"systemd_unit": "myapp", // no .service suffix
		"dry_run":      true,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["systemd_unit"] != "myapp.service" {
		t.Errorf("expected myapp.service, got: %v", result["systemd_unit"])
	}
}
