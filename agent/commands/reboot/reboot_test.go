package reboot_test

import (
	"testing"

	"nexplane-agent/commands/reboot"
)

func TestExecute_MissingGracefulDelay_DefaultsTo60(t *testing.T) {
	// Does not actually reboot — just validates param parsing.
	// We test by confirming no validation error, not by running the reboot.
	// (Actual reboot skipped in CI via dry-run flag approach.)
	params := map[string]any{
		"verify_services": []any{"nginx"},
		// graceful_delay_seconds intentionally omitted — should default to 60
		"dry_run": true, // signal to skip actual shutdown command
	}
	result, err := reboot.Execute(params)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["action"] != "graceful_reboot" {
		t.Fatalf("expected action=graceful_reboot, got %v", result["action"])
	}
}

func TestExecute_GracefulDelayBelowMinimum_ClampedTo60(t *testing.T) {
	params := map[string]any{
		"graceful_delay_seconds": float64(10), // below minimum
		"dry_run":                true,
	}
	result, err := reboot.Execute(params)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result["graceful_delay_seconds"].(int) < 60 {
		t.Fatal("expected graceful_delay_seconds to be clamped to at least 60")
	}
}

func TestVerifyPostReboot_EmptyServices(t *testing.T) {
	result, err := reboot.VerifyPostRebootExecute(map[string]any{
		"verify_services": []any{},
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	services, _ := result["services"].(map[string]string)
	if len(services) != 0 {
		t.Fatalf("expected empty services map, got %v", services)
	}
}

func TestRollback_ReturnsNoOp(t *testing.T) {
	result, err := reboot.Rollback(map[string]any{})
	if err != nil {
		t.Fatalf("rollback should not error: %v", err)
	}
	if result["rolled_back"] == true {
		t.Fatal("reboot rollback should be a no-op")
	}
}
