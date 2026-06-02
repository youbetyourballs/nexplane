//go:build darwin

package configsyslog

import "fmt"

func executeOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("config_syslog not supported on macOS")
}

func rollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("config_syslog rollback not supported on macOS")
}
