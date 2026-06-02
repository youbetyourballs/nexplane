//go:build darwin

package linuxauth

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func LockLocalUserExecute(params map[string]any) (map[string]any, error) {
	username, _ := params["username"].(string)
	if username == "" {
		return nil, fmt.Errorf("username is required")
	}
	terminateSessions, _ := params["terminate_sessions"].(bool)

	// Snapshot current AuthenticationAuthority for rollback
	snapshotOut, _ := exec.Command("dscl", ".", "-read", "/Users/"+username, "AuthenticationAuthority").Output()
	snapshot := strings.TrimSpace(string(snapshotOut))

	// Disable via sysadminctl (preferred) or dscl fallback
	out, err := exec.Command("sysadminctl", "-disableUser", username).CombinedOutput()
	if err != nil {
		// Fallback: set AuthenticationAuthority directly
		out2, err2 := exec.Command("dscl", ".", "-append", "/Users/"+username,
			"AuthenticationAuthority", ";DisabledUser;").CombinedOutput()
		if err2 != nil {
			return nil, fmt.Errorf("lock user failed: sysadminctl: %s; dscl: %s", out, out2)
		}
	}

	killedSessions := 0
	if terminateSessions {
		exec.Command("pkill", "-KILL", "-u", username).Run() //nolint:errcheck
		killedSessions = 1
	}

	return map[string]any{
		"username":        username,
		"locked":          true,
		"sessions_killed": killedSessions,
		"snapshot":        snapshot,
		"locked_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func LockLocalUserRollback(params map[string]any) (map[string]any, error) {
	username, _ := params["username"].(string)
	if username == "" {
		return nil, fmt.Errorf("username is required for rollback")
	}

	out, err := exec.Command("sysadminctl", "-enableUser", username).CombinedOutput()
	if err != nil {
		// Fallback: remove DisabledUser marker via dscl
		out2, err2 := exec.Command("dscl", ".", "-delete", "/Users/"+username,
			"AuthenticationAuthority", ";DisabledUser;").CombinedOutput()
		if err2 != nil {
			return nil, fmt.Errorf("unlock failed: sysadminctl: %s; dscl: %s", out, out2)
		}
	}
	return map[string]any{"rolled_back": true, "username": username}, nil
}
