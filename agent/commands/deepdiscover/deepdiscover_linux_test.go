//go:build linux

package deepdiscover

import (
	"os/exec"
	"testing"
)

// TestExecuteReturnsWorkloads verifies Execute returns the expected keys.
func TestExecuteReturnsWorkloads(t *testing.T) {
	result, err := Execute(map[string]any{})
	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	if result["action"] != "deep_discover" {
		t.Errorf("expected action=deep_discover, got %v", result["action"])
	}
	if _, ok := result["workloads"]; !ok {
		t.Error("expected workloads key in result")
	}
}

// TestCollectListeningPortsReturnsSlice verifies the function returns a non-nil slice.
func TestCollectListeningPortsReturnsSlice(t *testing.T) {
	ports := collectListeningPortsLinux()
	if ports == nil {
		t.Error("expected non-nil slice from collectListeningPortsLinux")
	}
}

// TestDetectContainerRuntimesNoError verifies the function returns a non-nil slice.
func TestDetectContainerRuntimesNoError(t *testing.T) {
	runtimes := detectContainerRuntimesLinux()
	if runtimes == nil {
		t.Error("expected non-nil slice from detectContainerRuntimesLinux")
	}
}

// TestWorkloadHasEnrichmentFields verifies that workload maps include enrichment fields.
func TestWorkloadHasEnrichmentFields(t *testing.T) {
	result, err := Execute(map[string]any{})
	if err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}
	workloads, ok := result["workloads"].([]map[string]any)
	if !ok {
		t.Skip("no workloads on this machine")
	}
	if len(workloads) == 0 {
		t.Skip("no workloads on this machine")
	}
	w := workloads[0]
	for _, key := range []string{"pid_found", "env_var_names", "open_files", "runtime_deps", "config_intelligence"} {
		if _, ok := w[key]; !ok {
			t.Errorf("workload missing key: %s", key)
		}
	}
}

func TestCollectEnvVarNamesForPid1(t *testing.T) {
	// PID 1 always exists on Linux; may be unreadable in some containers
	names := collectEnvVarNamesLinux(1)
	// Must not panic; result may be empty in restricted environments
	_ = names
}

func TestCollectOpenFilesForPid1(t *testing.T) {
	files := collectOpenFilesLinux(1)
	if files == nil {
		t.Error("expected non-nil slice from collectOpenFilesLinux")
	}
}

func TestCollectRuntimeDepsForBinary(t *testing.T) {
	// /bin/sh exists on all Linux systems
	deps := collectRuntimeDepsLinux("/bin/sh")
	// ldd output may be empty for static binaries; must not panic
	_ = deps
}

// TestExecCommandHookable verifies that execCommandLinux is mockable.
func TestExecCommandHookable(t *testing.T) {
	var called []string

	original := execCommandLinux
	defer func() { execCommandLinux = original }()

	execCommandLinux = func(name string, args ...string) *exec.Cmd {
		called = append(called, name)
		// Fall back to a no-op command that exits 0 with empty output.
		return exec.Command("true")
	}

	_ = collectListeningPortsLinux()

	if len(called) == 0 {
		t.Error("expected execCommandLinux mock to be called at least once")
	}
}
