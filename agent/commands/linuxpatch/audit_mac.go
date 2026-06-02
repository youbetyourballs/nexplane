//go:build darwin

package linuxpatch

import (
	"os/exec"
	"strings"
	"time"
)

// AuditPatchesMacExecute lists available macOS software updates without applying them.
func AuditPatchesMacExecute(_ map[string]any) (map[string]any, error) {
	out, _ := exec.Command("softwareupdate", "-l").CombinedOutput()
	raw := string(out)

	if strings.Contains(raw, "No new software available") {
		return map[string]any{
			"updates_available": []string{},
			"update_count":      0,
			"raw_output":        raw,
			"audited_at":        time.Now().UTC().Format(time.RFC3339),
		}, nil
	}

	var updates []string
	for _, line := range strings.Split(raw, "\n") {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "* ") {
			updates = append(updates, strings.TrimPrefix(trimmed, "* "))
		} else if strings.HasPrefix(trimmed, "- ") {
			updates = append(updates, strings.TrimPrefix(trimmed, "- "))
		}
	}
	if updates == nil {
		updates = []string{}
	}

	return map[string]any{
		"updates_available": updates,
		"update_count":      len(updates),
		"raw_output":        raw,
		"audited_at":        time.Now().UTC().Format(time.RFC3339),
	}, nil
}

// AuditPatchesMacRollback is a no-op because audit makes no changes.
func AuditPatchesMacRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"no_op": true}, nil
}
