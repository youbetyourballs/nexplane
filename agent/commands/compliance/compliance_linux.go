//go:build linux

package compliance

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

func auditCISComplianceOS(level int, osFamily string) (map[string]any, error) {
	var controls []ControlResult

	// Level 1: filesystem, sysctl, ssh
	controls = append(controls, checkFilesystem()...)
	controls = append(controls, checkSysctl()...)
	controls = append(controls, checkSSH()...)

	// Level 2: pam, auditd, MAC (selinux or apparmor)
	if level >= 2 {
		controls = append(controls, checkPAM()...)
		controls = append(controls, checkAuditd()...)
		switch osFamily {
		case "rhel":
			controls = append(controls, checkSELinux()...)
		case "debian", "ubuntu":
			controls = append(controls, checkAppArmor()...)
		}
	}

	score := CalculateScore(controls)

	return map[string]any{
		"level":        level,
		"os_family":    osFamily,
		"score":        score,
		"controls":     controls,
		"collected_at": collectedNow(),
	}, nil
}

// --- filesystem section ---

func checkFilesystem() []ControlResult {
	return []ControlResult{
		checkModuleDisabled("1.1.1.1", "Disable mounting of cramfs filesystems", "cramfs"),
		checkModuleDisabled("1.1.1.2", "Disable mounting of squashfs filesystems", "squashfs"),
		checkModuleDisabled("1.1.1.3", "Disable mounting of udf filesystems", "udf"),
		checkMountOption("1.1.2", "Ensure /tmp is a separate partition", "/tmp", ""),
		checkMountOption("1.1.3", "Ensure noexec option on /tmp", "/tmp", "noexec"),
		checkMountOption("1.1.4", "Ensure nosuid option on /tmp", "/tmp", "nosuid"),
		checkMountOption("1.1.5", "Ensure nodev option on /tmp", "/tmp", "nodev"),
	}
}

func checkModuleDisabled(id, title, module string) ControlResult {
	expected := "install /bin/true"
	// Check /etc/modprobe.d/ files for "install <module> /bin/true"
	out, _ := exec.Command("sh", "-c",
		fmt.Sprintf("grep -r 'install %s /bin/true' /etc/modprobe.d/ 2>/dev/null | head -1", module),
	).Output()
	actual := strings.TrimSpace(string(out))
	status := "fail"
	if strings.Contains(actual, "install "+module+" /bin/true") {
		status = "pass"
		actual = expected
	} else if actual == "" {
		actual = "not set"
	}
	return ControlResult{ID: id, Title: title, Section: "filesystem", Status: status, Expected: expected, Actual: actual}
}

func checkMountOption(id, title, mountPoint, option string) ControlResult {
	data, err := os.ReadFile("/proc/mounts")
	if err != nil {
		return ControlResult{ID: id, Title: title, Section: "filesystem", Status: "skip", Expected: option, Actual: "cannot read /proc/mounts"}
	}
	scanner := bufio.NewScanner(strings.NewReader(string(data)))
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 4 {
			continue
		}
		if fields[1] == mountPoint {
			if option == "" {
				// Just checking the partition exists
				return ControlResult{ID: id, Title: title, Section: "filesystem", Status: "pass", Expected: "separate partition", Actual: fields[0]}
			}
			opts := strings.Split(fields[3], ",")
			for _, o := range opts {
				if o == option {
					return ControlResult{ID: id, Title: title, Section: "filesystem", Status: "pass", Expected: option, Actual: option}
				}
			}
			return ControlResult{ID: id, Title: title, Section: "filesystem", Status: "fail", Expected: option, Actual: fields[3]}
		}
	}
	if option == "" {
		return ControlResult{ID: id, Title: title, Section: "filesystem", Status: "fail", Expected: "separate partition", Actual: "not a separate partition"}
	}
	return ControlResult{ID: id, Title: title, Section: "filesystem", Status: "fail", Expected: option, Actual: "mount point not found"}
}

// --- sysctl section ---

func checkSysctl() []ControlResult {
	return []ControlResult{
		checkSysctlValue("3.1.1", "Ensure IP forwarding is disabled", "net.ipv4.ip_forward", "0"),
		checkSysctlValue("3.1.2", "Ensure packet redirect sending is disabled", "net.ipv4.conf.all.send_redirects", "0"),
		checkSysctlValue("1.5.3", "Ensure ASLR is enabled", "kernel.randomize_va_space", "2"),
		checkSysctlValue("1.5.4", "Ensure core dumps are restricted", "fs.suid_dumpable", "0"),
	}
}

