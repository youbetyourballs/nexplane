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

// RollbackDefaultsWrite restores a defaults key to its previous value, or deletes it if it didn't exist before.
func RollbackDefaultsWrite(params map[string]any) (map[string]any, error) {
	domain, _ := params["domain"].(string)
	key, _ := params["key"].(string)
	if domain == "" || key == "" {
		return nil, fmt.Errorf("defaults_write rollback requires domain and key in params")
	}

	previousValue, hasPrev := params["previous_value"]
	if !hasPrev || previousValue == nil {
		// Key didn't exist before — delete it.
		out, err := run("defaults", "delete", domain, key)
		if err != nil {
			return nil, fmt.Errorf("defaults delete %s %s (rollback): %s: %w", domain, key, out, err)
		}
		return map[string]any{"rolled_back": true, "action": "deleted", "output": out}, nil
	}

	prev := fmt.Sprintf("%v", previousValue)
	out, err := run("defaults", "write", domain, key, prev)
	if err != nil {
		return nil, fmt.Errorf("defaults write %s %s (rollback): %s: %w", domain, key, out, err)
	}
	return map[string]any{"rolled_back": true, "action": "restored", "output": out}, nil
}
