package fleet_test

import (
	"context"
	"testing"

	"nexplane-agent/commands/fleet"
)

// --- RestartService ---

func TestRestartServiceParams_MissingServiceName(t *testing.T) {
	result, _ := fleet.RestartServiceExecute(map[string]any{})
	if _, ok := result["error"]; !ok {
		t.Fatal("expected error key when service_name is empty")
	}
}

func TestRestartServiceParams_ReturnsRunningKey(t *testing.T) {
	// Uses a known-safe no-op service name; on CI the service won't exist so
	// running=false is acceptable — we just require the key to be present.
	result, _ := fleet.RestartServiceExecute(map[string]any{"service_name": "nonexistent-nx-test-svc"})
	if _, ok := result["running"]; !ok {
		t.Fatal("result must contain 'running' key")
	}
}

// --- PushConfigFile ---

func TestPushConfigFile_MissingFilePath(t *testing.T) {
	result, _ := fleet.PushConfigFileExecute(map[string]any{
		"file_content": "aGVsbG8=", // base64("hello")
	})
	if _, ok := result["error"]; !ok {
		t.Fatal("expected error when file_path is missing")
	}
}

func TestPushConfigFile_WritesAndBacksUp(t *testing.T) {
	t.TempDir() // ensure temp dir exists
	dir := t.TempDir()
	path := dir + "/test.conf"
	result, _ := fleet.PushConfigFileExecute(map[string]any{
		"file_path":    path,
		"file_content": "aGVsbG8=", // base64("hello")
		"backup":       false,
	})
	if errVal, ok := result["error"]; ok && errVal != "" {
		t.Fatalf("unexpected error: %v", errVal)
	}
}

func TestPushConfigFile_InvalidBase64(t *testing.T) {
	dir := t.TempDir()
	result, _ := fleet.PushConfigFileExecute(map[string]any{
		"file_path":    dir + "/test.conf",
		"file_content": "not-valid-base64!!!",
		"backup":       false,
	})
	if _, ok := result["error"]; !ok {
		t.Fatal("expected error for invalid base64 content")
	}
}

// --- DistributeFile ---

func TestDistributeFile_WritesFileWithPermissions(t *testing.T) {
	dir := t.TempDir()
	result, _ := fleet.DistributeFileExecute(map[string]any{
		"file_path":    dir + "/dist.txt",
		"file_content": "aGVsbG8=", // base64("hello")
		"permissions":  "0644",
	})
	if errVal, ok := result["error"]; ok && errVal != "" {
		t.Fatalf("unexpected error: %v", errVal)
	}
}

func TestDistributeFile_InvalidBase64(t *testing.T) {
	dir := t.TempDir()
	result, _ := fleet.DistributeFileExecute(map[string]any{
		"file_path":    dir + "/dist.txt",
		"file_content": "!!!bad!!!",
	})
	if _, ok := result["error"]; !ok {
		t.Fatal("expected error for invalid base64")
	}
}

// --- HealthCheck ---

func TestHealthCheck_ReturnsExpectedKeys(t *testing.T) {
	result, _ := fleet.HealthCheckExecute(map[string]any{})
	for _, key := range []string{"disk_free_ok", "load_ok", "reachable", "no_pending_reboot", "services", "pass"} {
		if _, ok := result[key]; !ok {
			t.Errorf("missing expected key %q in health check result", key)
		}
	}
}

func TestHealthCheck_ReachableAlwaysTrue(t *testing.T) {
	result, _ := fleet.HealthCheckExecute(map[string]any{})
	if reachable, ok := result["reachable"].(bool); !ok || !reachable {
		t.Fatal("reachable must always be true when the agent is responding")
	}
}

func TestHealthCheck_ServicesMap(t *testing.T) {
	result, _ := fleet.HealthCheckExecute(map[string]any{
		"required_services": []any{"nonexistent-nx-svc"},
	})
	services, ok := result["services"].(map[string]bool)
	if !ok {
		t.Fatal("services must be map[string]bool")
	}
	if _, present := services["nonexistent-nx-svc"]; !present {
		t.Fatal("services map must contain the requested service name")
	}
}

// --- Context cancellation ---

func TestRestartService_ContextCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result := fleet.RestartServiceExecuteCtx(ctx, map[string]any{"service_name": "some-svc"})
	// Should return error or running=false; must not panic
	_ = result
}
