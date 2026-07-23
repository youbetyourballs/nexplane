//go:build !windows

package winupgrade

import "fmt"

func PreflightExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("windows_preflight_os_upgrade is only supported on Windows")
}

func VSSCreateShadowExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("windows_vss_create_shadow is only supported on Windows")
}

func StartOSUpgradeExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("windows_start_os_upgrade is only supported on Windows")
}

func VerifyOSUpgradeExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("windows_verify_os_upgrade is only supported on Windows")
}

func VSSRestoreExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("windows_vss_restore is only supported on Windows")
}
