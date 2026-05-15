//go:build linux

package linuxharden

import (
	"os"
	"os/exec"
	"strings"
	"time"
)

func openscapScanExecute(params map[string]any) (map[string]any, error) {
	profile, _ := params["profile"].(string)
	if profile == "" {
		profile = "xccdf_org.ssgproject.content_profile_cis"
	}

	// Install oscap if needed
	if _, err := exec.LookPath("oscap"); err != nil {
		exec.Command("sh", "-c",
			"yum install -y openscap-scanner scap-security-guide 2>/dev/null || apt-get install -y libopenscap8 ssg-base 2>/dev/null || true").Run()
	}

	// Find SCAP content file
	contentFile := ""
	for _, path := range []string{
		"/usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml",
		"/usr/share/xml/scap/ssg/content/ssg-rhel8-ds.xml",
		"/usr/share/xml/scap/ssg/content/ssg-amzn2-ds.xml",
		"/usr/share/scap-security-guide/ssg-ubuntu2204-ds.xml",
	} {
		if _, err := os.Stat(path); err == nil {
			contentFile = path
			break
		}
	}
	if contentFile == "" {
		return map[string]any{
			"action": "openscap_scan",
			"status": "skipped",
			"reason": "SCAP content file not found — install scap-security-guide",
		}, nil
	}

	out, _ := exec.Command("oscap", "xccdf", "eval",
		"--profile", profile,
		"--results", "/tmp/oscap-results.xml",
		contentFile).CombinedOutput()

	lines := strings.Split(string(out), "\n")
	var passes, fails []string
	for _, line := range lines {
		if strings.Contains(line, "pass") {
			passes = append(passes, strings.TrimSpace(line))
		} else if strings.Contains(line, "fail") {
			fails = append(fails, strings.TrimSpace(line))
		}
	}

	failLimit := len(fails)
	if failLimit > 30 {
		failLimit = 30
	}

	return map[string]any{
		"action":           "openscap_scan",
		"profile":          profile,
		"pass_count":       len(passes),
		"fail_count":       len(fails),
		"failing_controls": fails[:failLimit],
		"scanned_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func openscapScanRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "openscap_scan_rollback", "status": "read_only"}, nil
}
