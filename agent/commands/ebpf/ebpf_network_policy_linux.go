//go:build linux

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const (
	netChain       = "NEXPLANE_NET"
	netSnapshotDir = "/etc/nexplane/ebpf"
	netModeFile    = "/etc/nexplane/ebpf/ebpf_network-mode"
)

func ConfigureEbpfNetworkExecute(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if mode == "" {
		return nil, fmt.Errorf("mode is required (audit or enforce)")
	}
	if mode != "audit" && mode != "enforce" {
		return nil, fmt.Errorf("mode must be 'audit' or 'enforce'")
	}

	os.MkdirAll(netSnapshotDir, 0755) //nolint:errcheck

	// Snapshot current iptables state to a file
	snapshotPath := fmt.Sprintf("%s/net-snapshot-%d.txt", netSnapshotDir, time.Now().UnixNano())
	snapshotOut, _ := exec.Command("iptables-save").Output()
	os.WriteFile(snapshotPath, snapshotOut, 0600) //nolint:errcheck

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
		proto, _ := flow["protocol"].(string)
		if proto == "" {
			proto, _ = flow["proto"].(string)
		}
		var port string
		switch v := flow["dst_port"].(type) {
		case float64:
			port = fmt.Sprintf("%d", int(v))
		case int:
			port = fmt.Sprintf("%d", v)
		case string:
			port = v
		}
		if proto == "" || port == "" || port == "0" {
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

	// Record current mode for promote command
	os.WriteFile(netModeFile, []byte(mode), 0644) //nolint:errcheck

	return map[string]any{
		"mode":        mode,
		"rules_added": rulesAdded,
		"chain":       netChain,
		"snapshot_id": snapshotPath,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ConfigureEbpfNetworkRollback(params map[string]any) (map[string]any, error) {
	snapshotID, _ := params["snapshot_id"].(string)

	exec.Command("iptables", "-D", "OUTPUT", "-j", netChain).Run() //nolint:errcheck
	exec.Command("iptables", "-F", netChain).Run()                   //nolint:errcheck
	exec.Command("iptables", "-X", netChain).Run()                   //nolint:errcheck

	if snapshotID != "" {
		data, err := os.ReadFile(snapshotID)
		if err == nil {
			restore := exec.Command("iptables-restore")
			restore.Stdin = strings.NewReader(string(data))
			restore.Run() //nolint:errcheck
		}
	}

	os.WriteFile(netModeFile, []byte(""), 0644) //nolint:errcheck

	return map[string]any{"rolled_back": true}, nil
}
