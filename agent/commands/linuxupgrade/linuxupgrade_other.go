//go:build !linux

package linuxupgrade

import "fmt"

func upgradeExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("upgrade_linux_instance requires Linux")
}

func upgradeRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("upgrade_linux_instance requires Linux")
}
