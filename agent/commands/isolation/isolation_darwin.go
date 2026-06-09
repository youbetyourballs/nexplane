//go:build darwin

package isolation

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
)

var execCommandIsolation = exec.Command

func isolateOS(ctx context.Context, cfg IsolationConfig) (*PreIsolationState, error) {
	state := &PreIsolationState{OS: "darwin"}

	// Capture current pf ruleset
	if out, err := execCommandIsolation("pfctl", "-s", "rules").CombinedOutput(); err == nil {
		state.PFRules = string(out)
	}

	cpIP, err := resolveHostDarwin(cfg.ControlPlaneURL)
	if err != nil {
		return nil, fmt.Errorf("cannot resolve control plane host: %w", err)
	}

	rules := fmt.Sprintf(`# Nexplane isolation rules
pass in quick on lo0 all
pass out quick on lo0 all
pass out quick proto tcp to %s port 443
pass out quick proto tcp to %s port 22
block out all
block in all
`, cpIP, cfg.ManagementCIDR)

	tmpFile := "/tmp/nexplane-pf-iso.conf"
	if err := os.WriteFile(tmpFile, []byte(rules), 0600); err != nil {
		return nil, fmt.Errorf("writing pf rules: %w", err)
	}
	defer os.Remove(tmpFile)

	if out, err := execCommandIsolation("pfctl", "-e", "-f", tmpFile).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("pfctl -e -f: %s: %w", out, err)
	}

	return state, nil
}

func restoreOS(ctx context.Context, state *PreIsolationState) error {
	if state.PFRules == "" {
		execCommandIsolation("pfctl", "-d").CombinedOutput() //nolint:errcheck
		return nil
	}

	tmpFile := "/tmp/nexplane-pf-restore.conf"
	if err := os.WriteFile(tmpFile, []byte(state.PFRules), 0600); err != nil {
		return fmt.Errorf("writing restore rules: %w", err)
	}
	defer os.Remove(tmpFile)

	if out, err := execCommandIsolation("pfctl", "-f", tmpFile).CombinedOutput(); err != nil {
		return fmt.Errorf("pfctl -f restore: %s: %w", out, err)
	}
	return nil
}

func resolveHostDarwin(rawURL string) (string, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return "", err
	}
	host := u.Hostname()
	if net.ParseIP(host) != nil {
		return host, nil
	}
	addrs, err := net.LookupHost(host)
	if err != nil || len(addrs) == 0 {
		return "", fmt.Errorf("cannot resolve %q: %w", host, err)
	}
	return addrs[0], nil
}
