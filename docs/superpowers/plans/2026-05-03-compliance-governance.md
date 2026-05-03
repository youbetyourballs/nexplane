# Compliance & Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a full compliance and governance layer to Nexplane: CIS Benchmark auditing via a new `compliance` agent command package, baseline drift detection with auto-remediation DRAFT change requests, audit-ready evidence bundles streamed as ZIP, and change freeze window enforcement injected as a FastAPI dependency into the approve/execute routes.

**Architecture:** A new `agent/commands/compliance/` package provides `audit_cis_compliance` (per-control pass/fail scoring across filesystem, sysctl, ssh, pam, auditd, selinux/apparmor sections) and `collect_evidence` (config files + command outputs + change logs packaged as JSON). The backend gains a `backend/app/routers/compliance.py` router (baselines CRUD, drift alerts, evidence ZIP endpoint, freeze window management) and a `backend/app/compliance/freeze.py` FastAPI dependency (`require_no_active_freeze`) injected into the existing approve and execute routes. Two new Alembic migrations add `compliance_baselines` and `change_freeze_windows` tables. A weekly APScheduler cron job in `scheduler_service.py` runs drift detection and creates DRAFT `enforce_cis_benchmark` change requests. The frontend gains a `/compliance` page with per-host CIS score cards and a sticky amber freeze banner in `Layout.tsx`.

**Tech Stack:** Go 1.26 (`os/exec`, build tags for Linux/other), Python 3.12 + FastAPI + SQLAlchemy 2 async + APScheduler 3 (CronTrigger), `zipfile` + `StreamingResponse`, React 18 + TanStack Query, Tailwind CSS, PostgreSQL JSONB + ARRAY(JSONB).

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `agent/commands/compliance/compliance.go` | Create | Package-level dispatcher; exported `AuditCISComplianceExecute` and `CollectEvidenceExecute` wrappers |
| `agent/commands/compliance/compliance_linux.go` | Create | `auditCISComplianceLinux` — per-section CIS control checks; `collectEvidenceLinux` |
| `agent/commands/compliance/compliance_windows.go` | Create | `collectEvidenceWindows` stub returning structured JSON |
| `agent/commands/compliance/compliance_other.go` | Create | `auditCISComplianceOther` + `collectEvidenceOther` stubs for non-Linux/Windows |
| `agent/commands/compliance/compliance_test.go` | Create | Unit tests for score calculation, section checks, drift detection helpers |
| `agent/executor/executor.go` | Modify | Register `audit_cis_compliance` and `collect_evidence` commands |
| `backend/alembic/versions/016_add_compliance_baselines.py` | Create | `compliance_baselines` table migration |
| `backend/alembic/versions/017_add_change_freeze_windows.py` | Create | `change_freeze_windows` table migration |
| `backend/app/models/compliance.py` | Create | `ComplianceBaseline` + `ChangeFreezeWindow` SQLAlchemy models |
| `backend/app/schemas/compliance.py` | Create | Pydantic read/create/update schemas for both models + evidence request |
| `backend/app/routers/compliance.py` | Create | All compliance API routes (baselines CRUD, drift-alerts, evidence-collection, freeze windows) |
| `backend/app/compliance/freeze.py` | Create | `require_no_active_freeze` FastAPI dependency; `log_freeze_bypass` |
| `backend/app/compliance/drift.py` | Create | `run_drift_detection`, `detect_drift`, `write_cis_score_to_metadata` |
| `backend/app/models/change_request.py` | Modify | Add `enforce_cis_benchmark` + `collect_evidence` to `ChangeType` enum |
| `backend/app/routers/change_requests.py` | Modify | Inject `require_no_active_freeze` on approve + execute routes |
| `backend/app/services/scheduler_service.py` | Modify | Add weekly `run_drift_detection` cron job in `start()` |
| `backend/app/main.py` | Modify | Import and include compliance router |
| `backend/app/change_type_definitions/enforce_cis_benchmark.json` | Create | Change type definition JSON for the AI planner |
| `backend/app/change_type_definitions/collect_evidence.json` | Create | Change type definition JSON for the AI planner |
| `frontend/src/pages/Compliance.tsx` | Create | Per-host CIS score cards, control breakdown table, evidence request button |
| `frontend/src/components/FreezeAlert.tsx` | Create | Sticky amber banner shown when an active freeze window exists |
| `frontend/src/components/Layout.tsx` | Modify | Poll `GET /compliance/freeze-windows/active`; render `FreezeAlert` when active |
| `frontend/src/App.tsx` | Modify | Add `/compliance` route; add Compliance nav item |

---

## Task 1: Agent compliance package — `audit_cis_compliance`

**Files:**
- Create: `agent/commands/compliance/compliance_test.go`
- Create: `agent/commands/compliance/compliance.go`
- Create: `agent/commands/compliance/compliance_linux.go`
- Create: `agent/commands/compliance/compliance_other.go`

### Step 1: Write failing tests first

Create `agent/commands/compliance/compliance_test.go`:

```go
//go:build linux

package compliance_test

import (
	"context"
	"testing"

	"nexplane-agent/commands/compliance"
)

func TestAuditCISCompliance_ReturnsStructuredOutput(t *testing.T) {
	out, err := compliance.AuditCISComplianceExecute(map[string]any{
		"level":     float64(1),
		"os_family": "rhel",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	controls, ok := out["controls"]
	if !ok {
		t.Fatal("missing 'controls' key in output")
	}
	list, ok := controls.([]compliance.ControlResult)
	if !ok {
		t.Fatalf("controls is not []ControlResult, got %T", controls)
	}
	if len(list) == 0 {
		t.Fatal("expected at least one control result")
	}
	score, ok := out["score"].(float64)
	if !ok {
		t.Fatal("missing or non-float 'score' key in output")
	}
	if score < 0 || score > 1 {
		t.Fatalf("score out of range [0,1]: %f", score)
	}
}

func TestAuditCISCompliance_Level1_OnlyBasicSections(t *testing.T) {
	out, err := compliance.AuditCISComplianceExecute(map[string]any{
		"level":     float64(1),
		"os_family": "debian",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	controls := out["controls"].([]compliance.ControlResult)
	// Level 1 must not include pam, auditd, selinux, apparmor sections
	for _, c := range controls {
		switch c.Section {
		case "pam", "auditd", "selinux", "apparmor":
			t.Errorf("level 1 should not include section %q (control %s)", c.Section, c.ID)
		}
	}
}

func TestAuditCISCompliance_Level2_IncludesAllSections(t *testing.T) {
	out, err := compliance.AuditCISComplianceExecute(map[string]any{
		"level":     float64(2),
		"os_family": "rhel",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	controls := out["controls"].([]compliance.ControlResult)
	sections := map[string]bool{}
	for _, c := range controls {
		sections[c.Section] = true
	}
	for _, want := range []string{"filesystem", "sysctl", "ssh", "pam", "auditd", "selinux"} {
		if !sections[want] {
			t.Errorf("level 2 rhel: expected section %q in controls", want)
		}
	}
}

func TestAuditCISCompliance_InvalidOSFamily(t *testing.T) {
	_, err := compliance.AuditCISComplianceExecute(map[string]any{
		"level":     float64(1),
		"os_family": "openbsd",
	})
	if err == nil {
		t.Fatal("expected error for unsupported os_family")
	}
}

func TestAuditCISCompliance_MissingLevel(t *testing.T) {
	_, err := compliance.AuditCISComplianceExecute(map[string]any{
		"os_family": "rhel",
	})
	if err == nil {
		t.Fatal("expected error when level is missing")
	}
}

func TestCalculateScore(t *testing.T) {
	controls := []compliance.ControlResult{
		{Status: "pass"},
		{Status: "pass"},
		{Status: "fail"},
		{Status: "skip"},
	}
	score := compliance.CalculateScore(controls)
	// 2 pass out of 3 non-skip = 0.666...
	if score < 0.66 || score > 0.67 {
		t.Fatalf("expected ~0.666, got %f", score)
	}
}

func TestCalculateScore_AllSkip(t *testing.T) {
	controls := []compliance.ControlResult{
		{Status: "skip"},
		{Status: "skip"},
	}
	score := compliance.CalculateScore(controls)
	if score != 1.0 {
		t.Fatalf("all-skip score should be 1.0, got %f", score)
	}
}
```

