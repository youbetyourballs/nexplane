//go:build darwin

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const (
	pfAnchorPath = "/etc/pf.anchors/nexplane_net"
)

func modeFileFor(policyType string) string {
	return fmt.Sprintf("/etc/nexplane/ebpf/%s-mode", policyType)
}

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
	priorMode := "audit"
	if data, err := os.ReadFile(modeFile); err == nil {
		if m := strings.TrimSpace(string(data)); m != "" {
			priorMode = m
		}
	}

	// Snapshot pf state
	snapshotOut, _ := exec.Command("pfctl", "-s", "all").CombinedOutput()
	snapshot := string(snapshotOut)

	if policyType == "ebpf_network" {
		var anchorContent string
		if targetMode == "enforce" {
			anchorContent = "# Nexplane network policy anchor - enforce mode\nblock log all\n"
		} else {
			anchorContent = "# Nexplane network policy anchor - audit mode\npass log all\n"
		}
		if err := os.WriteFile(pfAnchorPath, []byte(anchorContent), 0644); err != nil {
			return nil, fmt.Errorf("writing pf anchor: %w", err)
		}
		exec.Command("pfctl", "-f", "/etc/pf.conf").CombinedOutput() //nolint:errcheck
	}
	// ebpf_lsm: BSM always logs; mode is tracked in mode file only

	// Write mode file
	os.MkdirAll("/etc/nexplane/ebpf", 0755)              //nolint:errcheck
	os.WriteFile(modeFile, []byte(targetMode), 0644)     //nolint:errcheck

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

	if policyType == "ebpf_network" {
		var anchorContent string
		if priorMode == "enforce" {
			anchorContent = "# Nexplane network policy anchor - enforce mode\nblock log all\n"
		} else {
			anchorContent = "# Nexplane network policy anchor - audit mode\npass log all\n"
		}
		os.WriteFile(pfAnchorPath, []byte(anchorContent), 0644)          //nolint:errcheck
		exec.Command("pfctl", "-f", "/etc/pf.conf").CombinedOutput()     //nolint:errcheck
	}

	// Restore mode file
	if policyType != "" && priorMode != "" {
		modeFile := modeFileFor(policyType)
		os.WriteFile(modeFile, []byte(priorMode), 0644) //nolint:errcheck
	}

	return map[string]any{"rolled_back": true, "restored_mode": priorMode}, nil
}
