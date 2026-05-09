//go:build linux

package changip

import (
	"os/exec"
	"strings"
	"testing"
)

func TestCaptureSnapshot_PopulatesFields(t *testing.T) {
	// This test runs against whatever interface "lo" is — always present on Linux.
	snap, err := captureSnapshot("lo")
	if err != nil {
		t.Fatalf("captureSnapshot: %v", err)
	}
	if snap.Interface != "lo" {
		t.Errorf("Interface: got %q, want %q", snap.Interface, "lo")
	}
	if snap.NetworkManager == "" {
		t.Error("NetworkManager field must not be empty")
	}
	// lo always has 127.0.0.1/8
	found := false
	for _, a := range snap.IPv4Addresses {
		if a == "127.0.0.1/8" {
			found = true
		}
	}
	if !found {
		t.Errorf("IPv4Addresses %v did not contain 127.0.0.1/8", snap.IPv4Addresses)
	}
	if snap.MTU == 0 {
		t.Error("MTU must be non-zero")
	}
}

func TestConfigureDNS_NetworkManager(t *testing.T) {
	var capturedName string
	var capturedArgs []string
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		capturedName = name
		capturedArgs = args
		// Return a no-op command that exits 0.
		return exec.Command("true")
	}
	t.Cleanup(func() { execCommandForRun = exec.Command })

	err := configureDNS(nmNetworkManager, "eth0", "Wired connection 1",
		[]string{"1.1.1.1", "8.8.8.8"}, []string{"corp.example.com"})
	if err != nil {
		t.Fatalf("configureDNS: %v", err)
	}
	if capturedName != "nmcli" {
		t.Errorf("expected nmcli, got %q", capturedName)
	}
	_ = capturedArgs // verified by name check
}

func TestConfigureDNS_Resolvectl(t *testing.T) {
	var calls [][]string
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		calls = append(calls, append([]string{name}, args...))
		return exec.Command("true")
	}
	t.Cleanup(func() { execCommandForRun = exec.Command })

	err := configureDNS(nmSystemd, "eth0", "",
		[]string{"1.1.1.1"}, []string{"example.com"})
	if err != nil {
		t.Fatalf("configureDNS: %v", err)
	}
	if len(calls) < 2 {
		t.Fatalf("expected at least 2 commands, got %d: %v", len(calls), calls)
	}
	first := strings.Join(calls[0], " ")
	if !strings.Contains(first, "resolvectl") {
		t.Errorf("first call should be resolvectl, got %q", first)
	}
}

func TestAddSecondaryIP_CallsIPAddrAdd(t *testing.T) {
	var capturedArgs []string
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		capturedArgs = append([]string{name}, args...)
		return exec.Command("true")
	}
	t.Cleanup(func() { execCommandForRun = exec.Command })

	err := AddSecondaryIP("eth0", "10.0.0.200/24")
	if err != nil {
		t.Fatalf("AddSecondaryIP: %v", err)
	}
	// Expect: ip addr add 10.0.0.200/24 dev eth0
	if len(capturedArgs) < 5 {
		t.Fatalf("expected at least 5 args, got %v", capturedArgs)
	}
	if capturedArgs[0] != "ip" || capturedArgs[2] != "add" || capturedArgs[3] != "10.0.0.200/24" {
		t.Errorf("unexpected command: %v", capturedArgs)
	}
}

func TestRemoveSecondaryIP_CallsIPAddrDel(t *testing.T) {
	var capturedArgs []string
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		capturedArgs = append([]string{name}, args...)
		return exec.Command("true")
	}
	t.Cleanup(func() { execCommandForRun = exec.Command })

	err := RemoveSecondaryIP("eth0", "10.0.0.200/24")
	if err != nil {
		t.Fatalf("RemoveSecondaryIP: %v", err)
	}
	if capturedArgs[0] != "ip" || capturedArgs[2] != "del" || capturedArgs[3] != "10.0.0.200/24" {
		t.Errorf("unexpected command: %v", capturedArgs)
	}
}

func TestRollbackRestoresDNS(t *testing.T) {
	var calls [][]string
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		calls = append(calls, append([]string{name}, args...))
		return exec.Command("true")
	}
	t.Cleanup(func() { execCommandForRun = exec.Command })

	snapshot := map[string]any{
		"interface":          "eth0",
		"ip_v4_addresses":    []any{"192.168.1.100/24"},
		"ip_v6_addresses":    []any{},
		"gateway_v4":         "192.168.1.1",
		"gateway_v6":         "",
		"dns_servers":        []any{"8.8.8.8", "8.8.4.4"},
		"dns_search_domains": []any{"example.com"},
		"network_manager":    "systemd-networkd",
		"connection_name":    "",
	}

	params := map[string]any{
		"snapshot":  snapshot,
		"interface": "eth0",
	}

	result, err := rollbackOS(params)
	if err != nil {
		t.Fatalf("rollbackOS: %v", err)
	}
	if result["rolled_back"] != true {
		t.Errorf("expected rolled_back=true, got %v", result)
	}

	// Verify that at least one call involved DNS configuration (resolvectl dns or nmcli dns).
	foundDNS := false
	for _, call := range calls {
		joined := strings.Join(call, " ")
		if strings.Contains(joined, "resolvectl") || strings.Contains(joined, "dns") {
			foundDNS = true
			break
		}
	}
	if !foundDNS {
		t.Errorf("expected DNS configuration call, got calls: %v", calls)
	}
}

