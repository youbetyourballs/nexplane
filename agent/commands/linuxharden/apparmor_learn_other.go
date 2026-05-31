//go:build !linux

package linuxharden

import "fmt"

func apparmorLearnExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apparmor_learn is only supported on Linux")
}

func apparmorLearnRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"status": "no_state_to_revert"}, nil
}
