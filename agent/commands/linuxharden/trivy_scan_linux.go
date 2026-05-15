//go:build linux

package linuxharden

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"time"
)

func trivyScanExecute(params map[string]any) (map[string]any, error) {
	scanType, _ := params["scan_type"].(string)
	if scanType == "" {
		scanType = "fs"
	}
	target, _ := params["target"].(string)
	if target == "" {
		target = "/"
	}
	severity, _ := params["severity"].(string)
	if severity == "" {
		severity = "HIGH,CRITICAL"
	}

	// Install Trivy if not present
	if _, err := exec.LookPath("trivy"); err != nil {
		install := exec.Command("sh", "-c",
			`curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin 2>/dev/null || true`)
		install.Run()
	}

	out, err := exec.Command("trivy", scanType, "--format", "json",
		"--severity", severity, "--quiet", target).Output()
	if err != nil && len(out) == 0 {
		return nil, fmt.Errorf("trivy scan failed: %w", err)
	}

	var result map[string]any
	if jsonErr := json.Unmarshal(out, &result); jsonErr != nil {
		raw := string(out)
		if len(raw) > 2000 {
			raw = raw[:2000]
		}
		return map[string]any{
			"action":     "trivy_scan",
			"scan_type":  scanType,
			"target":     target,
			"raw_output": raw,
			"error":      jsonErr.Error(),
		}, nil
	}

	// Extract findings summary
	var findings []map[string]any
	if results, ok := result["Results"].([]any); ok {
		for _, r := range results {
			if rm, ok := r.(map[string]any); ok {
				if vulns, ok := rm["Vulnerabilities"].([]any); ok {
					for _, v := range vulns {
						if vm, ok := v.(map[string]any); ok {
							findings = append(findings, map[string]any{
								"cve_id":   vm["VulnerabilityID"],
								"package":  vm["PkgName"],
								"version":  vm["InstalledVersion"],
								"fixed_in": vm["FixedVersion"],
								"severity": vm["Severity"],
								"title":    vm["Title"],
							})
						}
					}
				}
			}
		}
	}

	return map[string]any{
		"action":          "trivy_scan",
		"scan_type":       scanType,
		"target":          target,
		"severity_filter": severity,
		"finding_count":   len(findings),
		"findings":        findings,
		"scanned_at":      time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func trivyScanRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "trivy_scan_rollback", "status": "no_state_to_revert"}, nil
}
