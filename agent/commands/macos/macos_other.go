//go:build !darwin

package macos

import "fmt"

func filevaultStatus(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("filevault_status is only supported on macOS")
}

func filevaultEnable(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("filevault_enable is only supported on macOS")
}

func gatekeeperStatus(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("gatekeeper_status is only supported on macOS")
}

func gatekeeperEnable(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("gatekeeper_enable is only supported on macOS")
}

func gatekeeperDisable(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("gatekeeper_disable is only supported on macOS")
}

func softwareupdateList(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("softwareupdate_list is only supported on macOS")
}

func softwareupdateInstall(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("softwareupdate_install is only supported on macOS")
}

func profilesList(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("profiles_list is only supported on macOS")
}

func launchctlList(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("launchctl_list is only supported on macOS")
}

func macosSysinfo(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("macos_sysinfo is only supported on macOS")
}
