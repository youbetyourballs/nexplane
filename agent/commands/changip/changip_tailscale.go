package changip

import (
	"fmt"
	"net/http"
	"os/exec"
	"strings"
	"time"
)

// execCommandForTailscale is a hook for tests to replace exec.Command.
var execCommandForTailscale = exec.Command

// IsTailscaleActive returns the Tailscale IPv4 address and true if tailscaled
// is running and has an active IP. Returns ("", false) otherwise.
func IsTailscaleActive() (tailscaleIP string, active bool) {
	out, err := execCommandForTailscale("tailscale", "ip", "-4").Output()
	if err != nil {
		return "", false
	}
	ip := strings.TrimSpace(string(out))
	if ip == "" {
		return "", false
	}
	return ip, true
}

// IsControlPlaneReachableViaTailscale probes {controlPlaneURL}/health with an
// HTTP GET and returns true if an HTTP 200 is received within timeout.
func IsControlPlaneReachableViaTailscale(controlPlaneURL string, timeout time.Duration) bool {
	client := &http.Client{Timeout: timeout}
	url := strings.TrimRight(controlPlaneURL, "/") + "/health"
	resp, err := client.Get(url)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == http.StatusOK
}

// tailscaleHealthURL builds the control-plane health URL via the Tailscale IP.
// port is the control plane HTTP port (e.g. "8000").
func tailscaleHealthURL(tsIP, port string) string {
	return fmt.Sprintf("http://%s:%s", tsIP, port)
}
