//go:build !linux

package ebpf

import "fmt"

func ebpfDeployOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_ebpf_policy requires Linux")
}
func ebpfDeployRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_ebpf_policy requires Linux")
}
func ebpfPolicyOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ebpf_security_policy requires Linux")
}
func ebpfPolicyRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ebpf_security_policy requires Linux")
}
func ebpfAuditOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_ebpf_posture requires Linux")
}
