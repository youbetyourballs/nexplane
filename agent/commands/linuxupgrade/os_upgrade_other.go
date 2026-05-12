//go:build !linux

package linuxupgrade

import "fmt"

func osUpgradePreflightOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("preflight_os_upgrade requires Linux")
}

func osUpgradeVerifyOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("verify_os_upgrade requires Linux")
}
