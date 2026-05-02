package ebpf

import "fmt"

var validAttachTypes = map[string]bool{
	"kprobe": true, "tracepoint": true, "tc": true, "xdp": true, "cgroup": true, "lsm": true,
}
var validFrameworks = map[string]bool{
	"cilium": true, "falco": true, "tetragon": true, "bpfd": true,
}

func DeployEBPFPolicyExecute(params map[string]any) (map[string]any, error) {
	if prog, _ := params["program_path"].(string); prog == "" {
		return nil, fmt.Errorf("program_path is required")
	}
	if attachType, ok := params["attach_type"].(string); ok && attachType != "" && !validAttachTypes[attachType] {
		return nil, fmt.Errorf("invalid attach_type %q: must be one of kprobe, tracepoint, tc, xdp, cgroup, lsm", attachType)
	}
	return ebpfDeployOS(params)
}

func DeployEBPFPolicyRollback(params map[string]any) (map[string]any, error) {
	return ebpfDeployRollbackOS(params)
}

func ConfigureEBPFSecurityPolicyExecute(params map[string]any) (map[string]any, error) {
	framework, _ := params["framework"].(string)
	if !validFrameworks[framework] {
		return nil, fmt.Errorf("invalid framework %q: must be one of cilium, falco, tetragon, bpfd", framework)
	}
	if _, ok := params["policy"].(string); !ok {
		return nil, fmt.Errorf("policy content is required")
	}
	return ebpfPolicyOS(params)
}

func ConfigureEBPFSecurityPolicyRollback(params map[string]any) (map[string]any, error) {
	return ebpfPolicyRollbackOS(params)
}

func AuditEBPFPostureExecute(params map[string]any) (map[string]any, error) {
	return ebpfAuditOS(params)
}
