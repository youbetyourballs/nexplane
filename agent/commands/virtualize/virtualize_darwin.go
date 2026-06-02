//go:build darwin

package virtualize

import "fmt"

func executeOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("virtualize not supported on macOS")
}

func rollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("virtualize rollback not supported on macOS")
}
