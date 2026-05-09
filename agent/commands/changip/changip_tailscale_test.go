package changip

import (
	"net/http"
	"net/http/httptest"
	"os/exec"
	"runtime"
	"testing"
	"time"
)

func echoCmd(s string) *exec.Cmd {
	if runtime.GOOS == "windows" {
		return exec.Command("cmd", "/c", "echo "+s)
	}
	return exec.Command("echo", s)
}

func failCmd() *exec.Cmd {
	if runtime.GOOS == "windows" {
		return exec.Command("cmd", "/c", "exit 1")
	}
	return exec.Command("false")
}

func TestIsTailscaleActive_Active(t *testing.T) {
	execCommandForTailscale = func(name string, args ...string) *exec.Cmd {
		// Simulate `tailscale ip -4` printing an IP.
		return echoCmd("100.64.0.1")
	}
	t.Cleanup(func() { execCommandForTailscale = exec.Command })

	ip, active := IsTailscaleActive()
	if !active {
		t.Error("expected active=true")
	}
	if ip != "100.64.0.1" {
		t.Errorf("expected IP 100.64.0.1, got %q", ip)
	}
}

func TestIsTailscaleActive_Inactive(t *testing.T) {
	execCommandForTailscale = func(name string, args ...string) *exec.Cmd {
		// Simulate tailscale not installed / exit non-zero.
		return failCmd()
	}
	t.Cleanup(func() { execCommandForTailscale = exec.Command })

	_, active := IsTailscaleActive()
	if active {
		t.Error("expected active=false")
	}
}

func TestIsControlPlaneReachableViaTailscale_Reachable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	ok := IsControlPlaneReachableViaTailscale(srv.URL, 2*time.Second)
	if !ok {
		t.Error("expected reachable=true")
	}
}

func TestIsControlPlaneReachableViaTailscale_Unreachable(t *testing.T) {
	// Point at a port that refuses connections.
	ok := IsControlPlaneReachableViaTailscale("http://127.0.0.1:1", 500*time.Millisecond)
	if ok {
		t.Error("expected reachable=false")
	}
}
