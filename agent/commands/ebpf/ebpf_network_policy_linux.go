//go:build linux

package ebpf

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

const netChain = "NEXPLANE_NET"

func ConfigureEbpfNetworkExecute(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if mode == "" {
		return nil, fmt.Errorf("mode is required (audit or enforce)")
	}
	if mode != "audit" && mode != "enforce" {
		return nil, fmt.Errorf("mode must be 'audit' or 'enforce'")
	}

	snapshotOut, _ := exec.Command("iptables-save").Output()
	snapshot := string(snapshotOut)

	// Create chain (ignore error if already exists)
	exec.Command("iptables", "-N", netChain).Run() //nolint:errcheck

	profile, _ := params["profile"].(map[string]any)
	flows, _ := profile["flows"].([]any)

	rulesAdded := 0
	for _, f := range flows {
		flow, ok := f.(map[string]any)
		if !ok {
			continue
		}
		proto, _ := flow["proto"].(string)
		port, _ := flow["local_port"].(string)
		if proto == "" || port == "" {
			continue
		}
		if mode == "audit" {
			exec.Command("iptables", "-A", netChain, "-p", proto, "--dport", port,
				"-j", "LOG", "--log-prefix", "nexplane-net: ").Run() //nolint:errcheck
		} else {
			exec.Command("iptables", "-A", netChain, "-p", proto, "--dport", port, "-j", "ACCEPT").Run() //nolint:errcheck
		}
		rulesAdded++
	}

	if mode == "enforce" {
		exec.Command("iptables", "-A", netChain, "-j", "DROP").Run() //nolint:errcheck
	}

	exec.Command("iptables", "-I", "OUTPUT", "1", "-j", netChain).Run() //nolint:errcheck

	return map[string]any{
		"mode":        mode,
		"rules_added": rulesAdded,
		"chain":       netChain,
		"snapshot":    snapshot,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ConfigureEbpfNetworkRollback(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	if snapshot == "" {
		// Best-effort cleanup without a snapshot
		exec.Command("iptables", "-D", "OUTPUT", "-j", netChain).Run() //nolint:errcheck
		exec.Command("iptables", "-F", netChain).Run()                   //nolint:errcheck
		exec.Command("iptables", "-X", netChain).Run()                   //nolint:errcheck
		return map[string]any{"rolled_back": true, "note": "no snapshot; chain flushed"}, nil
	}

	exec.Command("iptables", "-D", "OUTPUT", "-j", netChain).Run() //nolint:errcheck
	exec.Command("iptables", "-F", netChain).Run()                   //nolint:errcheck
	exec.Command("iptables", "-X", netChain).Run()                   //nolint:errcheck

	restore := exec.Command("iptables-restore")
	restore.Stdin = strings.NewReader(snapshot)
	if err := restore.Run(); err != nil {
		return nil, fmt.Errorf("iptables-restore failed: %w", err)
	}
	return map[string]any{"rolled_back": true}, nil
}
