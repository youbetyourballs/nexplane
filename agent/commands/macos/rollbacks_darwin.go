//go:build darwin

package macos

import (
	"fmt"
)

// RollbackFilevaultEnable disables FileVault to undo a filevault_enable action.
func RollbackFilevaultEnable(_ map[string]any) (map[string]any, error) {
	out, err := run("fdesetup", "disable")
	if err != nil {
		return nil, fmt.Errorf("fdesetup disable (rollback): %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true, "output": out}, nil
}

// RollbackGatekeeperEnable disables Gatekeeper to undo a gatekeeper_enable action.
func RollbackGatekeeperEnable(_ map[string]any) (map[string]any, error) {
	out, err := run("spctl", "--master-disable")
	if err != nil {
		return nil, fmt.Errorf("spctl --master-disable (rollback): %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true, "output": out}, nil
}

// RollbackGatekeeperDisable re-enables Gatekeeper to undo a gatekeeper_disable action.
func RollbackGatekeeperDisable(_ map[string]any) (map[string]any, error) {
	out, err := run("spctl", "--master-enable")
	if err != nil {
		return nil, fmt.Errorf("spctl --master-enable (rollback): %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true, "output": out}, nil
}
