//go:build linux

package ebpf

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func PromoteEbpfPolicyExecute(params map[string]any) (map[string]any, error) {
	policyType, _ := params["policy_type"].(string)
	if policyType == "" {
		return nil, fmt.Errorf("policy_type is required (ebpf_network or ebpf_lsm)")
	}
	targetMode, _ := params["target_mode"].(string)
	if targetMode == "" {
		targetMode = "enforce"
	}

	snapshotOut, _ := exec.Command("iptables-save").Output()
	snapshot := string(snapshotOut)

	if policyType == "ebpf_network" {
		if targetMode == "enforce" {
			// Remove LOG rules from NEXPLANE_NET and add a DROP
			out, _ := exec.Command("iptables", "-L", netChain, "-n", "--line-numbers").Output()
			lines := strings.Split(string(out), "\n")
			// Iterate in reverse so line numbers remain valid
			for i := len(lines) - 1; i >= 0; i-- {
				if strings.Contains(lines[i], "LOG") {
					fields := strings.Fields(lines[i])
					if len(fields) > 0 {
						exec.Command("iptables", "-D", netChain, fields[0]).Run() //nolint:errcheck
					}
				}
			}
			exec.Command("iptables", "-A", netChain, "-j", "DROP").Run() //nolint:errcheck
		} else {
			// Flip back to audit: remove DROP, add LOG
			exec.Command("iptables", "-D", netChain, "-j", "DROP").Run() //nolint:errcheck
			exec.Command("iptables", "-A", netChain, "-j", "LOG",
				"--log-prefix", "nexplane-net: ").Run() //nolint:errcheck
		}
	}
	// ebpf_lsm: auditd always logs; mode is tracked in the backend profile only

	return map[string]any{
		"policy_type": policyType,
		"target_mode": targetMode,
		"snapshot":    snapshot,
		"promoted_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func PromoteEbpfPolicyRollback(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	if snapshot == "" {
		return map[string]any{"rolled_back": false, "reason": "no snapshot"}, nil
	}
	restore := exec.Command("iptables-restore")
	restore.Stdin = strings.NewReader(snapshot)
	if err := restore.Run(); err != nil {
		return nil, fmt.Errorf("iptables-restore failed: %w", err)
	}
	return map[string]any{"rolled_back": true}, nil
}
