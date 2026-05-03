//go:build windows

package isolation

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func isolateOS(ctx context.Context, cfg IsolationConfig) (*PreIsolationState, error) {
	state := &PreIsolationState{OS: "windows"}

	// Export current firewall policy to a temp file
	tmpFile := filepath.Join(os.TempDir(), "wfw_pre_isolation.wfw")
	if out, err := exec.CommandContext(ctx, "netsh", "advfirewall", "export", tmpFile).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("netsh advfirewall export: %s: %w", out, err)
	}
	if data, err := os.ReadFile(tmpFile); err == nil {
		state.WFWRules = string(data)
	}

	cpIP, err := resolveHost(cfg.ControlPlaneURL)
	if err != nil {
		return nil, fmt.Errorf("cannot resolve control plane host: %w", err)
	}

	cmds := [][]string{
		{"netsh", "advfirewall", "set", "allprofiles", "firewallpolicy", "blockinbound,blockoutbound"},
		{"netsh", "advfirewall", "firewall", "delete", "rule", "name=NX_IR_MGMT_OUT"},
		{"netsh", "advfirewall", "firewall", "delete", "rule", "name=NX_IR_CP_OUT"},
		{"netsh", "advfirewall", "firewall", "add", "rule",
			"name=NX_IR_MGMT_OUT", "dir=out", "action=allow",
			"remoteip=" + cfg.ManagementCIDR, "protocol=any"},
		{"netsh", "advfirewall", "firewall", "add", "rule",
			"name=NX_IR_CP_OUT", "dir=out", "action=allow",
			"remoteip=" + cpIP, "protocol=tcp", "localport=443"},
	}

	for _, args := range cmds {
		// "delete" commands may fail if the rule doesn't exist — ignore those errors
		out, err := exec.CommandContext(ctx, args[0], args[1:]...).CombinedOutput()
		if err != nil && !strings.Contains(string(out), "No rules match") {
			return nil, fmt.Errorf("netsh %v: %s: %w", args[1:], out, err)
		}
	}

	return state, nil
}

func restoreOS(ctx context.Context, state *PreIsolationState) error {
	if state.WFWRules == "" {
		return fmt.Errorf("no WFW rules snapshot available for restore")
	}
	tmpFile := filepath.Join(os.TempDir(), "wfw_restore.wfw")
	if err := os.WriteFile(tmpFile, []byte(state.WFWRules), 0600); err != nil {
		return fmt.Errorf("writing restore file: %w", err)
	}
	out, err := exec.CommandContext(ctx, "netsh", "advfirewall", "import", tmpFile).CombinedOutput()
	if err != nil {
		return fmt.Errorf("netsh advfirewall import: %s: %w", out, err)
	}
	return nil
}

func resolveHost(rawURL string) (string, error) {
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