- [ ] **Step 2: Run tests — expect compile failure (package does not exist yet)**

```bash
cd /app/agent && go test ./commands/compliance/... -v 2>&1 | head -20
```

Expected: `cannot find package` or `build constraints exclude all Go files` — no package exists yet.

- [ ] **Step 3: Create `compliance.go` — dispatcher and shared types**

Create `agent/commands/compliance/compliance.go`:

```go
package compliance

import (
	"fmt"
	"time"
)

// ControlResult holds the pass/fail outcome for a single CIS control.
type ControlResult struct {
	ID       string `json:"id"`
	Title    string `json:"title"`
	Section  string `json:"section"`
	Status   string `json:"status"`   // "pass" | "fail" | "skip"
	Expected string `json:"expected"`
	Actual   string `json:"actual"`
}

// AuditCISComplianceExecute is the command entrypoint registered in executor.go.
// params: {"level": 1|2, "os_family": "rhel"|"debian"|"ubuntu"}
func AuditCISComplianceExecute(params map[string]any) (map[string]any, error) {
	levelF, ok := params["level"].(float64)
	if !ok {
		return nil, fmt.Errorf("level is required and must be 1 or 2")
	}
	level := int(levelF)
	if level != 1 && level != 2 {
		return nil, fmt.Errorf("level must be 1 or 2, got %d", level)
	}
	osFamily, _ := params["os_family"].(string)
	switch osFamily {
	case "rhel", "debian", "ubuntu":
	default:
		return nil, fmt.Errorf("unsupported os_family %q: must be rhel, debian, or ubuntu", osFamily)
	}
	return auditCISComplianceOS(level, osFamily)
}

// CollectEvidenceExecute is the command entrypoint for evidence collection.
// params: {"framework": "soc2"|"pci"|"iso27001", "control_id": "CC6.1", "evidence_types": [...]}
func CollectEvidenceExecute(params map[string]any) (map[string]any, error) {
	framework, _ := params["framework"].(string)
	switch framework {
	case "soc2", "pci", "iso27001":
	default:
		return nil, fmt.Errorf("unsupported framework %q: must be soc2, pci, or iso27001", framework)
	}
	controlID, _ := params["control_id"].(string)
	if controlID == "" {
		return nil, fmt.Errorf("control_id is required")
	}
	return collectEvidenceOS(params)
}

// CalculateScore returns passing controls / non-skip controls.
// Returns 1.0 if all controls are skipped.
// Exported for testing.
func CalculateScore(controls []ControlResult) float64 {
	total := 0
	pass := 0
	for _, c := range controls {
		if c.Status == "skip" {
			continue
		}
		total++
		if c.Status == "pass" {
			pass++
		}
	}
	if total == 0 {
		return 1.0
	}
	return float64(pass) / float64(total)
}

// collectedAt returns current UTC time formatted as RFC3339.
func collectedNow() string {
	return time.Now().UTC().Format(time.RFC3339)
}
```

- [ ] **Step 4: Create `compliance_linux.go` — full CIS audit implementation**

Create `agent/commands/compliance/compliance_linux.go`:

```go
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
```

- [ ] **Step 5: Create `compliance_other.go` — stubs for non-Linux**

Create `agent/commands/compliance/compliance_other.go`:

```go
//go:build !linux && !windows

package compliance

import "fmt"

func auditCISComplianceOS(level int, osFamily string) (map[string]any, error) {
	return nil, fmt.Errorf("audit_cis_compliance is only supported on Linux (requested os_family=%s)", osFamily)
}

func collectEvidenceOS(params map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("collect_evidence is not supported on this platform")
}
```

- [ ] **Step 6: Create `compliance_windows.go` — Windows evidence collection stub**

Create `agent/commands/compliance/compliance_windows.go`:

```go
//go:build windows

package compliance

import (
	"fmt"
	"os"
	"time"
)

func auditCISComplianceOS(level int, osFamily string) (map[string]any, error) {
	return nil, fmt.Errorf("audit_cis_compliance is not supported on Windows")
}

func collectEvidenceOS(params map[string]any) (map[string]any, error) {
	hostname, _ := os.Hostname()
	return map[string]any{
		"framework":    params["framework"],
		"control_id":  params["control_id"],
		"hostname":    hostname,
		"collected_at": time.Now().UTC().Format(time.RFC3339),
		"artifacts":   []map[string]any{},
		"note":        "Windows evidence collection not yet implemented",
	}, nil
}
```

- [ ] **Step 7: Run tests — expect pass on Linux**

```bash
cd /app/agent && go test ./commands/compliance/... -v
```

Expected output:
```
--- PASS: TestAuditCISCompliance_ReturnsStructuredOutput
--- PASS: TestAuditCISCompliance_Level1_OnlyBasicSections
--- PASS: TestAuditCISCompliance_Level2_IncludesAllSections
--- PASS: TestAuditCISCompliance_InvalidOSFamily
--- PASS: TestAuditCISCompliance_MissingLevel
--- PASS: TestCalculateScore
--- PASS: TestCalculateScore_AllSkip
PASS
ok      nexplane-agent/commands/compliance
```

- [ ] **Step 8: Commit**

```bash
git add agent/commands/compliance/
git commit -m "feat(agent): add compliance package — audit_cis_compliance with per-section CIS controls"
```

---

## Task 2: Register compliance commands in executor

**Files:**
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Add compliance import and register commands**

In `agent/executor/executor.go`, add the import:

```go
"nexplane-agent/commands/compliance"
```

Add to the `commands` map after the `linuxupgrade` entries:

```go
// Compliance (Spec: compliance-governance)
"audit_cis_compliance": compliance.AuditCISComplianceExecute,
"collect_evidence":     compliance.CollectEvidenceExecute,
```

- [ ] **Step 2: Build to verify compile**

```bash
cd /app/agent && go build ./...
```

Expected: exits 0.

- [ ] **Step 3: Run full agent test suite**

```bash
cd /app/agent && go test ./... 2>&1
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add agent/executor/executor.go
git commit -m "feat(agent): register audit_cis_compliance and collect_evidence in executor"
```

---

## Task 3: Database migrations — `compliance_baselines` and `change_freeze_windows`

**Files:**
- Create: `backend/alembic/versions/016_add_compliance_baselines.py`
- Create: `backend/alembic/versions/017_add_change_freeze_windows.py`
- Create: `backend/app/models/compliance.py`
- Create: `backend/app/schemas/compliance.py`

- [ ] **Step 1: Write `016_add_compliance_baselines.py`**

Create `backend/alembic/versions/016_add_compliance_baselines.py`:

```python
"""add compliance_baselines table

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'compliance_baselines',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('scope_type', sa.Text(), nullable=False),
        sa.Column('scope_value', sa.Text(), nullable=False),
        sa.Column('cis_level', sa.Integer(), nullable=False),
        sa.Column('os_family', sa.Text(), nullable=False),
        sa.Column('config', postgresql.JSONB(), nullable=False),
        sa.Column('history', postgresql.ARRAY(postgresql.JSONB()), nullable=False,
                  server_default='{}'),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('auto_execute', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("scope_type IN ('asset', 'tag')", name='ck_baseline_scope_type'),
        sa.CheckConstraint('cis_level IN (1, 2)', name='ck_baseline_cis_level'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_compliance_baselines_scope',
        'compliance_baselines',
        ['scope_type', 'scope_value'],
    )
    op.create_index(
        'idx_compliance_baselines_org',
        'compliance_baselines',
        ['organization_id'],
    )


def downgrade() -> None:
    op.drop_index('idx_compliance_baselines_scope', table_name='compliance_baselines')
    op.drop_index('idx_compliance_baselines_org', table_name='compliance_baselines')
    op.drop_table('compliance_baselines')
```

