//go:build linux

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

	snapshotOut, _ := exec.Command("passwd", "-S", username).Output()
	snapshot := strings.TrimSpace(string(snapshotOut))

	if out, err := exec.Command("usermod", "-L", username).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("usermod -L failed: %s: %w", out, err)
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
	if out, err := exec.Command("usermod", "-U", username).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("usermod -U failed: %s: %w", out, err)
	}
	return map[string]any{"rolled_back": true, "username": username}, nil
}
