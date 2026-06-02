//go:build !linux

package ebpf

import "fmt"

func PromoteEbpfPolicyExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("promote_ebpf_policy requires Linux")
}

func PromoteEbpfPolicyRollback(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("promote_ebpf_policy requires Linux")
}