- [ ] **Step 2: Write `017_add_change_freeze_windows.py`**

Create `backend/alembic/versions/017_add_change_freeze_windows.py`:

```python
"""add change_freeze_windows table

Revision ID: 017
Revises: 016
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '017'
down_revision = '016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'change_freeze_windows',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('start_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('emergency_bypass_role', sa.Text(), nullable=False,
                  server_default='emergency_bypass'),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint('end_at > start_at', name='ck_freeze_window_dates'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_freeze_windows_active',
        'change_freeze_windows',
        ['start_at', 'end_at'],
    )


def downgrade() -> None:
    op.drop_index('idx_freeze_windows_active', table_name='change_freeze_windows')
    op.drop_table('change_freeze_windows')
```

- [ ] **Step 3: Create `backend/app/models/compliance.py`**

```python
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Text, Integer, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

from app.database import Base


class ComplianceBaseline(Base):
    __tablename__ = "compliance_baselines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_type: Mapped[str] = mapped_column(Text, nullable=False)   # "asset" | "tag"
    scope_value: Mapped[str] = mapped_column(Text, nullable=False)
    cis_level: Mapped[int] = mapped_column(Integer, nullable=False)
    os_family: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    history: Mapped[list] = mapped_column(ARRAY(JSONB), nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    auto_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now()
    )


class ChangeFreezeWindow(Base):
    __tablename__ = "change_freeze_windows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    emergency_bypass_role: Mapped[str] = mapped_column(Text, nullable=False, default="emergency_bypass")
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
```

- [ ] **Step 4: Create `backend/app/schemas/compliance.py`**

```python
import uuid
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, field_validator


# --- ComplianceBaseline ---

class ComplianceBaselineCreate(BaseModel):
    name: str
    description: Optional[str] = None
    scope_type: Literal["asset", "tag"]
    scope_value: str
    cis_level: Literal[1, 2]
    os_family: Literal["rhel", "debian", "ubuntu"]
    config: dict
    auto_execute: bool = False


class ComplianceBaselineUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config: Optional[dict] = None
    auto_execute: Optional[bool] = None


class ComplianceBaselineRead(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    organization_id: uuid.UUID
    scope_type: str
    scope_value: str
    cis_level: int
    os_family: str
    config: dict
    history: list
    version: int
    auto_execute: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- ChangeFreezeWindow ---

class ChangeFreezeWindowCreate(BaseModel):
    reason: str
    start_at: datetime
    end_at: datetime
    emergency_bypass_role: str = "emergency_bypass"

    @field_validator("end_at")
    @classmethod
    def end_after_start(cls, v, info):
        if "start_at" in info.data and v <= info.data["start_at"]:
            raise ValueError("end_at must be after start_at")
        return v


class ChangeFreezeWindowRead(BaseModel):
    id: uuid.UUID
    reason: str
    start_at: datetime
    end_at: datetime
    emergency_bypass_role: str
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Evidence collection ---

class EvidenceCollectionRequest(BaseModel):
    framework: Literal["soc2", "pci", "iso27001"]
    control_id: str
    asset_ids: list[uuid.UUID]
    evidence_types: list[str] = ["config_files", "command_outputs", "change_logs"]
```

- [ ] **Step 5: Run migration inside the container**

```bash
docker compose exec backend alembic upgrade head
```

Expected: `Running upgrade 015 -> 016` and `Running upgrade 016 -> 017` with no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/016_add_compliance_baselines.py \
        backend/alembic/versions/017_add_change_freeze_windows.py \
        backend/app/models/compliance.py \
        backend/app/schemas/compliance.py
git commit -m "feat(db): add compliance_baselines and change_freeze_windows tables with models and schemas"
```

---

## Task 4: Compliance API router

**Files:**
- Create: `backend/app/routers/compliance.py`
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add new change types to `ChangeType` enum**

In `backend/app/models/change_request.py`, add to the `ChangeType` enum:

```python
enforce_cis_benchmark = "enforce_cis_benchmark"
collect_evidence = "collect_evidence"
```

- [ ] **Step 2: Create `backend/app/routers/compliance.py`**

```python
import io
import json
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.asset import Asset
from app.models.change_request import ChangeRequest, ChangeRequestStatus
from app.models.compliance import ComplianceBaseline, ChangeFreezeWindow
from app.routers import current_user
from app.models.user import User
from app.schemas.compliance import (
    ComplianceBaselineCreate,
    ComplianceBaselineRead,
    ComplianceBaselineUpdate,
    ChangeFreezeWindowCreate,
    ChangeFreezeWindowRead,
    EvidenceCollectionRequest,
)

router = APIRouter(prefix="/compliance", tags=["Compliance"])


# ---- Baselines CRUD ----

@router.get("/baselines", response_model=list[ComplianceBaselineRead])
async def list_baselines(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ComplianceBaseline)
        .where(ComplianceBaseline.organization_id == user.organization_id)
        .order_by(ComplianceBaseline.created_at.desc())
    )
    return result.scalars().all()


