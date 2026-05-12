//go:build windows

package listpkgs

import (
	"encoding/json"
	"os/exec"
	"strings"
	"time"
)

func executeOS(_ map[string]any) (map[string]any, error) {
	packages := collectWindowsPackages()
	return map[string]any{
		"action":    "list_installed_packages",
		"packages":  packages,
		"manager":   "windows",
		"total":     len(packages),
		"platform":  "windows",
		"listed_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func collectWindowsPackages() []map[string]any {
	// Query both 64-bit and 32-bit uninstall registry hives via PowerShell
	script := `
$paths = @(
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$results = foreach ($p in $paths) {
    if (Test-Path $p) {
        Get-ItemProperty $p -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -and $_.DisplayName -ne '' } |
            Select-Object DisplayName, DisplayVersion
    }
}
$results | Sort-Object DisplayName -Unique | ConvertTo-Json -Depth 1 -Compress
`
	out, err := exec.Command("powershell", "-NoProfile", "-NonInteractive", "-Command", script).Output()
	if err != nil {
		return []map[string]any{}
	}

	trimmed := strings.TrimSpace(string(out))
	if trimmed == "" || trimmed == "null" {
		return []map[string]any{}
	}

	// PowerShell may return a single object (not array) when there is only one result
	if !strings.HasPrefix(trimmed, "[") {
		trimmed = "[" + trimmed + "]"
	}

	var raw []struct {
		DisplayName    string `json:"DisplayName"`
		DisplayVersion string `json:"DisplayVersion"`
	}
	if err := json.Unmarshal([]byte(trimmed), &raw); err != nil {
		return []map[string]any{}
	}

	packages := make([]map[string]any, 0, len(raw))
	seen := map[string]bool{}
	for _, r := range raw {
		key := r.DisplayName + "|" + r.DisplayVersion
		if seen[key] {
			continue
		}
		seen[key] = true
		packages = append(packages, map[string]any{
			"name":    r.DisplayName,
			"version": r.DisplayVersion,
			"manager": "windows",
		})
		if len(packages) >= 1000 {
			break
		}
	}
	return packages
}
