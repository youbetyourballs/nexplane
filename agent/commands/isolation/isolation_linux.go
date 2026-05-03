//go:build linux

package isolation

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"os"
	"os/exec"
	"strings"
)

func isolateOS(ctx context.Context, cfg IsolationConfig) (*PreIsolationState, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("isolate_host requires root privileges")
	}

	state := &PreIsolationState{OS: "linux"}

	// Capture iptables state
	if out, err := exec.CommandContext(ctx, "iptables-save").Output(); err == nil {
		state.IPTablesRules = string(out)
	}
	// Capture nftables state if nft is available
	if _, err := exec.LookPath("nft"); err == nil {
		if out, err := exec.CommandContext(ctx, "nft", "list", "ruleset").Output(); err == nil {
			state.NFTablesRules = string(out)
		}
	}

	cpIP, err := resolveHost(cfg.ControlPlaneURL)
	if err != nil {
		return nil, fmt.Errorf("cannot resolve control plane host: %w", err)
	}

	// Apply isolation rules for IPv4
	cmds := [][]string{
		{"iptables", "-F", "OUTPUT"},
		{"iptables", "-F", "FORWARD"},
		{"iptables", "-A", "OUTPUT", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"},
		{"iptables", "-A", "OUTPUT", "-d", cfg.ManagementCIDR, "-p", "tcp", "--dport", "22", "-j", "ACCEPT"},
		{"iptables", "-A", "OUTPUT", "-d", cpIP, "-p", "tcp", "--dport", "443", "-j", "ACCEPT"},
		{"iptables", "-P", "OUTPUT", "DROP"},
		{"iptables", "-P", "FORWARD", "DROP"},
		// Repeat for IPv6
		{"ip6tables", "-F", "OUTPUT"},
		{"ip6tables", "-F", "FORWARD"},
		{"ip6tables", "-A", "OUTPUT", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"},
		{"ip6tables", "-P", "OUTPUT", "DROP"},
		{"ip6tables", "-P", "FORWARD", "DROP"},
	}

	for _, args := range cmds {
		if out, err := exec.CommandContext(ctx, args[0], args[1:]...).CombinedOutput(); err != nil {
			// ip6tables may not be present — log warning but continue
			if args[0] == "ip6tables" {
				continue
			}
			return nil, fmt.Errorf("%s %v: %s: %w", args[0], args[1:], out, err)
		}
	}

	return state, nil
}

func restoreOS(ctx context.Context, state *PreIsolationState) error {
	if state.IPTablesRules != "" {
		cmd := exec.CommandContext(ctx, "iptables-restore")
		cmd.Stdin = strings.NewReader(state.IPTablesRules)
		if out, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("iptables-restore: %s: %w", out, err)
		}
	}
	if state.NFTablesRules != "" {
		cmd := exec.CommandContext(ctx, "nft", "-f", "-")
		cmd.Stdin = strings.NewReader(state.NFTablesRules)
		if out, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("nft restore: %s: %w", out, err)
		}
	}
	return nil
}

// resolveHost returns the IP address of the host in a URL string.
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
