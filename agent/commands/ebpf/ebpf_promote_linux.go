//go:build linux

package ebpf

import (
	"fmt"
	"os"
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

	// Read prior mode
	modeFile := modeFileFor(policyType)
	priorMode := "audit" // default assumption
	if data, err := os.ReadFile(modeFile); err == nil {
		if m := strings.TrimSpace(string(data)); m != "" {
			priorMode = m
		}
	}

	snapshotOut, _ := exec.Command("iptables-save").Output()
	snapshot := string(snapshotOut)

	if policyType == "ebpf_network" {
		if targetMode == "enforce" {
			// Replace LOG rules in NEXPLANE_NET with DROP
			out, _ := exec.Command("iptables", "-L", netChain, "-n", "--line-numbers").Output()
			lines := strings.Split(string(out), "\n")
			// Iterate in reverse to keep line numbers valid
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
			exec.Command("iptables", "-D", netChain, "-j", "DROP").Run() //nolint:errcheck
			exec.Command("iptables", "-A", netChain, "-j", "LOG",
				"--log-prefix", "nexplane-net: ").Run() //nolint:errcheck
		}
	}
	// ebpf_lsm: auditd always logs; mode is tracked in the backend profile only

	os.WriteFile(modeFile, []byte(targetMode), 0644) //nolint:errcheck

	return map[string]any{
		"policy_type": policyType,
		"prior_mode":  priorMode,
		"target_mode": targetMode,
		"snapshot":    snapshot,
		"promoted_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func PromoteEbpfPolicyRollback(params map[string]any) (map[string]any, error) {
	policyType, _ := params["policy_type"].(string)
	priorMode, _ := params["prior_mode"].(string)
	snapshot, _ := params["snapshot"].(string)

	if snapshot != "" && policyType == "ebpf_network" {
		restore := exec.Command("iptables-restore")
		restore.Stdin = strings.NewReader(snapshot)
		restore.Run() //nolint:errcheck
	}

	// Restore prior mode in mode file
	if policyType != "" && priorMode != "" {
		modeFile := modeFileFor(policyType)
		os.WriteFile(modeFile, []byte(priorMode), 0644) //nolint:errcheck
	}

	return map[string]any{"rolled_back": true, "restored_mode": priorMode}, nil
}

func modeFileFor(policyType string) string {
	return fmt.Sprintf("/etc/nexplane/ebpf/%s-mode", policyType)
}
