//go:build windows

package deepdiscover

import (
	"os/exec"
	"testing"
)

func TestExecuteWindowsReturnsWorkloads(t *testing.T) {
	result, err := Execute(map[string]any{})
	if err != nil {
		t.Fatalf("Execute() returned error: %v", err)
	}
	if _, ok := result["action"]; !ok {
		t.Error("result missing 'action' key")
	}
	if _, ok := result["workloads"]; !ok {
		t.Error("result missing 'workloads' key")
	}
}

func TestCollectWindowsServicesNoError(t *testing.T) {
	services := collectWindowsServices()
	if services == nil {
		t.Error("collectWindowsServices() returned nil, expected non-nil slice")
	}
}

func TestExecCommandWindowsHookable(t *testing.T) {
	called := false
	orig := execCommandWindows
	defer func() { execCommandWindows = orig }()

	execCommandWindows = func(name string, args ...string) *exec.Cmd {
		called = true
		// Return a command that exits with error so we get empty results
		return exec.Command("cmd", "/C", "exit 1")
	}

	_ = collectWindowsServices()

	if !called {
		t.Error("execCommandWindows hook was not called by collectWindowsServices()")
	}
}
