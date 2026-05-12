//go:build !linux && !windows

package listpkgs

import "time"

func executeOS(_ map[string]any) (map[string]any, error) {
	return map[string]any{
		"action":    "list_installed_packages",
		"packages":  []map[string]any{},
		"manager":   "unknown",
		"total":     0,
		"platform":  "other",
		"note":      "Package listing is not implemented for this operating system.",
		"listed_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
