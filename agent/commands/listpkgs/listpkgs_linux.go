//go:build linux

package listpkgs

import (
	"os/exec"
	"time"
)

func executeOS(_ map[string]any) (map[string]any, error) {
	packages, manager := collectPackages()
	return map[string]any{
		"action":    "list_installed_packages",
		"packages":  packages,
		"manager":   manager,
		"total":     len(packages),
		"platform":  "linux",
		"listed_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func collectPackages() ([]map[string]any, string) {
	// Try dpkg-query (Debian / Ubuntu)
	if out, err := exec.Command("dpkg-query", "-W", "-f=${Package}\t${Version}\n").Output(); err == nil {
		pkgs := parseTSV(string(out), "dpkg")
		if len(pkgs) > 0 {
			return cap1000(pkgs), "dpkg"
		}
	}

	// Try rpm (RHEL / Amazon Linux / CentOS)
	if out, err := exec.Command("rpm", "-qa", "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\n").Output(); err == nil {
		pkgs := parseTSV(string(out), "rpm")
		if len(pkgs) > 0 {
			return cap1000(pkgs), "rpm"
		}
	}

	// Try apk (Alpine)
	if out, err := exec.Command("apk", "list", "--installed").Output(); err == nil {
		pkgs := parseApk(string(out))
		if len(pkgs) > 0 {
			return cap1000(pkgs), "apk"
		}
	}

	// Try snap
	if out, err := exec.Command("snap", "list").Output(); err == nil {
		pkgs := parseSnap(string(out))
		if len(pkgs) > 0 {
			return cap1000(pkgs), "snap"
		}
	}

	return []map[string]any{}, "unknown"
}
