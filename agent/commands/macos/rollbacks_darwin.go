//go:build darwin

package macos

import (
	"encoding/base64"
	"fmt"
	"os"
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

func RollbackProfilesInstall(params map[string]any) (map[string]any, error) {
	identifier, _ := params["identifier"].(string)
	if identifier == "" {
		return nil, fmt.Errorf("profiles_install rollback requires identifier in params")
	}
	out, err := run("profiles", "remove", "-identifier", identifier)
	if err != nil {
		return nil, fmt.Errorf("profiles remove %s (rollback): %s: %w", identifier, out, err)
	}
	return map[string]any{"rolled_back": true, "identifier": identifier, "output": out}, nil
}

func RollbackSantaRuleAdd(params map[string]any) (map[string]any, error) {
	identifierType, _ := params["identifier_type"].(string)
	identifier, _ := params["identifier"].(string)
	previousState, _ := params["previous_state"].(string)

	if identifierType == "" || identifier == "" {
		return nil, fmt.Errorf("santa_rule_add rollback requires identifier_type, identifier, and previous_state")
	}

	if previousState == "absent" || previousState == "" {
		out, err := run("santactl", "rule", "--remove", "--"+identifierType, identifier)
		if err != nil {
			return nil, fmt.Errorf("santactl rule --remove (rollback): %s: %w", out, err)
		}
		return map[string]any{"rolled_back": true, "action": "removed", "output": out}, nil
	}

	flag := "--denylist"
	if previousState == "allow" {
		flag = "--allowlist"
	}
	out, err := run("santactl", "rule", "--add", flag, "--"+identifierType, identifier)
	if err != nil {
		return nil, fmt.Errorf("santactl rule --add %s (rollback): %s: %w", flag, out, err)
	}
	return map[string]any{"rolled_back": true, "action": "restored", "previous_state": previousState, "output": out}, nil
}

func RollbackSantaRuleRemove(params map[string]any) (map[string]any, error) {
	identifierType, _ := params["identifier_type"].(string)
	identifier, _ := params["identifier"].(string)
	previousState, _ := params["previous_state"].(string)

	if identifierType == "" || identifier == "" || previousState == "" || previousState == "absent" {
		return nil, fmt.Errorf("santa_rule_remove rollback requires identifier_type, identifier, and previous_state")
	}

	flag := "--denylist"
	if previousState == "allow" {
		flag = "--allowlist"
	}
	out, err := run("santactl", "rule", "--add", flag, "--"+identifierType, identifier)
	if err != nil {
		return nil, fmt.Errorf("santactl rule --add (rollback): %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true, "output": out}, nil
}

func RollbackSantaModeSet(params map[string]any) (map[string]any, error) {
	previousMode, _ := params["previous_mode"].(string)
	if previousMode == "" {
		return nil, fmt.Errorf("santa_mode_set rollback requires previous_mode in params")
	}
	modeInt := "1"
	if previousMode == "lockdown" {
		modeInt = "2"
	}
	out, err := run("defaults", "write", "/Library/Preferences/com.google.santa", "ClientMode", "-int", modeInt)
	if err != nil {
		return nil, fmt.Errorf("santa_mode_set rollback defaults write: %s: %w", out, err)
	}
	run("santactl", "sync", "--clean")
	return map[string]any{"rolled_back": true, "mode": previousMode, "output": out}, nil
}

// RollbackSantaInstall unloads the Santa system extension, removes Santa files, and forgets the package receipt.
func RollbackSantaInstall(_ map[string]any) (map[string]any, error) {
	// Unload system extension
	run("systemextensionsctl", "uninstall", "-", "com.northpolesec.santa.daemon")
	// Remove Santa application bundle
	run("rm", "-rf", "/Applications/Santa.app")
	// Forget package receipt so installer state is clean
	out, err := run("pkgutil", "--forget", "com.northpolesec.santa")
	if err != nil {
		// Not fatal — pkg may not have been recorded
		_ = out
	}
	return map[string]any{"rolled_back": true}, nil
}

func RollbackProfilesRemove(params map[string]any) (map[string]any, error) {
	plistB64, _ := params["previous_plist_b64"].(string)
	if plistB64 == "" {
		return nil, fmt.Errorf("profiles_remove rollback requires previous_plist_b64 in params")
	}
	plistBytes, err := base64.StdEncoding.DecodeString(plistB64)
	if err != nil {
		return nil, fmt.Errorf("profiles_remove rollback: invalid base64: %w", err)
	}
	tmp, err := os.CreateTemp("", "nexplane-profile-rollback-*.mobileconfig")
	if err != nil {
		return nil, fmt.Errorf("profiles_remove rollback: temp file: %w", err)
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(plistBytes); err != nil {
		return nil, fmt.Errorf("profiles_remove rollback: write temp: %w", err)
	}
	tmp.Close()
	out, err := run("profiles", "install", "-path", tmp.Name())
	if err != nil {
		return nil, fmt.Errorf("profiles install (rollback): %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true, "output": out}, nil
}
