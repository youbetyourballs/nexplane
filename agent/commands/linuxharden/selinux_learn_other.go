//go:build !linux

package linuxharden

import "fmt"

func selinuxLearnExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("selinux_learn is only supported on Linux")
}

func selinuxLearnRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"status": "no_state_to_revert"}, nil
}