func checkSysctlValue(id, title, key, expected string) ControlResult {
	out, err := exec.Command("sysctl", "-n", key).Output()
	actual := strings.TrimSpace(string(out))
	if err != nil || actual == "" {
		return ControlResult{ID: id, Title: title, Section: "sysctl", Status: "fail", Expected: expected, Actual: "error reading " + key}
	}
	status := "fail"
	if actual == expected {
		status = "pass"
	}
	return ControlResult{ID: id, Title: title, Section: "sysctl", Status: status, Expected: key + "=" + expected, Actual: key + "=" + actual}
}

// --- SSH section ---

func checkSSH() []ControlResult {
	cfg := readSSHConfig()
	return []ControlResult{
		checkSSHParam(cfg, "5.2.1", "Ensure SSH Protocol is set to 2", "Protocol", "2"),
		checkSSHParam(cfg, "5.2.8", "Ensure SSH root login is disabled", "PermitRootLogin", "no"),
		checkSSHParam(cfg, "5.2.9", "Ensure SSH PasswordAuthentication is disabled", "PasswordAuthentication", "no"),
		checkSSHMaxAuthTries(cfg),
		checkSSHClientAliveInterval(cfg),
	}
}

func readSSHConfig() map[string]string {
	cfg := map[string]string{}
	data, err := os.ReadFile("/etc/ssh/sshd_config")
	if err != nil {
		return cfg
	}
	scanner := bufio.NewScanner(strings.NewReader(string(data)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "#") || line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) >= 2 {
			cfg[fields[0]] = fields[1]
		}
	}
	return cfg
}

func checkSSHParam(cfg map[string]string, id, title, key, expected string) ControlResult {
	actual, ok := cfg[key]
	if !ok {
		return ControlResult{ID: id, Title: title, Section: "ssh", Status: "fail", Expected: expected, Actual: "not set"}
	}
	status := "fail"
	if strings.EqualFold(actual, expected) {
		status = "pass"
	}
	return ControlResult{ID: id, Title: title, Section: "ssh", Status: status, Expected: expected, Actual: actual}
}

func checkSSHMaxAuthTries(cfg map[string]string) ControlResult {
	id, title, key := "5.2.5", "Ensure SSH MaxAuthTries is set to 4 or less", "MaxAuthTries"
	val, ok := cfg[key]
	if !ok {
		return ControlResult{ID: id, Title: title, Section: "ssh", Status: "fail", Expected: "<= 4", Actual: "not set"}
	}
	var n int
	fmt.Sscanf(val, "%d", &n)
	status := "pass"
	if n > 4 {
		status = "fail"
	}
	return ControlResult{ID: id, Title: title, Section: "ssh", Status: status, Expected: "<= 4", Actual: val}
}

func checkSSHClientAliveInterval(cfg map[string]string) ControlResult {
	id, title, key := "5.2.13", "Ensure SSH ClientAliveInterval is 300 or less", "ClientAliveInterval"
	val, ok := cfg[key]
	if !ok {
		return ControlResult{ID: id, Title: title, Section: "ssh", Status: "fail", Expected: "<= 300", Actual: "not set"}
	}
	var n int
	fmt.Sscanf(val, "%d", &n)
	status := "pass"
	if n == 0 || n > 300 {
		status = "fail"
	}
	return ControlResult{ID: id, Title: title, Section: "ssh", Status: status, Expected: "<= 300", Actual: val}
}

// --- PAM section ---

func checkPAM() []ControlResult {
	return []ControlResult{
		checkFileContains("6.3.1", "Ensure pam_pwquality is configured", "/etc/pam.d/system-auth", "pam_pwquality"),
		checkFileContains("6.3.3", "Ensure password reuse is limited (history >= 5)", "/etc/pam.d/system-auth", "remember="),
		checkFileContains("6.3.2", "Ensure account lockout is configured", "/etc/pam.d/system-auth", "pam_faillock"),
	}
}

func checkFileContains(id, title, path, substr string) ControlResult {
	data, err := os.ReadFile(path)
	if err != nil {
		return ControlResult{ID: id, Title: title, Section: "pam", Status: "skip", Expected: substr, Actual: "cannot read " + path}
	}
	actual := strings.TrimSpace(string(data))
	if strings.Contains(actual, substr) {
		return ControlResult{ID: id, Title: title, Section: "pam", Status: "pass", Expected: "contains " + substr, Actual: "found"}
	}
	return ControlResult{ID: id, Title: title, Section: "pam", Status: "fail", Expected: "contains " + substr, Actual: "not found"}
}

// --- Auditd section ---

func checkAuditd() []ControlResult {
	return []ControlResult{
		checkAuditdRunning(),
		checkAuditRule("4.1.3", "Ensure privileged commands are audited", "-a always,exit -F arch=b64 -S execve"),
		checkAuditRule("4.1.5", "Ensure /etc/passwd changes are audited", "-w /etc/passwd -p wa"),
		checkAuditRule("4.1.6", "Ensure /etc/sudoers changes are audited", "-w /etc/sudoers -p wa"),
	}
}

