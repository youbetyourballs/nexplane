package listpkgs

import (
	"strings"
	"unicode"
)

const maxPackages = 1000

// parseTSV parses tab-separated name\tversion lines (dpkg-query / rpm -qa output).
func parseTSV(output, manager string) []map[string]any {
	var pkgs []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		parts := strings.SplitN(line, "\t", 2)
		if len(parts) == 2 && parts[0] != "" {
			pkgs = append(pkgs, map[string]any{
				"name":    parts[0],
				"version": parts[1],
				"manager": manager,
			})
		}
	}
	return pkgs
}

// parseApk parses `apk list --installed` output.
// Format: <name>-<version> <arch> {<origin>} (<license>) [installed]
// The version boundary is the first '-' that is immediately followed by a digit.
func parseApk(output string) []map[string]any {
	var pkgs []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(output), "\n") {
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		nameVer := fields[0]
		idx := -1
		for i := 1; i < len(nameVer); i++ {
			if nameVer[i-1] == '-' && unicode.IsDigit(rune(nameVer[i])) {
				idx = i - 1
				break
			}
		}
		if idx <= 0 {
			continue
		}
		pkgs = append(pkgs, map[string]any{
			"name":    nameVer[:idx],
			"version": nameVer[idx+1:],
			"manager": "apk",
		})
	}
	return pkgs
}

// parseSnap parses `snap list` output (first line is a header).
func parseSnap(output string) []map[string]any {
	var pkgs []map[string]any
	lines := strings.Split(strings.TrimSpace(output), "\n")
	if len(lines) <= 1 {
		return pkgs
	}
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		pkgs = append(pkgs, map[string]any{
			"name":    fields[0],
			"version": fields[1],
			"manager": "snap",
		})
	}
	return pkgs
}

func cap1000(pkgs []map[string]any) []map[string]any {
	if len(pkgs) > maxPackages {
		return pkgs[:maxPackages]
	}
	return pkgs
}