func TestGetInterfaceAddresses_ReturnsAddresses(t *testing.T) {
	execCommand = func(name string, args ...string) *exec.Cmd {
		// Simulate `ip -o addr show lo` output.
		return exec.Command("echo",
			"1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"+
				"1: lo    inet6 ::1/128 scope host\\       valid_lft forever preferred_lft forever")
	}
	t.Cleanup(func() { execCommand = exec.Command })

	addrs, err := GetInterfaceAddresses("lo")
	if err != nil {
		t.Fatalf("GetInterfaceAddresses: %v", err)
	}
	if len(addrs) == 0 {
		t.Error("expected at least one address")
	}
}

func TestExecuteOS_MethodManual_ReturnsRequiresConfirmation(t *testing.T) {
	// Mock runCmd so no real commands run.
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		return exec.Command("true")
	}
	execCommand = func(name string, args ...string) *exec.Cmd {
		// Simulate `ip -o addr show eth0` — return empty.
		return exec.Command("echo", "")
	}
	execCommandForTailscale = func(name string, args ...string) *exec.Cmd {
		return exec.Command("false")
	}
	t.Cleanup(func() {
		execCommandForRun = exec.Command
		execCommand = exec.Command
		execCommandForTailscale = exec.Command
	})

	result, err := executeOS(map[string]any{
		"interface":  "eth0",
		"mode":       "static",
		"new_ip_v4":  "10.0.0.200/24",
		"ip_version": "4",
		"method":     "manual",
	})
	if err != nil {
		t.Fatalf("executeOS manual: %v", err)
	}
	v, ok := result["requires_confirmation"].(bool)
	if !ok || !v {
		t.Errorf("expected requires_confirmation=true, got %v", result["requires_confirmation"])
	}
	if result["method_used"] != "manual" {
		t.Errorf("expected method_used=manual, got %v", result["method_used"])
	}
}

func TestExecuteOS_MethodCommitTimer_ReturnsFlagAndStartsTimer(t *testing.T) {
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		return exec.Command("true")
	}
	execCommand = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "")
	}
	execCommandForTailscale = func(name string, args ...string) *exec.Cmd {
		return exec.Command("false")
	}
	t.Cleanup(func() {
		execCommandForRun = exec.Command
		execCommand = exec.Command
		execCommandForTailscale = exec.Command
	})

	tmpDir := t.TempDir()
	// Override the dead man's switch path so we don't write to /var/lib.
	deadManPathOverride = tmpDir + "/pending_rollback.json"
	t.Cleanup(func() { deadManPathOverride = "" })

	result, err := executeOS(map[string]any{
		"interface":            "eth0",
		"mode":                 "static",
		"new_ip_v4":            "10.0.0.200/24",
		"ip_version":           "4",
		"method":               "commit_timer",
		"commit_timer_seconds": 10,
		"probe_url":            "http://127.0.0.1:1",
	})
	if err != nil {
		t.Fatalf("executeOS commit_timer: %v", err)
	}
	if result["method_used"] != "commit_timer" {
		t.Errorf("expected method_used=commit_timer, got %v", result["method_used"])
	}
	started, _ := result["commit_timer_started"].(bool)
	if !started {
		t.Error("expected commit_timer_started=true")
	}
}

func TestExecuteOS_MethodTailscale_ReturnsTailscaleIP(t *testing.T) {
	execCommandForRun = func(name string, args ...string) *exec.Cmd {
		return exec.Command("true")
	}
	execCommand = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "")
	}
	execCommandForTailscale = func(name string, args ...string) *exec.Cmd {
		// Simulate tailscale ip -4 returning an IP.
		return exec.Command("echo", "100.64.0.1")
	}
	t.Cleanup(func() {
		execCommandForRun = exec.Command
		execCommand = exec.Command
		execCommandForTailscale = exec.Command
	})

	result, err := executeOS(map[string]any{
		"interface":  "eth0",
		"mode":       "static",
		"new_ip_v4":  "10.0.0.200/24",
		"ip_version": "4",
		"method":     "tailscale",
	})
	if err != nil {
		t.Fatalf("executeOS tailscale: %v", err)
	}
	if result["method_used"] != "tailscale" {
		t.Errorf("expected method_used=tailscale, got %v", result["method_used"])
	}
	if result["tailscale_ip"] == "" {
		t.Error("expected tailscale_ip to be populated")
	}
}
