//go:build linux

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func ebpfDeployOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("deploy_ebpf_policy requires root privileges")
	}
	if _, err := exec.LookPath("bpftool"); err != nil {
		return nil, fmt.Errorf("bpftool not found (required for eBPF program management)")
	}

	programPath, _ := params["program_path"].(string)
	attachType, _ := params["attach_type"].(string)
	attachTarget, _ := params["attach_target"].(string)

	out, err := exec.Command("bpftool", "prog", "load", programPath, "/sys/fs/bpf/nexplane_prog").CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("bpftool prog load: %s: %w", out, err)
	}

	attachResult := ""
	if attachType != "" && attachTarget != "" {
		idOut, _ := exec.Command("bpftool", "prog", "show", "pinned", "/sys/fs/bpf/nexplane_prog", "--json").Output()
		progID := strings.TrimSpace(string(idOut))
		attachOut, err := exec.Command("bpftool", "net", "attach", attachType, "id", progID, "dev", attachTarget).CombinedOutput()
		if err != nil {
			attachResult = fmt.Sprintf("attach failed: %s", attachOut)
		} else {
			attachResult = "attached"
		}
	}

	return map[string]any{
		"program_path":  programPath,
		"attach_type":   attachType,
		"attach_target": attachTarget,
		"attach_result": attachResult,
		"pin_path":      "/sys/fs/bpf/nexplane_prog",
		"snapshot":      map[string]any{"pin_path": "/sys/fs/bpf/nexplane_prog"},
		"applied_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ebpfDeployRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	pinPath, _ := snapshot["pin_path"].(string)
	if pinPath != "" {
		exec.Command("rm", "-f", pinPath).Run() //nolint:errcheck
	}
	return map[string]any{"rolled_back": true}, nil
}

func ebpfPolicyOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("configure_ebpf_security_policy requires root privileges")
	}
	framework, _ := params["framework"].(string)
	policy, _ := params["policy"].(string)

	policyPath := fmt.Sprintf("/etc/nexplane/ebpf/%s-policy.yaml", framework)
	if err := os.MkdirAll("/etc/nexplane/ebpf", 0755); err != nil {
		return nil, fmt.Errorf("creating ebpf policy dir: %w", err)
	}
	snapshot := ""
	if data, err := os.ReadFile(policyPath); err == nil {
		snapshot = string(data)
	}
	if err := os.WriteFile(policyPath, []byte(policy), 0644); err != nil {
		return nil, fmt.Errorf("writing policy: %w", err)
	}

	switch framework {
	case "falco":
		exec.Command("falco", "-r", policyPath, "--validate").Run() //nolint:errcheck
	case "cilium":
		exec.Command("cilium", "policy", "import", policyPath).Run() //nolint:errcheck
	}

	return map[string]any{
		"framework":   framework,
		"policy_path": policyPath,
		"snapshot":    snapshot,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func ebpfPolicyRollbackOS(params map[string]any) (map[string]any, error) {
	framework, _ := params["framework"].(string)
	snapshot, _ := params["snapshot"].(string)
	policyPath := fmt.Sprintf("/etc/nexplane/ebpf/%s-policy.yaml", framework)
	if snapshot == "" {
		os.Remove(policyPath)
	} else {
		os.WriteFile(policyPath, []byte(snapshot), 0644) //nolint:errcheck
	}
	return map[string]any{"rolled_back": true}, nil
}

func ebpfAuditOS(_ map[string]any) (map[string]any, error) {
	result := map[string]any{"audited_at": time.Now().UTC().Format(time.RFC3339)}

	out, err := exec.Command("bpftool", "prog", "list", "--json").Output()
	if err != nil {
		result["bpftool_available"] = false
		result["programs"] = []any{}
		return result, nil
	}
	result["bpftool_available"] = true
	result["programs_raw"] = strings.TrimSpace(string(out))

	netOut, _ := exec.Command("bpftool", "net", "list", "--json").Output()
	result["network_attachments_raw"] = strings.TrimSpace(string(netOut))

	unexpectedOut, _ := exec.Command("find", "/sys/fs/bpf", "-not", "-path", "*/nexplane*", "-type", "f").Output()
	var unexpected []string
	for _, line := range strings.Split(strings.TrimSpace(string(unexpectedOut)), "\n") {
		if line != "" {
			unexpected = append(unexpected, line)
		}
	}
	result["unexpected_programs"] = unexpected
	tags := []string{}
	if len(unexpected) > 0 {
		tags = append(tags, "unexpected-ebpf-programs")
	}
	result["tags"] = tags

	return result, nil
}
