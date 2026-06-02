//go:build darwin

package linuxpatch

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// ApplyMacPatchesExecute installs macOS software updates via softwareupdate.
func ApplyMacPatchesExecute(params map[string]any) (map[string]any, error) {
	all, _ := params["all"].(bool)

	var patchNames []string
	if raw, ok := params["patch_names"]; ok {
		switch v := raw.(type) {
		case []string:
			patchNames = v
		case []any:
			for _, item := range v {
				if s, ok := item.(string); ok {
					patchNames = append(patchNames, s)
				}
			}
		}
	}

	if !all && len(patchNames) == 0 {
		return nil, fmt.Errorf("either 'all' or 'patch_names' is required")
	}

	// Snapshot before applying.
	snapshotOut, _ := exec.Command("softwareupdate", "-l").CombinedOutput()
	snapshot := string(snapshotOut)

	var out []byte
	var applied []string

	if all {
		out, _ = exec.Command("softwareupdate", "--install", "--all").CombinedOutput()
		applied = []string{"--all"}
	} else {
		args := append([]string{"--install"}, patchNames...)
		out, _ = exec.Command("softwareupdate", args...).CombinedOutput()
		applied = patchNames
	}

	raw := string(out)
	rebootRequired := strings.Contains(raw, "restart") || strings.Contains(raw, "reboot")

	return map[string]any{
		"applied":          applied,
		"snapshot":         snapshot,
		"raw_output":       raw,
		"applied_at":       time.Now().UTC().Format(time.RFC3339),
		"reboot_required":  rebootRequired,
	}, nil
}

// ApplyMacPatchesRollback notes that macOS patches are not automatically reversible.
func ApplyMacPatchesRollback(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	return map[string]any{
		"rolled_back": false,
		"note":        "macOS patches are not automatically reversible; manual restore from Time Machine backup required",
		"snapshot":    snapshot,
	}, nil
}
