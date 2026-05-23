//go:build !darwin

package macos

import "fmt"

// RollbackFilevaultEnable is a stub; FileVault rollback is only supported on macOS.
func RollbackFilevaultEnable(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("filevault_enable rollback is only supported on macOS")
}

// RollbackGatekeeperEnable is a stub; Gatekeeper rollback is only supported on macOS.
func RollbackGatekeeperEnable(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("gatekeeper_enable rollback is only supported on macOS")
}

// RollbackGatekeeperDisable is a stub; Gatekeeper rollback is only supported on macOS.
func RollbackGatekeeperDisable(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("gatekeeper_disable rollback is only supported on macOS")
}

// RollbackDefaultsWrite is a stub; defaults_write rollback is only supported on macOS.
func RollbackDefaultsWrite(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("defaults_write rollback is only supported on macOS")
}

func RollbackProfilesInstall(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("profiles_install rollback is only supported on macOS")
}

func RollbackProfilesRemove(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("profiles_remove rollback is only supported on macOS")
}