func checkAuditdRunning() ControlResult {
	out, _ := exec.Command("systemctl", "is-active", "auditd").Output()
	actual := strings.TrimSpace(string(out))
	status := "fail"
	if actual == "active" {
		status = "pass"
	}
	return ControlResult{ID: "4.1.1", Title: "Ensure auditd service is running", Section: "auditd", Status: status, Expected: "active", Actual: actual}
}

func checkAuditRule(id, title, ruleFragment string) ControlResult {
	out, _ := exec.Command("auditctl", "-l").Output()
	rules := string(out)
	if strings.Contains(rules, ruleFragment) {
		return ControlResult{ID: id, Title: title, Section: "auditd", Status: "pass", Expected: ruleFragment, Actual: "found"}
	}
	return ControlResult{ID: id, Title: title, Section: "auditd", Status: "fail", Expected: ruleFragment, Actual: "not found"}
}

// --- SELinux section (RHEL) ---

func checkSELinux() []ControlResult {
	data, err := os.ReadFile("/etc/selinux/config")
	if err != nil {
		return []ControlResult{{
			ID: "1.6.1.1", Title: "Ensure SELinux is set to enforcing", Section: "selinux",
			Status: "skip", Expected: "SELINUX=enforcing", Actual: "cannot read /etc/selinux/config",
		}}
	}
	actual := "not set"
	scanner := bufio.NewScanner(strings.NewReader(string(data)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if strings.HasPrefix(line, "SELINUX=") {
			actual = strings.TrimPrefix(line, "SELINUX=")
		}
	}
	status := "fail"
	if actual == "enforcing" {
		status = "pass"
	}
	return []ControlResult{{
		ID: "1.6.1.1", Title: "Ensure SELinux is set to enforcing", Section: "selinux",
		Status: status, Expected: "enforcing", Actual: actual,
	}}
}

// --- AppArmor section (Debian/Ubuntu) ---

func checkAppArmor() []ControlResult {
	out, err := exec.Command("aa-status").Output()
	if err != nil {
		return []ControlResult{{
			ID: "1.6.2.1", Title: "Ensure AppArmor profiles are enforced", Section: "apparmor",
			Status: "skip", Expected: "profiles in enforce mode", Actual: "aa-status failed",
		}}
	}
	output := string(out)
	if strings.Contains(output, "profiles are in enforce mode") && !strings.Contains(output, "0 profiles are in enforce mode") {
		return []ControlResult{{
			ID: "1.6.2.1", Title: "Ensure AppArmor profiles are enforced", Section: "apparmor",
			Status: "pass", Expected: "profiles in enforce mode", Actual: "enforced profiles present",
		}}
	}
	return []ControlResult{{
		ID: "1.6.2.1", Title: "Ensure AppArmor profiles are enforced", Section: "apparmor",
		Status: "fail", Expected: "profiles in enforce mode", Actual: "no enforced profiles",
	}}
}

// --- Evidence collection ---

func collectEvidenceOS(params map[string]any) (map[string]any, error) {
	evidenceTypesRaw, _ := params["evidence_types"].([]any)
	evidenceTypes := map[string]bool{}
	for _, et := range evidenceTypesRaw {
		if s, ok := et.(string); ok {
			evidenceTypes[s] = true
		}
	}
	if len(evidenceTypes) == 0 {
		evidenceTypes = map[string]bool{"config_files": true, "command_outputs": true}
	}

	var artifacts []map[string]any

	if evidenceTypes["config_files"] {
		for _, path := range []string{
			"/etc/ssh/sshd_config",
			"/etc/pam.d/system-auth",
			"/etc/audit/audit.rules",
			"/etc/selinux/config",
		} {
			content, err := os.ReadFile(path)
			if err != nil {
				continue
			}
			artifacts = append(artifacts, map[string]any{
				"type":    "config_file",
				"path":    path,
				"content": string(content),
			})
		}
	}

	if evidenceTypes["command_outputs"] {
		cmds := [][]string{
			{"sestatus"},
			{"auditctl", "-l"},
			{"sysctl", "-a"},
			{"ss", "-tlnp"},
			{"last", "-n", "20"},
		}
		for _, cmd := range cmds {
			out, _ := exec.Command(cmd[0], cmd[1:]...).Output()
			artifacts = append(artifacts, map[string]any{
				"type":    "command_output",
				"command": strings.Join(cmd, " "),
				"output":  string(out),
			})
		}
	}

	hostname, _ := os.Hostname()
	return map[string]any{
		"framework":    params["framework"],
		"control_id":  params["control_id"],
		"hostname":    hostname,
		"collected_at": collectedNow(),
		"artifacts":   artifacts,
	}, nil
}