@router.post("/baselines", response_model=ComplianceBaselineRead, status_code=201)
async def create_baseline(
    body: ComplianceBaselineCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    baseline = ComplianceBaseline(
        organization_id=user.organization_id,
        **body.model_dump(),
    )
    db.add(baseline)
    await db.commit()
    await db.refresh(baseline)
    return baseline


@router.get("/baselines/{baseline_id}", response_model=ComplianceBaselineRead)
async def get_baseline(
    baseline_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    baseline = await _get_baseline(db, baseline_id, user.organization_id)
    return baseline


@router.put("/baselines/{baseline_id}", response_model=ComplianceBaselineRead)
async def update_baseline(
    baseline_id: uuid.UUID,
    body: ComplianceBaselineUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    baseline = await _get_baseline(db, baseline_id, user.organization_id)
    if body.config is not None and body.config != baseline.config:
        # Save current config snapshot to history before overwriting
        history = list(baseline.history or [])
        history.append(baseline.config)
        baseline.history = history
        baseline.version += 1
        baseline.config = body.config
    if body.name is not None:
        baseline.name = body.name
    if body.description is not None:
        baseline.description = body.description
    if body.auto_execute is not None:
        baseline.auto_execute = body.auto_execute
    baseline.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(baseline)
    return baseline


@router.delete("/baselines/{baseline_id}", status_code=204)
async def delete_baseline(
    baseline_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    baseline = await _get_baseline(db, baseline_id, user.organization_id)
    await db.delete(baseline)
    await db.commit()


async def _get_baseline(
    db: AsyncSession, baseline_id: uuid.UUID, org_id: uuid.UUID
) -> ComplianceBaseline:
    result = await db.execute(
        select(ComplianceBaseline).where(
            ComplianceBaseline.id == baseline_id,
            ComplianceBaseline.organization_id == org_id,
        )
    )
    baseline = result.scalar_one_or_none()
    if not baseline:
        raise HTTPException(status_code=404, detail="Compliance baseline not found")
    return baseline


# ---- Drift alerts ----

@router.get("/drift-alerts")
async def list_drift_alerts(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return assets that have open DRAFT enforce_cis_benchmark change requests (drift-detected)."""
    result = await db.execute(
        select(ChangeRequest).where(
            ChangeRequest.organization_id == user.organization_id,
            ChangeRequest.change_type == "enforce_cis_benchmark",
            ChangeRequest.status == ChangeRequestStatus.draft,
        ).order_by(ChangeRequest.created_at.desc())
    )
    crs = result.scalars().all()
    return [
        {
            "change_request_id": str(cr.id),
            "title": cr.title,
            "target_asset_ids": cr.target_asset_ids,
            "created_at": cr.created_at.isoformat(),
        }
        for cr in crs
    ]


# ---- Evidence collection ----

@router.post("/evidence-collection")
async def collect_evidence(
    body: EvidenceCollectionRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Dispatch collect_evidence agent job to each asset, aggregate results,
    and stream a ZIP archive containing all artifacts.
    """
    # Resolve assets
    result = await db.execute(
        select(Asset).where(
            Asset.organization_id == user.organization_id,
            Asset.id.in_(body.asset_ids),
        )
    )
    assets = result.scalars().all()
    if not assets:
        raise HTTPException(status_code=404, detail="No matching assets found")

    # Dispatch agent jobs and collect results
    from app.services.agent_job_service import dispatch_agent_job
    job_results = []
    for asset in assets:
        try:
            job_result = await dispatch_agent_job(
                db=db,
                asset_id=asset.id,
                command="collect_evidence",
                parameters={
                    "framework": body.framework,
                    "control_id": body.control_id,
                    "evidence_types": body.evidence_types,
                },
            )
            job_results.append((asset, job_result))
        except Exception as exc:
            job_results.append((asset, {"error": str(exc), "artifacts": []}))

    # Fetch change logs from DB for each asset
    change_logs: dict[str, list] = {}
    if "change_logs" in body.evidence_types:
        from sqlalchemy import and_
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        for asset in assets:
            cr_result = await db.execute(
                select(ChangeRequest).where(
                    and_(
                        ChangeRequest.organization_id == user.organization_id,
                        ChangeRequest.created_at >= cutoff,
                    )
                )
            )
            crs = cr_result.scalars().all()
            asset_id_str = str(asset.id)
            change_logs[asset_id_str] = [
                {
                    "id": str(cr.id),
                    "title": cr.title,
                    "change_type": cr.change_type.value if hasattr(cr.change_type, "value") else cr.change_type,
                    "status": cr.status.value if hasattr(cr.status, "value") else cr.status,
                    "created_at": cr.created_at.isoformat(),
                }
                for cr in crs
                if asset_id_str in (cr.target_asset_ids or [])
            ]

    # Build ZIP in memory
    buf = io.BytesIO()
    collected_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "framework": body.framework,
        "control_id": body.control_id,
        "collected_at": collected_at,
        "nexplane_version": "0.1.0",
        "assets": [a.name or str(a.id) for a in assets],
        "evidence_types": body.evidence_types,
    }

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for asset, job_result in job_results:
            hostname = getattr(asset, "name", None) or str(asset.id)
            artifacts = job_result.get("artifacts", [])
            for artifact in artifacts:
                if artifact.get("type") == "config_file":
                    filename = artifact["path"].lstrip("/").replace("/", "_")
                    zf.writestr(f"{hostname}/{filename}", artifact.get("content", ""))
                elif artifact.get("type") == "command_output":
                    cmd_safe = artifact["command"].replace(" ", "_").replace("/", "_")
                    zf.writestr(f"{hostname}/{cmd_safe}.txt", artifact.get("output", ""))
            if "change_logs" in body.evidence_types:
                asset_id_str = str(asset.id)
                logs = change_logs.get(asset_id_str, [])
                zf.writestr(f"{hostname}/change_log.json", json.dumps(logs, indent=2))

    buf.seek(0)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"evidence-{body.framework}-{body.control_id}-{date_str}.zip"

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---- Freeze windows ----

@router.get("/freeze-windows/active", response_model=Optional[ChangeFreezeWindowRead])
async def get_active_freeze(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current active freeze window or 404."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ChangeFreezeWindow)
        .where(ChangeFreezeWindow.start_at <= now, ChangeFreezeWindow.end_at >= now)
        .limit(1)
    )
    freeze = result.scalar_one_or_none()
    if freeze is None:
        raise HTTPException(status_code=404, detail="No active freeze window")
    return freeze


@router.get("/freeze-windows", response_model=list[ChangeFreezeWindowRead])
async def list_freeze_windows(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChangeFreezeWindow).order_by(ChangeFreezeWindow.start_at.desc())
    )
    return result.scalars().all()


@router.post("/freeze-windows", response_model=ChangeFreezeWindowRead, status_code=201)
async def create_freeze_window(
    body: ChangeFreezeWindowCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    freeze = ChangeFreezeWindow(
        reason=body.reason,
        start_at=body.start_at,
        end_at=body.end_at,
        emergency_bypass_role=body.emergency_bypass_role,
        created_by=user.id,
    )
    db.add(freeze)
    await db.commit()
    await db.refresh(freeze)
    return freeze


@router.delete("/freeze-windows/{freeze_id}", status_code=204)
async def delete_freeze_window(
    freeze_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChangeFreezeWindow).where(ChangeFreezeWindow.id == freeze_id)
    )
    freeze = result.scalar_one_or_none()
    if not freeze:
        raise HTTPException(status_code=404, detail="Freeze window not found")
    await db.delete(freeze)
    await db.commit()
```

- [ ] **Step 3: Register compliance router in `main.py`**

In `backend/app/main.py`, add to imports:

```python
from app.routers import compliance as compliance_router
```

Add after the `agent_router` include:

```python
app.include_router(compliance_router.router)
```

- [ ] **Step 4: Restart backend and smoke test routes**

```bash
docker compose stop frontend && docker compose up frontend -d
docker compose restart backend
```

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/compliance/baselines \
  -H "Authorization: Bearer $TOKEN"
```

Expected: `200`

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/compliance/freeze-windows/active \
  -H "Authorization: Bearer $TOKEN"
```

Expected: `404` (no active freeze yet).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/compliance.py \
        backend/app/models/change_request.py \
        backend/app/main.py
git commit -m "feat(backend): add compliance router — baselines CRUD, drift alerts, evidence ZIP, freeze windows"
```

---

## Task 5: Change freeze enforcement dependency

**Files:**
- Create: `backend/app/compliance/freeze.py`
- Modify: `backend/app/routers/change_requests.py`

- [ ] **Step 1: Write failing test first**

Create `backend/tests/test_compliance_freeze.py` (create `backend/tests/` directory if needed):

```python
"""
Tests for require_no_active_freeze dependency.
Run with: docker compose exec backend pytest tests/test_compliance_freeze.py -v
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException


@pytest.mark.asyncio
async def test_no_freeze_window_passes():
    from app.compliance.freeze import require_no_active_freeze
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    user = MagicMock(roles=[])

    # Should not raise
    await require_no_active_freeze(justification=None, db=db, current_user=user)


@pytest.mark.asyncio
async def test_active_freeze_blocks_normal_user():
    from app.compliance.freeze import require_no_active_freeze
    from app.models.compliance import ChangeFreezeWindow

    now = datetime.now(timezone.utc)
    freeze = MagicMock(spec=ChangeFreezeWindow)
    freeze.start_at = now - timedelta(hours=1)
    freeze.end_at = now + timedelta(hours=1)
    freeze.reason = "Year-end freeze"
    freeze.id = "test-id"
    freeze.emergency_bypass_role = "emergency_bypass"

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: freeze))
    user = MagicMock(roles=["viewer"])

    with pytest.raises(HTTPException) as exc_info:
        await require_no_active_freeze(justification=None, db=db, current_user=user)
    assert exc_info.value.status_code == 423
    assert "change_freeze_active" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_active_freeze_bypass_without_justification_raises_422():
    from app.compliance.freeze import require_no_active_freeze
    from app.models.compliance import ChangeFreezeWindow

    now = datetime.now(timezone.utc)
    freeze = MagicMock(spec=ChangeFreezeWindow)
    freeze.start_at = now - timedelta(hours=1)
    freeze.end_at = now + timedelta(hours=1)
    freeze.reason = "Year-end freeze"
    freeze.id = "test-id"
    freeze.emergency_bypass_role = "emergency_bypass"

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: freeze))
    user = MagicMock(roles=["emergency_bypass"])

    with pytest.raises(HTTPException) as exc_info:
        await require_no_active_freeze(justification=None, db=db, current_user=user)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_active_freeze_bypass_with_justification_passes():
    from app.compliance.freeze import require_no_active_freeze
    from app.models.compliance import ChangeFreezeWindow

    now = datetime.now(timezone.utc)
    freeze = MagicMock(spec=ChangeFreezeWindow)
    freeze.start_at = now - timedelta(hours=1)
    freeze.end_at = now + timedelta(hours=1)
    freeze.reason = "Year-end freeze"
    freeze.id = "test-id"
    freeze.emergency_bypass_role = "emergency_bypass"

    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: freeze))
    # log_freeze_bypass needs to be callable
    db.add = MagicMock()
    db.flush = AsyncMock()
    user = MagicMock(roles=["emergency_bypass"])

    with patch("app.compliance.freeze.log_freeze_bypass", new=AsyncMock()):
        # Should not raise
        await require_no_active_freeze(
            justification="Critical production incident P1-2026-0503",
            db=db,
            current_user=user,
        )
```

- [ ] **Step 2: Run tests — expect import failure (module not created yet)**

```bash
docker compose exec backend pytest tests/test_compliance_freeze.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'app.compliance'`.

- [ ] **Step 3: Create `backend/app/compliance/__init__.py`**

```python
# compliance package
```

- [ ] **Step 4: Create `backend/app/compliance/freeze.py`**

```python
import logging
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.compliance import ChangeFreezeWindow
from app.models.user import User
from app.routers import current_user

logger = logging.getLogger(__name__)


async def require_no_active_freeze(
    justification: str | None = Header(None, alias="X-Emergency-Justification"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(current_user),
) -> None:
    """
    FastAPI dependency injected into change-request approve and execute routes.
    Raises HTTP 423 if a freeze window is active and the caller does not have
    the emergency_bypass role. Requires X-Emergency-Justification header when
    bypassing. Logs bypass events to the audit log.
    """
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ChangeFreezeWindow).where(
            ChangeFreezeWindow.start_at <= now,
            ChangeFreezeWindow.end_at >= now,
        ).limit(1)
    )
    active = result.scalar_one_or_none()
    if active is None:
        return

    user_roles = getattr(current_user, "roles", []) or []
    if active.emergency_bypass_role not in user_roles:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "change_freeze_active",
                "message": f"Change freeze active until {active.end_at.isoformat()}",
                "reason": active.reason,
                "freeze_id": str(active.id),
            },
        )

    # User has bypass role — require justification header
    if not justification:
        raise HTTPException(
            status_code=422,
            detail="X-Emergency-Justification header required to bypass an active change freeze",
        )

    await log_freeze_bypass(db, current_user, active, justification)


async def log_freeze_bypass(
    db: AsyncSession,
    user: User,
    freeze: ChangeFreezeWindow,
    justification: str,
) -> None:
    """Record a freeze bypass event in the audit log."""
    try:
        from app.services.audit_service import record_event
        await record_event(
            db,
            user.organization_id,
            "change_freeze.bypassed",
            {
                "freeze_id": str(freeze.id),
                "reason": freeze.reason,
                "justification": justification,
            },
            actor_id=user.id,
        )
    except Exception as exc:
        logger.warning("Failed to log freeze bypass: %s", exc)
```

- [ ] **Step 5: Inject dependency into approve and execute routes**

In `backend/app/routers/change_requests.py`, add to imports:

```python
from app.compliance.freeze import require_no_active_freeze
```

Modify the `approve_change_request` route signature to add the dependency:

```python
@router.post("/{cr_id}/approve", response_model=ApprovalRead, dependencies=[Depends(require_no_active_freeze)])
async def approve_change_request(
```

Modify the `execute_change_request` route signature:

```python
@router.post("/{cr_id}/execute", response_model=ExecutionRunRead, dependencies=[Depends(require_no_active_freeze)])
async def execute_change_request(
```

- [ ] **Step 6: Run freeze tests — expect pass**

```bash
docker compose exec backend pytest tests/test_compliance_freeze.py -v
```

Expected:
```
PASSED tests/test_compliance_freeze.py::test_no_freeze_window_passes
PASSED tests/test_compliance_freeze.py::test_active_freeze_blocks_normal_user
PASSED tests/test_compliance_freeze.py::test_active_freeze_bypass_without_justification_raises_422
PASSED tests/test_compliance_freeze.py::test_active_freeze_bypass_with_justification_passes
```

- [ ] **Step 7: Integration smoke test — create a freeze, try to approve a CR, expect 423**

```bash
# Create a freeze window spanning now
curl -s -X POST http://localhost:8000/compliance/freeze-windows \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "Test freeze",
    "start_at": "2026-01-01T00:00:00Z",
    "end_at": "2099-12-31T23:59:59Z"
  }' | jq .id

# Attempt to approve any approved CR — should get 423
curl -s -X POST http://localhost:8000/change-requests/$CR_ID/approve \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decision": "approved", "comment": "test"}' | jq .
```

Expected: `{"detail": {"error": "change_freeze_active", ...}}` with HTTP 423.

- [ ] **Step 8: Commit**

```bash
git add backend/app/compliance/ \
        backend/app/routers/change_requests.py \
        backend/tests/test_compliance_freeze.py
git commit -m "feat(backend): add require_no_active_freeze dependency; inject into approve and execute routes"
```

---

## Task 6: Drift detection scheduler job

**Files:**
- Create: `backend/app/compliance/drift.py`
- Modify: `backend/app/services/scheduler_service.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_drift_detection.py`:

```python
"""
Tests for drift detection logic.
Run with: docker compose exec backend pytest tests/test_drift_detection.py -v
"""
import pytest
from unittest.mock import MagicMock


def test_detect_drift_returns_failing_non_skipped_controls():
    from app.compliance.drift import detect_drift

    baseline = MagicMock()
    baseline.config = {
        "overrides": {
            "5.2.9": "skip",   # PasswordAuthentication — skip for this baseline
        }
    }
    audit_result = {
        "controls": [
            {"id": "5.2.4", "status": "fail"},   # should be drifted
            {"id": "5.2.9", "status": "fail"},   # skipped in baseline — not drifted
            {"id": "3.1.1", "status": "pass"},   # passing — not drifted
            {"id": "1.6.1.1", "status": "fail"}, # should be drifted
        ]
    }
    drifted = detect_drift(audit_result, baseline)
    assert set(drifted) == {"5.2.4", "1.6.1.1"}
    assert "5.2.9" not in drifted


def test_detect_drift_no_overrides():
    from app.compliance.drift import detect_drift

    baseline = MagicMock()
    baseline.config = {}
    audit_result = {
        "controls": [
            {"id": "3.1.1", "status": "fail"},
            {"id": "3.1.2", "status": "pass"},
        ]
    }
    drifted = detect_drift(audit_result, baseline)
    assert drifted == ["3.1.1"]


def test_detect_drift_all_pass():
    from app.compliance.drift import detect_drift

    baseline = MagicMock()
    baseline.config = {}
    audit_result = {
        "controls": [
            {"id": "3.1.1", "status": "pass"},
            {"id": "3.1.2", "status": "pass"},
        ]
    }
    drifted = detect_drift(audit_result, baseline)
    assert drifted == []


def test_write_cis_score_to_metadata_keeps_history():
    from app.compliance.drift import _build_updated_metadata

    existing = {
        "cis_compliance": {
            "latest": {"score": 0.6, "level": 1, "collected_at": "2026-04-01T00:00:00Z"},
            "history": [
                {"score": 0.5, "level": 1, "collected_at": "2026-03-01T00:00:00Z"},
            ],
        }
    }
    audit_result = {"score": 0.74, "level": 2, "collected_at": "2026-05-03T00:00:00Z", "controls": []}
    updated = _build_updated_metadata(existing, audit_result)
    assert updated["cis_compliance"]["latest"]["score"] == 0.74
    assert len(updated["cis_compliance"]["history"]) == 2
    assert updated["cis_compliance"]["history"][-1]["score"] == 0.6


def test_write_cis_score_caps_history_at_30():
    from app.compliance.drift import _build_updated_metadata

    history = [{"score": 0.5, "level": 1, "collected_at": "t"} for _ in range(30)]
    existing = {
        "cis_compliance": {
            "latest": {"score": 0.6, "level": 1, "collected_at": "t"},
            "history": history,
        }
    }
    audit_result = {"score": 0.9, "level": 1, "collected_at": "t", "controls": []}
    updated = _build_updated_metadata(existing, audit_result)
    assert len(updated["cis_compliance"]["history"]) == 30
```

- [ ] **Step 2: Run tests — expect import failure**

```bash
docker compose exec backend pytest tests/test_drift_detection.py -v 2>&1 | head -10
```

Expected: `ImportError: cannot import name 'detect_drift' from 'app.compliance.drift'`.

- [ ] **Step 3: Create `backend/app/compliance/drift.py`**

```python
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import ComplianceBaseline
from app.models.asset import Asset
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel

logger = logging.getLogger(__name__)


def detect_drift(audit_result: dict, baseline: ComplianceBaseline) -> list[str]:
    """Return list of failing control IDs not marked 'skip' in baseline overrides."""
    overrides = baseline.config.get("overrides", {})
    return [
        c["id"]
        for c in audit_result.get("controls", [])
        if c["status"] == "fail" and overrides.get(c["id"]) != "skip"
    ]


def _build_updated_metadata(existing_metadata: dict, audit_result: dict) -> dict:
    """Pure function — builds updated metadata dict with cis_compliance key updated."""
    metadata = dict(existing_metadata or {})
    prev = metadata.get("cis_compliance", {})
    history = list(prev.get("history", []))

    # Push the old latest into history
    if "latest" in prev:
        history.append({
            "score": prev["latest"]["score"],
            "level": prev["latest"]["level"],
            "collected_at": prev["latest"]["collected_at"],
        })

    # Keep last 30 snapshots
    history = history[-30:]

    metadata["cis_compliance"] = {
        "latest": audit_result,
        "history": history,
    }
    return metadata


async def write_cis_score_to_metadata(
    db: AsyncSession, asset_id: uuid.UUID, audit_result: dict
) -> None:
    """Persist audit_result into asset.metadata under the cis_compliance key."""
    asset = await db.get(Asset, asset_id)
    if not asset:
        logger.warning("write_cis_score_to_metadata: asset %s not found", asset_id)
        return
    asset.metadata = _build_updated_metadata(asset.metadata or {}, audit_result)
    await db.flush()


async def _resolve_scope(
    db: AsyncSession, scope_type: str, scope_value: str, org_id: uuid.UUID
) -> list[Asset]:
    """Return assets matching the baseline scope (asset UUID or tag string)."""
    if scope_type == "asset":
        try:
            asset_id = uuid.UUID(scope_value)
        except ValueError:
            return []
        result = await db.execute(
            select(Asset).where(Asset.id == asset_id, Asset.organization_id == org_id)
        )
        asset = result.scalar_one_or_none()
        return [asset] if asset else []
    elif scope_type == "tag":
        result = await db.execute(
            select(Asset).where(
                Asset.organization_id == org_id,
                Asset.tags.contains([scope_value]),
            )
        )
        return list(result.scalars().all())
    return []


async def _create_drift_change_request(
    db: AsyncSession,
    asset: Asset,
    baseline: ComplianceBaseline,
    drifted_controls: list[str],
    audit_result: dict,
) -> ChangeRequest:
    """Create a DRAFT enforce_cis_benchmark change request for drifted controls."""
    cr = ChangeRequest(
        organization_id=baseline.organization_id,
        requester_id=None,  # system-generated
        title=f"[Drift] {asset.name or asset.id}: {len(drifted_controls)} controls drifted from '{baseline.name}'",
        description=(
            f"Drift detection found {len(drifted_controls)} controls failing baseline '{baseline.name}' "
            f"(CIS Level {baseline.cis_level}, {baseline.os_family}). "
            f"Drifted control IDs: {', '.join(drifted_controls[:10])}{'...' if len(drifted_controls) > 10 else ''}."
        ),
        change_type=ChangeType.enforce_cis_benchmark,
        target_asset_ids=[str(asset.id)],
        status=ChangeRequestStatus.draft,
        risk_level=RiskLevel.medium,
        parameters={
            "level": baseline.cis_level,
            "os_family": baseline.os_family,
            "asset_ids": [str(asset.id)],
            "dry_run": False,
            "baseline_id": str(baseline.id),
            "drifted_controls": drifted_controls,
            "before_score": audit_result.get("score"),
        },
    )
    db.add(cr)
    await db.flush()
    return cr


async def run_drift_detection(db: AsyncSession) -> None:
    """
    Weekly job: for each ComplianceBaseline, fetch matching assets, dispatch
    audit_cis_compliance, compare to baseline, and create DRAFT change requests
    for drifted hosts. If baseline.auto_execute is True, also approves and executes.
    """
    result = await db.execute(select(ComplianceBaseline))
    baselines = result.scalars().all()

    for baseline in baselines:
        try:
            assets = await _resolve_scope(db, baseline.scope_type, baseline.scope_value, baseline.organization_id)
        except Exception as exc:
            logger.error("Drift detection: failed to resolve scope for baseline %s: %s", baseline.id, exc)
            continue

        for asset in assets:
            try:
                from app.services.agent_job_service import dispatch_agent_job
                job_result = await dispatch_agent_job(
                    db=db,
                    asset_id=asset.id,
                    command="audit_cis_compliance",
                    parameters={"level": baseline.cis_level, "os_family": baseline.os_family},
                )
                await write_cis_score_to_metadata(db, asset.id, job_result)

                drifted = detect_drift(job_result, baseline)
                if not drifted:
                    logger.info("Drift detection: asset %s is compliant with baseline %s", asset.id, baseline.id)
                    continue

                logger.info(
                    "Drift detected: asset %s has %d drifted controls from baseline %s",
                    asset.id, len(drifted), baseline.id,
                )
                cr = await _create_drift_change_request(db, asset, baseline, drifted, job_result)

                if baseline.auto_execute:
                    from app.workflows import runner as workflow_runner
                    from app.models.execution_run import ExecutionRun, ExecutionStatus
                    cr.status = ChangeRequestStatus.approved
                    run = ExecutionRun(
                        change_request_id=cr.id,
                        workflow_id=f"wf-drift-{cr.id}",
                        status=ExecutionStatus.pending,
                    )
                    db.add(run)
                    await db.flush()
                    await workflow_runner.run_workflow(db, run.id)

            except Exception as exc:
                logger.error("Drift detection failed for asset %s: %s", asset.id, exc)

    await db.commit()
```

- [ ] **Step 4: Add drift detection cron job to `scheduler_service.py`**

In `backend/app/services/scheduler_service.py`, add to the `start()` function, after `logger.info(f"Loaded {loaded} scheduled ingest jobs")`:

```python
    # Weekly drift detection — every Sunday at 02:00 UTC
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        _run_drift_detection_job,
        trigger=CronTrigger(day_of_week="sun", hour=2, minute=0, timezone="UTC"),
        id="drift_detection_weekly",
        replace_existing=True,
    )
    logger.info("Registered weekly drift detection job (Sundays 02:00 UTC)")
```

Add the job function at the end of the file:

```python
async def _run_drift_detection_job():
    """APScheduler entrypoint for weekly drift detection."""
    if _db_factory is None:
        return
    async with _db_factory() as db:
        try:
            from app.compliance.drift import run_drift_detection
            await run_drift_detection(db)
        except Exception as e:
            logger.error(f"Drift detection job failed: {e}")
```

- [ ] **Step 5: Run drift tests — expect pass**

```bash
docker compose exec backend pytest tests/test_drift_detection.py -v
```

Expected:
```
PASSED tests/test_drift_detection.py::test_detect_drift_returns_failing_non_skipped_controls
PASSED tests/test_drift_detection.py::test_detect_drift_no_overrides
PASSED tests/test_drift_detection.py::test_detect_drift_all_pass
PASSED tests/test_drift_detection.py::test_write_cis_score_to_metadata_keeps_history
PASSED tests/test_drift_detection.py::test_write_cis_score_caps_history_at_30
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/compliance/drift.py \
        backend/app/compliance/__init__.py \
        backend/app/services/scheduler_service.py \
        backend/tests/test_drift_detection.py
git commit -m "feat(backend): drift detection — weekly cron job compares CIS audit to baseline, creates DRAFT CRs"
```

---

## Task 7: Change type definition JSON files

**Files:**
- Create: `backend/app/change_type_definitions/enforce_cis_benchmark.json`
- Create: `backend/app/change_type_definitions/collect_evidence.json`

- [ ] **Step 1: Check whether the directory exists**

```bash
docker compose exec backend ls /app/app/change_type_definitions/ 2>&1 || echo "directory does not exist"
```

If the directory does not exist, create it:

```bash
docker compose exec backend mkdir -p /app/app/change_type_definitions
```

- [ ] **Step 2: Create `enforce_cis_benchmark.json`**

Create `backend/app/change_type_definitions/enforce_cis_benchmark.json`:

```json
{
  "change_type": "enforce_cis_benchmark",
  "display_name": "Enforce CIS Benchmark",
  "description": "Audit each target host against the CIS Benchmark and remediate failing controls. Before/after scores are recorded in the change result.",
  "category": "compliance",
  "risk_guidance": "Medium risk. Remediation commands (sysctl, sshd, PAM, auditd, SELinux) modify running system configuration. Validate in staging before production.",
  "parameters": {
    "level": {
      "type": "integer",
      "enum": [1, 2],
      "description": "CIS Benchmark level. Level 1 covers filesystem, sysctl, and SSH. Level 2 adds PAM, auditd, and MAC (SELinux/AppArmor).",
      "required": true
    },
    "os_family": {
      "type": "string",
      "enum": ["rhel", "debian", "ubuntu"],
      "description": "OS family of target hosts.",
      "required": true
    },
    "asset_ids": {
      "type": "array",
      "items": { "type": "string", "format": "uuid" },
      "description": "UUIDs of target assets.",
      "required": true
    },
    "dry_run": {
      "type": "boolean",
      "description": "If true, audit only — do not apply remediation.",
      "default": false
    }
  },
  "agent_commands": ["audit_cis_compliance"],
  "remediation_commands": ["apply_sysctl_hardening", "harden_ssh", "configure_selinux", "configure_apparmor", "deploy_auditd_rules", "configure_pam"]
}
```

- [ ] **Step 3: Create `collect_evidence.json`**

Create `backend/app/change_type_definitions/collect_evidence.json`:

```json
{
  "change_type": "collect_evidence",
  "display_name": "Collect Audit Evidence",
  "description": "Gather config files, command outputs, and change logs from target hosts and package them as a structured evidence bundle for compliance audits (SOC2, PCI-DSS, ISO 27001).",
  "category": "compliance",
  "risk_guidance": "Read-only. No configuration changes are made to target hosts.",
  "parameters": {
    "framework": {
      "type": "string",
      "enum": ["soc2", "pci", "iso27001"],
      "description": "Compliance framework for which evidence is being collected.",
      "required": true
    },
    "control_id": {
      "type": "string",
      "description": "Control identifier within the framework (e.g. CC6.1 for SOC2).",
      "required": true
    },
    "asset_ids": {
      "type": "array",
      "items": { "type": "string", "format": "uuid" },
      "description": "UUIDs of target assets.",
      "required": true
    },
    "evidence_types": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": ["config_files", "command_outputs", "change_logs"]
      },
      "description": "Types of evidence to collect.",
      "default": ["config_files", "command_outputs", "change_logs"]
    }
  },
  "agent_commands": ["collect_evidence"]
}
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/change_type_definitions/enforce_cis_benchmark.json \
        backend/app/change_type_definitions/collect_evidence.json
git commit -m "feat(backend): add change type definitions for enforce_cis_benchmark and collect_evidence"
```

---

## Task 8: Frontend — Compliance tab and freeze banner

**Files:**
- Create: `frontend/src/pages/Compliance.tsx`
- Create: `frontend/src/components/FreezeAlert.tsx`
- Modify: `frontend/src/components/Layout.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create `frontend/src/components/FreezeAlert.tsx`**

```tsx
import { X } from "lucide-react";

interface FreezeAlertProps {
  reason: string;
  endAt: string;
  onDismiss: () => void;
}

export function FreezeAlert({ reason, endAt, onDismiss }: FreezeAlertProps) {
  const endDate = new Date(endAt).toLocaleString();
  return (
    <div className="bg-amber-500 text-amber-950 px-4 py-2 flex items-center justify-between gap-4 text-sm font-medium">
      <span>
        <strong>Change Freeze Active</strong> — Changes cannot be approved or executed until{" "}
        <strong>{endDate}</strong>. Reason: {reason}
      </span>
      <button
        onClick={onDismiss}
        className="flex-shrink-0 text-amber-900 hover:text-amber-950"
        aria-label="Dismiss freeze banner"
      >
        <X size={16} />
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Modify `frontend/src/components/Layout.tsx` to poll for active freeze**

Read the current `Layout.tsx` to find the correct insertion point, then add the freeze query and banner rendering at the top of the Layout component's return value, above the nav/sidebar.

Add to imports at the top of `Layout.tsx`:
```tsx
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { FreezeAlert } from "./FreezeAlert";
import { apiClient } from "../api/client";
```

Add inside the Layout component function, before the return:
```tsx
  const [freezeDismissed, setFreezeDismissed] = useState(false);
  const { data: activeFreeze } = useQuery({
    queryKey: ["active-freeze"],
    queryFn: () =>
      apiClient
        .get("/compliance/freeze-windows/active")
        .then((r) => r.data)
        .catch(() => null),
    refetchInterval: 60_000,
  });
```

Add at the very top of the returned JSX, before the existing wrapper div:
```tsx
  return (
    <>
      {activeFreeze && !freezeDismissed && (
        <FreezeAlert
          reason={activeFreeze.reason}
          endAt={activeFreeze.end_at}
          onDismiss={() => setFreezeDismissed(true)}
        />
      )}
      {/* existing Layout JSX below */}
```

- [ ] **Step 3: Create `frontend/src/pages/Compliance.tsx`**

```tsx
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, ShieldAlert, AlertTriangle, Download } from "lucide-react";
import { Link } from "react-router-dom";
import { apiClient } from "../api/client";
import { PageLoading } from "../components/LoadingSpinner";
import { formatDistanceToNow } from "date-fns";

interface CISLatest {
  score: number;
  level: number;
  collected_at: string;
  controls: ControlResult[];
}

interface ControlResult {
  id: string;
  title: string;
  section: string;
  status: "pass" | "fail" | "skip";
  expected: string;
  actual: string;
}

interface AssetWithCIS {
  id: string;
  name: string;
  cis_compliance?: { latest?: CISLatest };
}

function ScoreBadge({ score }: { score: number }) {
  const pct = Math.round(score * 100);
  const color =
    score >= 0.8 ? "bg-green-100 text-green-800" :
    score >= 0.6 ? "bg-yellow-100 text-yellow-800" :
    "bg-red-100 text-red-800";
  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold ${color}`}>
      {pct}%
    </span>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === "pass") return <ShieldCheck size={16} className="text-green-600 inline" />;
  if (status === "fail") return <ShieldAlert size={16} className="text-red-500 inline" />;
  return <AlertTriangle size={16} className="text-slate-400 inline" />;
}

function ControlBreakdownTable({ controls }: { controls: ControlResult[] }) {
  return (
    <table className="w-full text-sm border-t border-slate-200 mt-2">
      <thead>
        <tr className="text-left text-xs text-slate-500 uppercase tracking-wide">
          <th className="py-1 pr-3 font-medium">ID</th>
          <th className="py-1 pr-3 font-medium">Section</th>
          <th className="py-1 pr-3 font-medium">Title</th>
          <th className="py-1 pr-3 font-medium">Status</th>
          <th className="py-1 pr-3 font-medium">Expected</th>
          <th className="py-1 font-medium">Actual</th>
        </tr>
      </thead>
      <tbody>
        {controls.map((c) => (
          <tr key={c.id} className="border-t border-slate-100 hover:bg-slate-50">
            <td className="py-1 pr-3 font-mono text-xs text-slate-600">{c.id}</td>
            <td className="py-1 pr-3 text-xs text-slate-500 capitalize">{c.section}</td>
            <td className="py-1 pr-3 text-slate-700">{c.title}</td>
            <td className="py-1 pr-3"><StatusIcon status={c.status} /> {c.status}</td>
            <td className="py-1 pr-3 font-mono text-xs text-slate-500 max-w-[200px] truncate">{c.expected}</td>
            <td className="py-1 font-mono text-xs text-slate-500 max-w-[200px] truncate">{c.actual}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Compliance() {
  const [expandedAsset, setExpandedAsset] = useState<string | null>(null);

  const { data: assets, isLoading } = useQuery<AssetWithCIS[]>({
    queryKey: ["assets-with-cis"],
    queryFn: () =>
      apiClient.get("/assets").then((r) =>
        r.data.filter((a: AssetWithCIS) => a.cis_compliance?.latest)
      ),
  });

  const { data: driftAlerts } = useQuery({
    queryKey: ["drift-alerts"],
    queryFn: () => apiClient.get("/compliance/drift-alerts").then((r) => r.data),
  });

  if (isLoading) return <PageLoading />;

  const audited = assets ?? [];
  const drifted = driftAlerts ?? [];

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Compliance</h1>
          <p className="text-slate-500 mt-1 text-sm">CIS Benchmark scores and drift alerts across your fleet</p>
        </div>
        <Link
          to="/change-requests/new?type=enforce_cis_benchmark"
          className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition-colors"
        >
          <ShieldCheck size={16} />
          Run CIS Audit
        </Link>
      </div>

      {drifted.length > 0 && (
        <div className="mb-6 bg-yellow-50 border border-yellow-200 rounded-lg p-4">
          <div className="flex items-center gap-2 text-yellow-800 font-semibold mb-2">
            <AlertTriangle size={18} />
            {drifted.length} open drift {drifted.length === 1 ? "alert" : "alerts"}
          </div>
          <ul className="space-y-1">
            {drifted.slice(0, 5).map((alert: any) => (
              <li key={alert.change_request_id} className="text-sm text-yellow-700">
                <Link
                  to={`/change-requests/${alert.change_request_id}`}
                  className="hover:underline font-medium"
                >
                  {alert.title}
                </Link>
                {" — "}
                <span className="text-yellow-600">
                  {formatDistanceToNow(new Date(alert.created_at), { addSuffix: true })}
                </span>
              </li>
            ))}
          </ul>
          {drifted.length > 5 && (
            <p className="text-xs text-yellow-600 mt-1">
              +{drifted.length - 5} more — <Link to="/change-requests?change_type=enforce_cis_benchmark&status=draft" className="underline">view all</Link>
            </p>
          )}
        </div>
      )}

      {audited.length === 0 ? (
        <div className="text-center text-slate-500 py-16">
          <ShieldCheck size={40} className="mx-auto mb-3 text-slate-300" />
          <p className="text-lg font-medium text-slate-600">No CIS audit data yet</p>
          <p className="text-sm mt-1">Run a CIS Audit change request to see compliance scores here.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {audited.map((asset) => {
            const latest = asset.cis_compliance!.latest!;
            const isExpanded = expandedAsset === asset.id;
            return (
              <div key={asset.id} className="bg-white rounded-lg border border-slate-200 overflow-hidden">
                <button
                  className="w-full flex items-center justify-between px-5 py-4 hover:bg-slate-50 transition-colors"
                  onClick={() => setExpandedAsset(isExpanded ? null : asset.id)}
                >
                  <div className="flex items-center gap-3">
                    <ScoreBadge score={latest.score} />
                    <span className="font-medium text-slate-800">{asset.name || asset.id}</span>
                    <span className="text-xs text-slate-400">
                      Level {latest.level} · audited {formatDistanceToNow(new Date(latest.collected_at), { addSuffix: true })}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <a
                      href={`/compliance/evidence-collection?asset_id=${asset.id}`}
                      onClick={(e) => e.stopPropagation()}
                      className="flex items-center gap-1 text-xs text-indigo-600 hover:text-indigo-800"
                    >
                      <Download size={13} />
                      Evidence
                    </a>
                    <span className="text-slate-400 text-sm">{isExpanded ? "▲" : "▼"}</span>
                  </div>
                </button>
                {isExpanded && (
                  <div className="px-5 pb-4">
                    <ControlBreakdownTable controls={latest.controls} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Add `/compliance` route and nav item to `App.tsx`**

In `frontend/src/App.tsx`, add the import:

```tsx
import { Compliance } from "./pages/Compliance";
```

Add the route inside the router (alongside the other page routes):

```tsx
<Route path="/compliance" element={<Compliance />} />
```

Add the Compliance nav link in the sidebar/nav section (find the section with the other nav links and add):

```tsx
<NavLink
  to="/compliance"
  className={({ isActive }) =>
    `flex items-center gap-2 px-3 py-2 rounded-md text-sm font-medium transition-colors ${
      isActive ? "bg-indigo-50 text-indigo-700" : "text-slate-600 hover:text-slate-900 hover:bg-slate-100"
    }`
  }
>
  <ShieldCheck size={16} />
  Compliance
</NavLink>
```

Add `ShieldCheck` to the lucide-react import if not already present.

- [ ] **Step 5: Rebuild and verify in browser**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Open `http://localhost:3000/compliance`. Verify:
1. Page loads with header "Compliance" and "Run CIS Audit" button.
2. If no CIS audit data exists yet, the empty-state illustration appears.
3. Nav sidebar shows "Compliance" link.
4. Create a test freeze window via the API, reload — amber freeze banner appears at the top.
5. Dismiss button hides the banner for the session.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Compliance.tsx \
        frontend/src/components/FreezeAlert.tsx \
        frontend/src/components/Layout.tsx \
        frontend/src/App.tsx
git commit -m "feat(frontend): add Compliance page with CIS score cards, drift alerts, and freeze banner"
```

---

## Self-Review Checklist

**Spec coverage:**

- Section 1 (CIS audit agent command): Tasks 1 and 2
- Section 2 (drift detection + auto-remediation): Task 6 (`drift.py`, scheduler)
- Section 3 (evidence collection): Tasks 1 (agent), 4 (API endpoint + ZIP)
- Section 4 (change freeze enforcement): Tasks 3 (model/migration), 5 (freeze dependency + route injection)
- Section 5 (CIS results in asset metadata): Task 6 (`write_cis_score_to_metadata`)
- Frontend (Compliance tab, freeze banner): Task 8

**TDD compliance:**

- Task 1: `compliance_test.go` written before `compliance_linux.go`
- Task 5: `test_compliance_freeze.py` written before `freeze.py`
- Task 6: `test_drift_detection.py` written before `drift.py`

**Placeholder scan:** No TBDs, TODOs, or vague "implement later" steps. All code is complete.

**Type consistency:**

- `ControlResult` exported from `compliance.go` and used in test file's type assertion
- `ComplianceBaselineCreate` validates `scope_type`, `cis_level`, and `os_family` with `Literal` constraints
- `ChangeFreezeWindowCreate` Pydantic validator enforces `end_at > start_at`
- `require_no_active_freeze` uses `current_user` from `app.routers` (same pattern as all other routes)
- `run_drift_detection` matches the APScheduler job signature (no args — uses `_db_factory` closure)

**Migration chain:** 015 → 016 → 017. Both migrations include `downgrade()`.
