//go:build linux

package linuxauth

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

func auditPrivescOS(_ map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("audit_privesc_vulnerabilities requires root privileges")
	}

	major, minor, patch := linuxKernelVersion()
	var findings []map[string]any

	// CVE-2021-4034 PwnKit
	pwnkitPresent := false
	if out, _ := exec.Command("dpkg", "-l", "policykit-1").Output(); strings.Contains(string(out), "ii") {
		pwnkitPresent = true
	} else if out2, _ := exec.Command("rpm", "-q", "polkit").Output(); !strings.Contains(string(out2), "not installed") && len(out2) > 0 {
		pwnkitPresent = true
	}
	findings = append(findings, map[string]any{
		"cve_id": "CVE-2021-4034", "severity": "critical", "condition_present": pwnkitPresent,
		"description": "PwnKit: polkit pkexec local privilege escalation",
		"remediation": "Upgrade polkit: apt-get upgrade policykit-1 or dnf update polkit",
	})

	// CVE-2022-0847 DirtyPipe
	dirtyPipe := major == 5 && minor >= 8 &&
		!((minor == 10 && patch >= 102) ||
			(minor == 15 && patch >= 25) ||
			(minor == 16 && patch >= 11) ||
			minor >= 17)
	findings = append(findings, map[string]any{
		"cve_id": "CVE-2022-0847", "severity": "high", "condition_present": dirtyPipe,
		"description": fmt.Sprintf("DirtyPipe: kernel %d.%d.%d may be vulnerable (introduced in 5.8, fixed in 5.10.102+/5.15.25+/5.16.11+)", major, minor, patch),
		"remediation": "Upgrade kernel to 5.16.11+, 5.15.25+, or 5.10.102+",
	})

	// CVE-2016-5195 Dirty COW
	dirtyCOW := major < 4 || (major == 4 && minor < 8)
	findings = append(findings, map[string]any{
		"cve_id": "CVE-2016-5195", "severity": "critical", "condition_present": dirtyCOW,
		"description": "Dirty COW: kernel < 4.8.3 vulnerable to race condition LPE",
		"remediation": "Upgrade kernel to 4.8.3 or later",
	})

	// Unexpected SUID binaries
	baseline := map[string]bool{
		"/usr/bin/sudo": true, "/usr/bin/su": true, "/usr/bin/passwd": true,
		"/usr/bin/newgrp": true, "/usr/bin/chsh": true, "/usr/bin/chfn": true,
		"/usr/bin/gpasswd": true, "/bin/ping": true, "/usr/bin/pkexec": true,
		"/usr/lib/openssh/ssh-keysign":                true,
		"/usr/lib/dbus-1.0/dbus-daemon-launch-helper": true,
	}
	if suidOut, _ := exec.Command("find", "/", "-perm", "-4000", "-o", "-perm", "-2000", "-type", "f").Output(); len(suidOut) > 0 {
		for _, line := range strings.Split(strings.TrimSpace(string(suidOut)), "\n") {
			if line == "" || baseline[line] {
				continue
			}
			findings = append(findings, map[string]any{
				"severity": "high", "condition_present": true, "tag": "unexpected-suid",
				"description": fmt.Sprintf("Unexpected SUID/SGID binary: %s", line),
				"remediation": fmt.Sprintf("Review: chmod u-s %s", line),
			})
		}
	}

	// Writable cron dirs
	for _, dir := range []string{"/etc/cron.daily", "/etc/cron.weekly", "/etc/cron.monthly", "/etc/cron.d"} {
		if fi, err := os.Stat(dir); err == nil && fi.Mode()&0002 != 0 {
			findings = append(findings, map[string]any{
				"severity": "high", "condition_present": true, "tag": "writable-cron",
				"description": fmt.Sprintf("World-writable cron dir: %s", dir),
				"remediation": fmt.Sprintf("chmod o-w %s", dir),
			})
		}
	}

	// Writable service files
	if svcOut, _ := exec.Command("find", "/etc/systemd", "/lib/systemd", "-perm", "-o+w", "-name", "*.service").Output(); len(svcOut) > 0 {
		for _, line := range strings.Split(strings.TrimSpace(string(svcOut)), "\n") {
			if line == "" {
				continue
			}
			findings = append(findings, map[string]any{
				"severity": "high", "condition_present": true, "tag": "writable-service",
				"description": fmt.Sprintf("World-writable service file: %s", line),
				"remediation": fmt.Sprintf("chmod o-w %s", line),
			})
		}
	}

	// Sudo NOPASSWD rules
	if sudoOut, _ := exec.Command("sudo", "-l").Output(); len(sudoOut) > 0 {
		for _, line := range strings.Split(string(sudoOut), "\n") {
			if strings.Contains(line, "NOPASSWD") {
				findings = append(findings, map[string]any{
					"severity": "medium", "condition_present": true, "tag": "sudo-misconfigured",
					"description": fmt.Sprintf("NOPASSWD sudo rule: %s", strings.TrimSpace(line)),
					"remediation": "Review /etc/sudoers and /etc/sudoers.d/",
				})
			}
		}
	}

	highestTag := "privesc-risk:medium"
	for _, f := range findings {
		if present, _ := f["condition_present"].(bool); !present {
			continue
		}
		if f["severity"] == "critical" {
			highestTag = "privesc-risk:critical"
			break
		}
		if f["severity"] == "high" {
			highestTag = "privesc-risk:high"
		}
	}

	return map[string]any{
		"findings":   findings,
		"total":      len(findings),
		"tags":       []string{highestTag},
		"audited_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func linuxKernelVersion() (major, minor, patch int) {
	out, _ := exec.Command("uname", "-r").Output()
	ver := strings.TrimSpace(string(out))
	parts := strings.SplitN(ver, ".", 3)
	if len(parts) >= 1 {
		major, _ = strconv.Atoi(parts[0])
	}
	if len(parts) >= 2 {
		minor, _ = strconv.Atoi(parts[1])
	}
	if len(parts) >= 3 {
		p := parts[2]
		if idx := strings.IndexAny(p, "-+"); idx != -1 {
			p = p[:idx]
		}
		patch, _ = strconv.Atoi(p)
	}
	return
}
