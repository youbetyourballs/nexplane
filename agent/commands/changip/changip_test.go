//go:build windows

package changip

import (
	"os/exec"
	"strings"
	"testing"
)

func TestExecuteRequiresInterface(t *testing.T) {
	_, err := Execute(map[string]any{
		"mode": "static",
	})
	if err == nil {
		t.Error("expected error for missing interface")
	}
}

func TestExecuteRequiresMode(t *testing.T) {
	_, err := Execute(map[string]any{
		"interface": "Ethernet",
	})
	if err == nil {
		t.Error("expected error for missing mode")
	}
}

func TestExecuteInvalidMode(t *testing.T) {
	_, err := Execute(map[string]any{
		"interface": "Ethernet",
		"mode":      "invalid",
	})
	if err == nil {
		t.Error("expected error for invalid mode")
	}
}

func TestCaptureSnapshotW_PopulatesFields(t *testing.T) {
	snap, err := captureSnapshotW("Loopback Pseudo-Interface 1")
	if err != nil {
		t.Fatalf("captureSnapshotW: %v", err)
	}
	if snap.Interface == "" {
		t.Error("Interface must not be empty")
	}
	if snap.NetworkManager == "" {
		t.Error("NetworkManager must not be empty")
	}
}

func TestConfigureDNSW_SetsServers(t *testing.T) {
	var calls [][]string
	execCommandForRunW = func(name string, args ...string) *exec.Cmd {
		calls = append(calls, append([]string{name}, args...))
		return exec.Command("cmd", "/c", "exit 0")
	}
	t.Cleanup(func() { execCommandForRunW = exec.Command })

	err := configureDNSW("Ethernet", []string{"1.1.1.1", "8.8.8.8"})
	if err != nil {
		t.Fatalf("configureDNSW: %v", err)
	}
	if len(calls) < 2 {
		t.Fatalf("expected at least 2 netsh calls, got %d: %v", len(calls), calls)
	}
	// First call: set primary static
	first := strings.Join(calls[0], " ")
	if !strings.Contains(first, "static") || !strings.Contains(first, "1.1.1.1") {
		t.Errorf("first call should set primary DNS 1.1.1.1: %q", first)
	}
	// Second call: add secondary
	second := strings.Join(calls[1], " ")
	if !strings.Contains(second, "8.8.8.8") || !strings.Contains(second, "index=2") {
		t.Errorf("second call should add secondary DNS 8.8.8.8 at index=2: %q", second)
	}
}

func TestAddSecondaryIPW_CallsNetsh(t *testing.T) {
	var capturedArgs []string
	execCommandForRunW = func(name string, args ...string) *exec.Cmd {
		capturedArgs = append([]string{name}, args...)
		return exec.Command("cmd", "/c", "exit 0")
	}
	t.Cleanup(func() { execCommandForRunW = exec.Command })

	err := AddSecondaryIPW("Ethernet", "10.0.0.200", "255.255.255.0")
	if err != nil {
		t.Fatalf("AddSecondaryIPW: %v", err)
	}
	if capturedArgs[0] != "netsh" {
		t.Errorf("expected netsh, got %q", capturedArgs[0])
	}
	joined := strings.Join(capturedArgs, " ")
	if !strings.Contains(joined, "add") || !strings.Contains(joined, "10.0.0.200") {
		t.Errorf("expected add address command: %v", capturedArgs)
	}
}

func TestRemoveSecondaryIPW_CallsNetsh(t *testing.T) {
	var capturedArgs []string
	execCommandForRunW = func(name string, args ...string) *exec.Cmd {
		capturedArgs = append([]string{name}, args...)
		return exec.Command("cmd", "/c", "exit 0")
	}
	t.Cleanup(func() { execCommandForRunW = exec.Command })

	err := RemoveSecondaryIPW("Ethernet", "10.0.0.200")
	if err != nil {
		t.Fatalf("RemoveSecondaryIPW: %v", err)
	}
	joined := strings.Join(capturedArgs, " ")
	if !strings.Contains(joined, "delete") || !strings.Contains(joined, "10.0.0.200") {
		t.Errorf("expected delete address command: %v", capturedArgs)
	}
}
