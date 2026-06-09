//go:build darwin

package compliance

import (
	"fmt"
	"os/exec"
	"strings"
)

var execCommandCompliance = exec.Command

func auditCISComplianceOS(level int, osFamily string) (map[string]any, error) {
	var controls []ControlResult

	controls = append(controls, checkMacSIP()...)
	controls = append(controls, checkMacGatekeeper()...)
	controls = append(controls, checkMacALF()...)
	controls = append(controls, checkMacSoftwareUpdate()...)
	controls = append(controls, checkMacNTP()...)
	controls = append(controls, checkMacSSHConfig()...)

	if level >= 2 {
		controls = append(controls, checkMacAuditdActive()...)
		controls = append(controls, checkMacSantaInstalled()...)
		controls = append(controls, checkMacScreenLock()...)
	}

	score := CalculateScore(controls)
	return map[string]any{
		"level":        level,
		"os_family":    "darwin",
		"score":        score,
		"controls":     controls,
		"collected_at": collectedNow(),
	}, nil
}

func collectEvidenceOS(_ map[string]any) (map[string]any, error) {
	spOut, _ := execCommandCompliance("system_profiler", "SPSoftwareDataType", "SPSecurityDataType").Output()
	csrOut, _ := execCommandCompliance("csrutil", "status").Output()
	spctlOut, _ := execCommandCompliance("spctl", "--status").Output()
	return map[string]any{
		"system_profiler": string(spOut),
		"csrutil_status":  strings.TrimSpace(string(csrOut)),
		"spctl_status":    strings.TrimSpace(string(spctlOut)),
		"collected_at":    collectedNow(),
	}, nil
}

func statusOf(passed bool) string {
	if passed {
		return "pass"
	}
	return "fail"
}

func checkMacSIP() []ControlResult {
	out, _ := execCommandCompliance("csrutil", "status").Output()
	passed := strings.Contains(string(out), "enabled")
	return []ControlResult{{
		ID:       "1.1",
		Title:    "Ensure SIP is enabled",
		Section:  "1",
		Status:   statusOf(passed),
		Expected: "enabled",
		Actual:   strings.TrimSpace(string(out)),
	}}
}

func checkMacGatekeeper() []ControlResult {
	out, _ := execCommandCompliance("spctl", "--status").Output()
	passed := strings.Contains(string(out), "assessments enabled")
	return []ControlResult{{
		ID:       "1.2",
		Title:    "Ensure Gatekeeper is enabled",
		Section:  "1",
		Status:   statusOf(passed),
		Expected: "assessments enabled",
		Actual:   strings.TrimSpace(string(out)),
	}}
}

func checkMacALF() []ControlResult {
	out, _ := execCommandCompliance("/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate").Output()
	passed := strings.Contains(string(out), "enabled") || strings.Contains(string(out), "State = 1")
	return []ControlResult{{
		ID:       "2.1",
		Title:    "Ensure Application Layer Firewall is enabled",
		Section:  "2",
		Status:   statusOf(passed),
		Expected: "enabled",
		Actual:   strings.TrimSpace(string(out)),
	}}
}

func checkMacSoftwareUpdate() []ControlResult {
	out, _ := execCommandCompliance("sw_vers", "-productVersion").Output()
	version := strings.TrimSpace(string(out))
	passed := version != "" && version >= "13"
	return []ControlResult{{
		ID:       "1.3",
		Title:    "Ensure macOS >= Ventura 13.0",
		Section:  "1",
		Status:   statusOf(passed),
		Expected: ">= 13.0",
		Actual:   fmt.Sprintf("macOS %s", version),
	}}
}

func checkMacNTP() []ControlResult {
	out, _ := execCommandCompliance("systemsetup", "-getusingnetworktime").Output()
	passed := strings.Contains(strings.ToLower(string(out)), "on")
	return []ControlResult{{
		ID:       "2.2",
		Title:    "Ensure NTP is enabled",
		Section:  "2",
		Status:   statusOf(passed),
		Expected: "on",
		Actual:   strings.TrimSpace(string(out)),
	}}
}

func checkMacSSHConfig() []ControlResult {
	out, _ := execCommandCompliance("sshd", "-T").Output()
	permitRoot, passwordAuth := "unknown", "unknown"
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.ToLower(strings.TrimSpace(line))
		if strings.HasPrefix(line, "permitrootlogin ") {
			permitRoot = strings.TrimPrefix(line, "permitrootlogin ")
		}
		if strings.HasPrefix(line, "passwordauthentication ") {
			passwordAuth = strings.TrimPrefix(line, "passwordauthentication ")
		}
	}
	return []ControlResult{
		{
			ID:       "3.1",
			Title:    "Ensure SSH PermitRootLogin is no",
			Section:  "3",
			Status:   statusOf(permitRoot == "no"),
			Expected: "no",
			Actual:   "PermitRootLogin " + permitRoot,
		},
		{
			ID:       "3.2",
			Title:    "Ensure SSH PasswordAuthentication is no",
			Section:  "3",
			Status:   statusOf(passwordAuth == "no"),
			Expected: "no",
			Actual:   "PasswordAuthentication " + passwordAuth,
		},
	}
}

func checkMacAuditdActive() []ControlResult {
	out, _ := execCommandCompliance("audit", "-l").Output()
	passed := len(out) > 0 && !strings.Contains(string(out), "not running")
	return []ControlResult{{
		ID:       "4.1",
		Title:    "Ensure BSM audit daemon is active",
		Section:  "4",
		Status:   statusOf(passed),
		Expected: "running",
		Actual:   strings.TrimSpace(string(out)),
	}}
}

func checkMacSantaInstalled() []ControlResult {
	out, err := execCommandCompliance("santactl", "version").Output()
	passed := err == nil && len(out) > 0
	actual := "not installed"
	if passed {
		actual = strings.TrimSpace(string(out))
	}
	return []ControlResult{{
		ID:       "4.2",
		Title:    "Ensure Santa is installed",
		Section:  "4",
		Status:   statusOf(passed),
		Expected: "installed",
		Actual:   actual,
	}}
}

func checkMacScreenLock() []ControlResult {
	out, _ := execCommandCompliance("defaults", "read", "com.apple.screensaver", "idleTime").Output()
	idleTime := strings.TrimSpace(string(out))
	passed := idleTime != "" && idleTime != "0"
	return []ControlResult{{
		ID:       "4.3",
		Title:    "Ensure screen lock is configured",
		Section:  "4",
		Status:   statusOf(passed),
		Expected: "> 0",
		Actual:   fmt.Sprintf("idleTime=%s", idleTime),
	}}
}
