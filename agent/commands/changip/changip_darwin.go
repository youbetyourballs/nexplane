//go:build darwin

package changip

import "fmt"

func executeOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("change_ip not supported on macOS")
}

func rollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("change_ip rollback not supported on macOS")
}
