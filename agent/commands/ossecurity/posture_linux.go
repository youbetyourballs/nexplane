//go:build linux

package ossecurity

import (
	"os/exec"
	"strings"
	"time"
)

func auditPostureOS(_ map[string]any) (map[string]any, error) {
	result := map[string]any{"audited_at": time.Now().UTC().Format(time.RFC3339)}

	selinuxOut, _ := exec.Command("sestatus").Output()
	result["selinux"] = map[string]any{
		"available": len(selinuxOut) > 0,
		"status":    strings.TrimSpace(string(selinuxOut)),
	}

	aaOut, _ := exec.Command("aa-status", "--json").Output()
	result["apparmor"] = map[string]any{
		"available": len(aaOut) > 0,
		"status":    strings.TrimSpace(string(aaOut)),
	}

	auditOut, _ := exec.Command("auditctl", "-s").Output()
	result["auditd"] = map[string]any{
		"available": len(auditOut) > 0,
		"status":    strings.TrimSpace(string(auditOut)),
	}

	denialsOut, _ := exec.Command("ausearch", "-m", "avc", "-ts", "recent").Output()
	result["recent_denials"] = strings.TrimSpace(string(denialsOut))

	return result, nil
}
