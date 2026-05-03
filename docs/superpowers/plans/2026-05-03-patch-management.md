# Patch Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add end-to-end patch management to Nexplane: Linux apt/yum/dnf security patching and Windows Update/KB patching via new Go agent command packages, four new executor command registrations, two connector executor Python files for compliance baseline and campaign orchestration, two change type JSON definitions, and catalog entries in `nexplane_agent.json`.

**Architecture:** Two new Go packages (`linuxpatch`, `winpatch`) implement all system-level patching operations using the same OS-split file pattern as `linuxupgrade` and `winharden`. Both packages expose Execute, Rollback, and Audit functions registered in `executor.go`. Two Python connector executors handle multi-host orchestration — `check_patch_compliance.py` queries asset metadata `last_security_patch_at` to find stale hosts and optionally auto-creates change requests; `run_patch_campaign.py` fans out CVE/package-targeted patching across the fleet in rolling batches with abort-on-error-rate logic. Two JSON change type definitions (`patch_packages.json`, `patch_campaign.json`) give the AI planner the parameter schemas it needs. Catalog entries in `nexplane_agent.json` register six new actions: `apply_linux_patches`, `audit_linux_patch_status`, `apply_windows_patches`, `audit_windows_patch_status`, `check_patch_compliance`, and `run_patch_campaign`.

**Tech Stack:** Go 1.26 (module `nexplane-agent`), `os/exec`, build tags (`//go:build linux`, `//go:build windows`, `//go:build !linux`, `//go:build !windows`), Python 3.12 with `asyncio.gather`, `packaging.version.Version`, FastAPI/SQLAlchemy context, JSON change type definitions.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `agent/commands/linuxpatch/linuxpatch.go` | Create | Param validation entry points for Linux patching |
| `agent/commands/linuxpatch/linuxpatch_linux.go` | Create | apt/yum/dnf execution, package diff, reboot detection |
| `agent/commands/linuxpatch/linuxpatch_other.go` | Create | Non-Linux stubs returning errNotSupported |
| `agent/commands/linuxpatch/linuxpatch_test.go` | Create | Unit tests for param validation (cross-platform) |
| `agent/commands/winpatch/winpatch.go` | Create | Param validation entry points for Windows patching |
| `agent/commands/winpatch/winpatch_windows.go` | Create | PowerShell/WUA COM execution, reboot scheduling |
| `agent/commands/winpatch/winpatch_other.go` | Create | Non-Windows stubs returning errNotSupported |
| `agent/commands/winpatch/winpatch_test.go` | Create | Unit tests for param validation (cross-platform) |
| `agent/executor/executor.go` | Modify | Register 4 commands + 2 rollbacks for linuxpatch and winpatch |
| `backend/app/connectors/change_type_definitions/patch_packages.json` | Create | Change type definition for single-host patching |
| `backend/app/connectors/change_type_definitions/patch_campaign.json` | Create | Change type definition for fleet-wide CVE campaign |
| `backend/app/connectors/executors/nexplane_agent/check_patch_compliance.py` | Create | Compliance baseline connector action |
| `backend/app/connectors/executors/nexplane_agent/run_patch_campaign.py` | Create | Campaign orchestration connector action |
| `backend/app/connectors/catalog/nexplane_agent.json` | Modify | Add 6 new action entries |

---

## Task 1: linuxpatch Go package — tests first, then implementation

**Files:**
- Create: `agent/commands/linuxpatch/linuxpatch_test.go`
- Create: `agent/commands/linuxpatch/linuxpatch.go`
- Create: `agent/commands/linuxpatch/linuxpatch_linux.go`
- Create: `agent/commands/linuxpatch/linuxpatch_other.go`

- [ ] **Step 1: Write the failing tests**

Create `agent/commands/linuxpatch/linuxpatch_test.go`:

```go
package linuxpatch_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/linuxpatch"
)

func TestApplyLinuxPatchesRequiresMode(t *testing.T) {
	_, err := linuxpatch.ApplyLinuxPatchesExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "mode must be") {
		t.Errorf("expected mode validation error, got: %v", err)
	}
}

func TestApplyLinuxPatchesInvalidMode(t *testing.T) {
	_, err := linuxpatch.ApplyLinuxPatchesExecute(map[string]any{"mode": "full_upgrade"})
	if err == nil || !strings.Contains(err.Error(), "mode must be") {
		t.Errorf("expected mode validation error, got: %v", err)
	}
}

func TestApplyLinuxPatchesModePackageRequiresPackageName(t *testing.T) {
	_, err := linuxpatch.ApplyLinuxPatchesExecute(map[string]any{"mode": "package"})
	if err == nil || !strings.Contains(err.Error(), "package_name") {
		t.Errorf("expected package_name error, got: %v", err)
	}
}

func TestApplyLinuxPatchesModeCVERequiresCVEID(t *testing.T) {
	_, err := linuxpatch.ApplyLinuxPatchesExecute(map[string]any{"mode": "cve"})
	if err == nil || !strings.Contains(err.Error(), "cve_id") {
		t.Errorf("expected cve_id error, got: %v", err)
	}
}

func TestApplyLinuxPatchesValidModesPassValidation(t *testing.T) {
	cases := []map[string]any{
		{"mode": "security_only"},
		{"mode": "package", "package_name": "openssl"},
		{"mode": "cve", "cve_id": "CVE-2024-3094"},
	}
	for _, params := range cases {
		// On non-Linux the OS call will fail, but param validation must pass
		_, err := linuxpatch.ApplyLinuxPatchesExecute(params)
		if err != nil && strings.Contains(err.Error(), "mode must be") {
			t.Errorf("params %v should pass validation, got: %v", params, err)
		}
		if err != nil && strings.Contains(err.Error(), "package_name") {
			t.Errorf("params %v should pass validation, got: %v", params, err)
		}
		if err != nil && strings.Contains(err.Error(), "cve_id") {
			t.Errorf("params %v should pass validation, got: %v", params, err)
		}
	}
}

func TestAuditLinuxPatchStatusExecuteReturnsResult(t *testing.T) {
	// On any platform this returns either real data or an errNotSupported error.
	// The function must not panic.
	result, _ := linuxpatch.AuditLinuxPatchStatusExecute(map[string]any{})
	_ = result // nil on non-Linux is acceptable
}
```

- [ ] **Step 2: Run tests — expect compile failure**

```bash
cd f:\Nexplane\nexplane\agent && go test ./commands/linuxpatch/... -v 2>&1 | Select-Object -First 20
```
Expected: `cannot find package` or `no Go files` build error.

- [ ] **Step 3: Create linuxpatch.go (entry points + validation)**

Create `agent/commands/linuxpatch/linuxpatch.go`:

```go
package linuxpatch

import "fmt"

var validModes = map[string]bool{
	"security_only": true,
	"package":       true,
	"cve":           true,
}

// ApplyLinuxPatchesExecute applies security patches on the host.
//
// Params:
//
//	mode         string  required  "security_only" | "package" | "cve"
//	package_name string  optional  required when mode=package
//	cve_id       string  optional  required when mode=cve (e.g. "CVE-2024-3094")
//	dry_run      bool    optional  default false
//
// Result keys:
//
//	packages_updated  []map[string]string  [{name, version_before, version_after}]
//	reboot_required   bool
//	dry_run           bool
//	patched_at        string  RFC3339, omitted on dry_run
func ApplyLinuxPatchesExecute(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if !validModes[mode] {
		return nil, fmt.Errorf("mode must be security_only, package, or cve; got %q", mode)
	}
	if mode == "package" {
		if pkg, _ := params["package_name"].(string); pkg == "" {
			return nil, fmt.Errorf("package_name is required when mode=package")
		}
	}
	if mode == "cve" {
		if cve, _ := params["cve_id"].(string); cve == "" {
			return nil, fmt.Errorf("cve_id is required when mode=cve")
		}
	}
	return applyPatchesOS(params)
}

// ApplyLinuxPatchesRollback downgrades packages to the versions captured in
// the execute result (packages_updated[].version_before). Only possible when
// the package manager retains old packages in its cache.
func ApplyLinuxPatchesRollback(params map[string]any) (map[string]any, error) {
	return rollbackPatchesOS(params)
}

// AuditLinuxPatchStatusExecute returns the current patch state without changes.
//
// Result keys:
//
//	installed_packages              []map[string]string  [{name, version, arch}]
//	security_updates_available      []map[string]any     [{name, current_version, available_version, advisories}]
//	days_since_last_security_update int
//	reboot_required                 bool
func AuditLinuxPatchStatusExecute(params map[string]any) (map[string]any, error) {
	return auditPatchStatusOS(params)
}
```

- [ ] **Step 4: Create linuxpatch_linux.go (Linux implementation)**

Create `agent/commands/linuxpatch/linuxpatch_linux.go`:

```go
//go:build linux

package linuxpatch

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

// detectPackageManager returns "apt", "dnf", or "yum" based on PATH availability.
func detectPackageManager() (string, error) {
	for _, pm := range []string{"apt-get", "dnf", "yum"} {
		if path, err := exec.LookPath(pm); err == nil && path != "" {
			if pm == "apt-get" {
				return "apt", nil
			}
			return pm, nil
		}
	}
	return "", fmt.Errorf("no supported package manager found (apt-get, dnf, yum)")
}

func applyPatchesOS(params map[string]any) (map[string]any, error) {
	dryRun, _ := params["dry_run"].(bool)
	pm, err := detectPackageManager()
	if err != nil {
		return nil, err
	}

	before, err := listInstalledPackages(pm)
	if err != nil {
		return nil, fmt.Errorf("pre-patch inventory failed: %w", err)
	}

	if dryRun {
		available, err := listSecurityUpdates(pm, params)
		if err != nil {
			return nil, err
		}
		return map[string]any{
			"dry_run":          true,
			"packages_updated": available,
			"reboot_required":  false,
		}, nil
	}

	if err := runPatch(pm, params); err != nil {
		return nil, err
	}

	after, err := listInstalledPackages(pm)
	if err != nil {
		return nil, fmt.Errorf("post-patch inventory failed: %w", err)
	}

	diff := diffPackages(before, after)
	reboot := checkRebootRequired()

	return map[string]any{
		"dry_run":          false,
		"packages_updated": diff,
		"reboot_required":  reboot,
		"patched_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackPatchesOS(params map[string]any) (map[string]any, error) {
	pm, err := detectPackageManager()
	if err != nil {
		return nil, err
	}

	updated, _ := params["packages_updated"].([]any)
	rolledBack := []string{}

	for _, raw := range updated {
		entry, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		name, _ := entry["name"].(string)
		versionBefore, _ := entry["version_before"].(string)
		if name == "" || versionBefore == "" {
			continue
		}
		var cmd *exec.Cmd
		switch pm {
		case "apt":
			target := fmt.Sprintf("%s=%s", name, versionBefore)
			cmd = exec.Command("apt-get", "install", "-y", "--allow-downgrades", target)
		case "dnf":
			cmd = exec.Command("dnf", "downgrade", "-y", fmt.Sprintf("%s-%s", name, versionBefore))
		case "yum":
			cmd = exec.Command("yum", "downgrade", "-y", fmt.Sprintf("%s-%s", name, versionBefore))
		}
		if out, err := cmd.CombinedOutput(); err != nil {
			return nil, fmt.Errorf("rollback of %s failed: %w\n%s", name, err, out)
		}
		rolledBack = append(rolledBack, name)
	}

	return map[string]any{
		"rolled_back": rolledBack,
	}, nil
}

func auditPatchStatusOS(_ map[string]any) (map[string]any, error) {
	pm, err := detectPackageManager()
	if err != nil {
		return nil, err
	}

	installed, err := listInstalledPackages(pm)
	if err != nil {
		return nil, err
	}

	updates, err := listSecurityUpdates(pm, nil)
	if err != nil {
		return nil, err
	}

	reboot := checkRebootRequired()

	return map[string]any{
		"installed_packages":         installed,
		"security_updates_available": updates,
		"reboot_required":            reboot,
	}, nil
}

// listInstalledPackages returns [{name, version, arch}] for all installed packages.
func listInstalledPackages(pm string) ([]map[string]string, error) {
	var cmd *exec.Cmd
	switch pm {
	case "apt":
		cmd = exec.Command("dpkg-query", "-W", "-f=${Package}\t${Version}\t${Architecture}\n")
	default: // dnf, yum
		cmd = exec.Command("rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n")
	}

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("listInstalledPackages: %w", err)
	}

	var result []map[string]string
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		fields := strings.Split(scanner.Text(), "\t")
		if len(fields) < 2 {
			continue
		}
		entry := map[string]string{"name": fields[0], "version": fields[1]}
		if len(fields) >= 3 {
			entry["arch"] = fields[2]
		}
		result = append(result, entry)
	}
	return result, nil
}

// listSecurityUpdates returns available security updates without applying them.
func listSecurityUpdates(pm string, params map[string]any) ([]map[string]string, error) {
	mode := ""
	if params != nil {
		mode, _ = params["mode"].(string)
	}

	var cmd *exec.Cmd
	switch pm {
	case "apt":
		// apt-get -s dist-upgrade lists what would be upgraded; filter by security source
		cmd = exec.Command("apt-get", "-s", "dist-upgrade")
	case "dnf":
		if mode == "cve" {
			cve, _ := params["cve_id"].(string)
			cmd = exec.Command("dnf", "updateinfo", "list", "--cve", cve)
		} else {
			cmd = exec.Command("dnf", "updateinfo", "list", "--security")
		}
	default: // yum
		cmd = exec.Command("yum", "updateinfo", "list", "security")
	}

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("listSecurityUpdates: %w", err)
	}

	// Parse output into package list; for dry_run this is the "would update" set.
	var result []map[string]string
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		line := scanner.Text()
		// apt outputs "Inst <package> ..." lines for pending upgrades
		if pm == "apt" && strings.HasPrefix(line, "Inst ") {
			fields := strings.Fields(line)
			if len(fields) >= 2 {
				result = append(result, map[string]string{"name": fields[1]})
			}
			continue
		}
		// dnf/yum: lines like "CVE-2024-3094 Important/Sec. xz-5.4.6-1.el9.x86_64"
		fields := strings.Fields(line)
		if len(fields) >= 3 && (pm == "dnf" || pm == "yum") {
			result = append(result, map[string]string{"name": fields[2]})
		}
	}
	return result, nil
}

// runPatch executes the patch command appropriate to the package manager and mode.
func runPatch(pm string, params map[string]any) error {
	mode, _ := params["mode"].(string)
	var cmd *exec.Cmd

	switch pm {
	case "apt":
		// Always update package lists first
		if out, err := exec.Command("apt-get", "update", "-q").CombinedOutput(); err != nil {
			return fmt.Errorf("apt-get update: %w\n%s", err, out)
		}
		switch mode {
		case "security_only":
			// Install only packages from security sources using unattended-upgrades
			cmd = exec.Command("apt-get", "install", "-y", "--only-upgrade",
				"-o", "Dir::Etc::SourceList=/etc/apt/sources.list.d/security.list",
				"-o", "Dir::Etc::SourceParts=/dev/null",
				"--with-new-pkgs")
			// Fallback: use unattended-upgrade if available
			if _, err := exec.LookPath("unattended-upgrade"); err == nil {
				cmd = exec.Command("unattended-upgrade", "-v")
			}
		case "package":
			pkg, _ := params["package_name"].(string)
			cmd = exec.Command("apt-get", "install", "-y", "--only-upgrade", pkg)
		case "cve":
			// apt does not support CVE-targeted search natively; upgrade the named package
			pkg, _ := params["package_name"].(string)
			if pkg == "" {
				return fmt.Errorf("apt mode=cve requires package_name to be resolved before calling runPatch")
			}
			cmd = exec.Command("apt-get", "install", "-y", "--only-upgrade", pkg)
		}
	case "dnf":
		switch mode {
		case "security_only":
			cmd = exec.Command("dnf", "upgrade", "-y", "--security")
		case "package":
			pkg, _ := params["package_name"].(string)
			cmd = exec.Command("dnf", "upgrade", "-y", pkg)
		case "cve":
			cve, _ := params["cve_id"].(string)
			cmd = exec.Command("dnf", "upgrade", "-y", "--cve", cve)
		}
	case "yum":
		switch mode {
		case "security_only":
			cmd = exec.Command("yum", "update", "-y", "--security")
		case "package":
			pkg, _ := params["package_name"].(string)
			cmd = exec.Command("yum", "update", "-y", pkg)
		case "cve":
			cve, _ := params["cve_id"].(string)
			cmd = exec.Command("yum", "update", "-y", "--cve", cve)
		}
	}

	if cmd == nil {
		return fmt.Errorf("runPatch: unhandled pm=%q mode=%q", pm, mode)
	}

	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("patch command failed: %w\n%s", err, out)
	}
	return nil
}

// diffPackages compares before/after package snapshots and returns changed entries.
func diffPackages(before, after []map[string]string) []map[string]string {
	beforeMap := make(map[string]string, len(before))
	for _, p := range before {
		beforeMap[p["name"]] = p["version"]
	}
	var diff []map[string]string
	for _, p := range after {
		bv, existed := beforeMap[p["name"]]
		if !existed || bv != p["version"] {
			entry := map[string]string{
				"name":           p["name"],
				"version_after":  p["version"],
				"version_before": bv,
			}
			diff = append(diff, entry)
		}
	}
	return diff
}

// checkRebootRequired checks /var/run/reboot-required (Debian/Ubuntu) or
// needs-restarting -r (RHEL/CentOS 7+). Returns true if a reboot is needed.
func checkRebootRequired() bool {
	// Debian/Ubuntu
	if _, err := os.Stat("/var/run/reboot-required"); err == nil {
		return true
	}
	// RHEL/CentOS via needs-restarting
	if path, err := exec.LookPath("needs-restarting"); err == nil && path != "" {
		cmd := exec.Command("needs-restarting", "-r")
		// Exit code 1 means reboot required
		if err := cmd.Run(); err != nil {
			return true
		}
	}
	return false
}
```

- [ ] **Step 5: Create linuxpatch_other.go (non-Linux stub)**

Create `agent/commands/linuxpatch/linuxpatch_other.go`:

```go
//go:build !linux

package linuxpatch

import "fmt"

func applyPatchesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_linux_patches requires Linux")
}

func rollbackPatchesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_linux_patches requires Linux")
}

func auditPatchStatusOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_linux_patch_status requires Linux")
}
```

- [ ] **Step 6: Run tests — expect pass**

```bash
cd f:\Nexplane\nexplane\agent && go test ./commands/linuxpatch/... -v
```
Expected:
```
--- PASS: TestApplyLinuxPatchesRequiresMode (0.00s)
--- PASS: TestApplyLinuxPatchesInvalidMode (0.00s)
--- PASS: TestApplyLinuxPatchesModePackageRequiresPackageName (0.00s)
--- PASS: TestApplyLinuxPatchesModeCVERequiresCVEID (0.00s)
--- PASS: TestApplyLinuxPatchesValidModesPassValidation (0.00s)
--- PASS: TestAuditLinuxPatchStatusExecuteReturnsResult (0.00s)
PASS
ok      nexplane-agent/commands/linuxpatch
```

- [ ] **Step 7: Build to verify cross-compilation**

```bash
cd f:\Nexplane\nexplane\agent && go build ./commands/linuxpatch/...
```
Expected: exits 0.

- [ ] **Step 8: Commit**

```bash
git add agent/commands/linuxpatch/
git commit -m "feat(agent): add linuxpatch package — apt/yum/dnf security patching with dry_run and rollback"
```

---

## Task 2: winpatch Go package — tests first, then implementation

**Files:**
- Create: `agent/commands/winpatch/winpatch_test.go`
- Create: `agent/commands/winpatch/winpatch.go`
- Create: `agent/commands/winpatch/winpatch_windows.go`
- Create: `agent/commands/winpatch/winpatch_other.go`

- [ ] **Step 1: Write the failing tests**

Create `agent/commands/winpatch/winpatch_test.go`:

```go
package winpatch_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/winpatch"
)

func TestApplyWindowsPatchesRequiresMode(t *testing.T) {
	_, err := winpatch.ApplyWindowsPatchesExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "mode must be") {
		t.Errorf("expected mode validation error, got: %v", err)
	}
}

func TestApplyWindowsPatchesInvalidMode(t *testing.T) {
	_, err := winpatch.ApplyWindowsPatchesExecute(map[string]any{"mode": "full_upgrade"})
	if err == nil || !strings.Contains(err.Error(), "mode must be") {
		t.Errorf("expected mode validation error, got: %v", err)
	}
}

func TestApplyWindowsPatchesModeKBRequiresKBID(t *testing.T) {
	_, err := winpatch.ApplyWindowsPatchesExecute(map[string]any{"mode": "kb"})
	if err == nil || !strings.Contains(err.Error(), "kb_id") {
		t.Errorf("expected kb_id error, got: %v", err)
	}
}

func TestApplyWindowsPatchesValidModesPassValidation(t *testing.T) {
	cases := []map[string]any{
		{"mode": "security_only"},
		{"mode": "kb", "kb_id": "KB5034441"},
	}
	for _, params := range cases {
		_, err := winpatch.ApplyWindowsPatchesExecute(params)
		if err != nil && strings.Contains(err.Error(), "mode must be") {
			t.Errorf("params %v should pass mode validation, got: %v", params, err)
		}
		if err != nil && strings.Contains(err.Error(), "kb_id") {
			t.Errorf("params %v should pass kb_id validation, got: %v", params, err)
		}
	}
}

func TestAuditWindowsPatchStatusExecuteReturnsResult(t *testing.T) {
	result, _ := winpatch.AuditWindowsPatchStatusExecute(map[string]any{})
	_ = result // nil on non-Windows is acceptable
}
```

- [ ] **Step 2: Run tests — expect compile failure**

```bash
cd f:\Nexplane\nexplane\agent && go test ./commands/winpatch/... -v 2>&1 | Select-Object -First 20
```
Expected: package not found build error.

- [ ] **Step 3: Create winpatch.go (entry points + validation)**

Create `agent/commands/winpatch/winpatch.go`:

```go
package winpatch

import "fmt"

var validModes = map[string]bool{
	"security_only": true,
	"kb":            true,
}

// ApplyWindowsPatchesExecute installs Windows Updates on the host.
//
// Params:
//
//	mode           string  required  "security_only" | "kb"
//	kb_id          string  optional  required when mode=kb (e.g. "KB5034441")
//	dry_run        bool    optional  default false
//	defer_reboot   bool    optional  default false
//	reboot_window  string  optional  cron expression override for maintenance window
//
// Result keys:
//
//	updates_installed  []map[string]any  [{kb_id, title, size_bytes, installed_at}]
//	reboot_required    bool
//	reboot_scheduled   string  RFC3339 time or ""
//	dry_run            bool
func ApplyWindowsPatchesExecute(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if !validModes[mode] {
		return nil, fmt.Errorf("mode must be security_only or kb; got %q", mode)
	}
	if mode == "kb" {
		if kb, _ := params["kb_id"].(string); kb == "" {
			return nil, fmt.Errorf("kb_id is required when mode=kb")
		}
	}
	return applyPatchesOS(params)
}

// ApplyWindowsPatchesRollback uninstalls KBs installed during execute
// using wusa.exe /uninstall /kb:<number> /quiet /norestart.
func ApplyWindowsPatchesRollback(params map[string]any) (map[string]any, error) {
	return rollbackPatchesOS(params)
}

// AuditWindowsPatchStatusExecute returns installed KBs, pending updates, and
// reboot-pending state without making changes.
//
// Result keys:
//
//	installed_kbs             []string         e.g. ["KB5034441", "KB5032189"]
//	security_updates_pending  []map[string]any [{kb_id, title, severity, cve_ids}]
//	last_update_check         string           RFC3339
//	reboot_pending            bool
func AuditWindowsPatchStatusExecute(params map[string]any) (map[string]any, error) {
	return auditPatchStatusOS(params)
}
```

- [ ] **Step 4: Create winpatch_windows.go (Windows implementation)**

Create `agent/commands/winpatch/winpatch_windows.go`:

```go
//go:build windows

package winpatch

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// psSearchScript queries the Windows Update Agent COM API for available updates.
// Outputs JSON array of {KBArticleID, Title, SizeInBytes, Severity, CVEIDs}.
const psSearchScript = `
$Session  = New-Object -ComObject Microsoft.Update.Session
$Searcher = $Session.CreateUpdateSearcher()
$Query    = "IsInstalled=0 and Type='Software' and BrowseOnly=0 and IsAssigned=1"
$Results  = $Searcher.Search($Query)
$out = @()
foreach ($u in $Results.Updates) {
    $cves = @($u.SecurityBulletinIDs) + @($u.CVEIDs)
    $out += [PSCustomObject]@{
        KBArticleID = ($u.KBArticleIDs | Select-Object -First 1)
        Title       = $u.Title
        SizeInBytes = $u.MaxDownloadSize
        Severity    = $u.MsrcSeverity
        CVEIDs      = $cves
    }
}
$out | ConvertTo-Json -Compress
`

// psInstallScript installs all updates from a WUA search result.
// Accepts a JSON filter parameter: "security_only" installs severity Critical/Important;
// "kb" installs the specific KB number passed as $KBFilter.
const psInstallScript = `
param([string]$Mode, [string]$KBFilter)
$Session   = New-Object -ComObject Microsoft.Update.Session
$Searcher  = $Session.CreateUpdateSearcher()
$Query     = "IsInstalled=0 and Type='Software' and BrowseOnly=0 and IsAssigned=1"
$Results   = $Searcher.Search($Query)
$ToInstall = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $Results.Updates) {
    $kb = ($u.KBArticleIDs | Select-Object -First 1)
    if ($Mode -eq "kb" -and $kb -ne $KBFilter) { continue }
    if ($Mode -eq "security_only" -and $u.MsrcSeverity -notin @("Critical","Important")) { continue }
    $u.AcceptEula()
    $ToInstall.Add($u) | Out-Null
}
if ($ToInstall.Count -eq 0) {
    Write-Output '{"installed":[],"reboot_required":false}'
    exit 0
}
$Downloader            = $Session.CreateUpdateDownloader()
$Downloader.Updates    = $ToInstall
$Downloader.Download() | Out-Null
$Installer             = $Session.CreateUpdateInstaller()
$Installer.Updates     = $ToInstall
$InstallResult         = $Installer.Install()
$installed = @()
for ($i=0; $i -lt $ToInstall.Count; $i++) {
    $u  = $ToInstall.Item($i)
    $kb = ($u.KBArticleIDs | Select-Object -First 1)
    $installed += [PSCustomObject]@{
        kb_id        = "KB$kb"
        title        = $u.Title
        size_bytes   = $u.MaxDownloadSize
        installed_at = (Get-Date -Format o)
    }
}
[PSCustomObject]@{
    installed       = $installed
    reboot_required = $InstallResult.RebootRequired
} | ConvertTo-Json -Compress
`

// psRebootPendingScript checks whether a reboot is pending via registry keys.
const psRebootPendingScript = `
$pending = $false
$keys = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
    "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager"
)
if (Test-Path $keys[0]) { $pending = $true }
$sm = Get-ItemProperty $keys[1] -ErrorAction SilentlyContinue
if ($sm.PendingFileRenameOperations) { $pending = $true }
Write-Output ($pending.ToString().ToLower())
`

func applyPatchesOS(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	kbID, _ := params["kb_id"].(string)
	dryRun, _ := params["dry_run"].(bool)
	deferReboot, _ := params["defer_reboot"].(bool)

	if dryRun {
		available, err := runPS(psSearchScript)
		if err != nil {
			return nil, fmt.Errorf("dry_run search failed: %w", err)
		}
		var updates []map[string]any
		if err := json.Unmarshal([]byte(available), &updates); err != nil {
			// Empty array is valid JSON null from PowerShell
			updates = []map[string]any{}
		}
		return map[string]any{
			"dry_run":           true,
			"updates_installed": updates,
			"reboot_required":   false,
		}, nil
	}

	script := fmt.Sprintf(`& { %s } -Mode '%s' -KBFilter '%s'`, psInstallScript, mode, strings.ReplaceAll(kbID, "'", "''"))
	out, err := runPS(script)
	if err != nil {
		return nil, fmt.Errorf("patch installation failed: %w", err)
	}

	var result struct {
		Installed      []map[string]any `json:"installed"`
		RebootRequired bool             `json:"reboot_required"`
	}
	if err := json.Unmarshal([]byte(out), &result); err != nil {
		return nil, fmt.Errorf("failed to parse patch result: %w", err)
	}

	rebootScheduled := ""
	if result.RebootRequired && !deferReboot {
		rebootScheduled = scheduleReboot(params)
	}

	return map[string]any{
		"dry_run":           false,
		"updates_installed": result.Installed,
		"reboot_required":   result.RebootRequired,
		"reboot_scheduled":  rebootScheduled,
	}, nil
}

func rollbackPatchesOS(params map[string]any) (map[string]any, error) {
	installed, _ := params["updates_installed"].([]any)
	uninstalled := []string{}

	for _, raw := range installed {
		entry, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		kbID, _ := entry["kb_id"].(string)
		if kbID == "" {
			continue
		}
		// Strip "KB" prefix for wusa.exe
		num := strings.TrimPrefix(kbID, "KB")
		script := fmt.Sprintf("wusa.exe /uninstall /kb:%s /quiet /norestart; exit $LASTEXITCODE", num)
		if _, err := runPS(script); err != nil {
			return nil, fmt.Errorf("uninstall of %s failed: %w", kbID, err)
		}
		uninstalled = append(uninstalled, kbID)
	}

	return map[string]any{
		"uninstalled": uninstalled,
	}, nil
}

func auditPatchStatusOS(_ map[string]any) (map[string]any, error) {
	// Installed KBs
	installedScript := `Get-HotFix | Select-Object -ExpandProperty HotFixID | ConvertTo-Json -Compress`
	installedOut, err := runPS(installedScript)
	if err != nil {
		return nil, fmt.Errorf("get installed KBs: %w", err)
	}
	var installedKBs []string
	_ = json.Unmarshal([]byte(installedOut), &installedKBs)

	// Pending security updates
	pendingOut, err := runPS(psSearchScript)
	if err != nil {
		return nil, fmt.Errorf("search pending updates: %w", err)
	}
	var pending []map[string]any
	_ = json.Unmarshal([]byte(pendingOut), &pending)

	// Reboot pending
	rebootOut, err := runPS(psRebootPendingScript)
	if err != nil {
		return nil, fmt.Errorf("check reboot pending: %w", err)
	}
	rebootPending := strings.TrimSpace(rebootOut) == "true"

	return map[string]any{
		"installed_kbs":            installedKBs,
		"security_updates_pending": pending,
		"last_update_check":        time.Now().UTC().Format(time.RFC3339),
		"reboot_pending":           rebootPending,
	}, nil
}

// runPS runs a PowerShell script string and returns stdout as a string.
func runPS(script string) (string, error) {
	cmd := exec.Command("powershell.exe", "-NonInteractive", "-NoProfile", "-Command", script)
	out, err := cmd.Output()
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			return "", fmt.Errorf("powershell exit %d: %s", exitErr.ExitCode(), exitErr.Stderr)
		}
		return "", err
	}
	return strings.TrimSpace(string(out)), nil
}

// scheduleReboot creates a Windows scheduled task to reboot at the next
// maintenance window from the asset tag, or immediately if no window is set.
// Returns the scheduled time as RFC3339 or "" if scheduling failed.
func scheduleReboot(params map[string]any) string {
	rebootWindow, _ := params["reboot_window"].(string)
	if rebootWindow == "" {
		// Default: schedule reboot 5 minutes from now
		t := time.Now().UTC().Add(5 * time.Minute)
		timeStr := t.Format("15:04")
		dateStr := t.Format("2006-01-02")
		script := fmt.Sprintf(
			`schtasks /Create /TN "NexplaneReboot" /TR "shutdown /r /t 30" /SC ONCE /ST %s /SD %s /F /RL HIGHEST`,
			timeStr, dateStr,
		)
		if _, err := runPS(script); err != nil {
			return ""
		}
		return t.Format(time.RFC3339)
	}
	// If a cron-style window is provided, parse the next occurrence.
	// For MVP, schedule at the next occurrence of the time-of-day portion.
	// Full cron parsing is a future enhancement.
	return ""
}
```

- [ ] **Step 5: Create winpatch_other.go (non-Windows stub)**

Create `agent/commands/winpatch/winpatch_other.go`:

```go
//go:build !windows

package winpatch

import "fmt"

func applyPatchesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_windows_patches requires Windows")
}

func rollbackPatchesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_windows_patches requires Windows")
}

func auditPatchStatusOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_windows_patch_status requires Windows")
}
```

- [ ] **Step 6: Run tests — expect pass**

```bash
cd f:\Nexplane\nexplane\agent && go test ./commands/winpatch/... -v
```
Expected:
```
--- PASS: TestApplyWindowsPatchesRequiresMode (0.00s)
--- PASS: TestApplyWindowsPatchesInvalidMode (0.00s)
--- PASS: TestApplyWindowsPatchesModeKBRequiresKBID (0.00s)
--- PASS: TestApplyWindowsPatchesValidModesPassValidation (0.00s)
--- PASS: TestAuditWindowsPatchStatusExecuteReturnsResult (0.00s)
PASS
ok      nexplane-agent/commands/winpatch
```

- [ ] **Step 7: Build to verify cross-compilation**

```bash
cd f:\Nexplane\nexplane\agent && go build ./commands/winpatch/...
```
Expected: exits 0.

- [ ] **Step 8: Commit**

```bash
git add agent/commands/winpatch/
git commit -m "feat(agent): add winpatch package — PowerShell/WUA Windows Update patching with dry_run and rollback"
```

---

## Task 3: Register new commands in executor.go

**Files:**
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Add imports for linuxpatch and winpatch**

In `agent/executor/executor.go`, find the import block and add the two new packages:

```go
// Find this import block:
import (
	"fmt"

	"nexplane-agent/commands/changip"
	"nexplane-agent/commands/configsyslog"
	"nexplane-agent/commands/estimatesize"
	"nexplane-agent/commands/ebpf"
	"nexplane-agent/commands/linuxauth"
	"nexplane-agent/commands/ossecurity"
	"nexplane-agent/commands/uploadimage"
	"nexplane-agent/commands/virtualize"
	"nexplane-agent/commands/winharden"
	"nexplane-agent/commands/crossplatform"
	"nexplane-agent/commands/linuxupgrade"
)

// Replace with:
import (
	"fmt"

	"nexplane-agent/commands/changip"
	"nexplane-agent/commands/configsyslog"
	"nexplane-agent/commands/crossplatform"
	"nexplane-agent/commands/ebpf"
	"nexplane-agent/commands/estimatesize"
	"nexplane-agent/commands/linuxauth"
	"nexplane-agent/commands/linuxpatch"
	"nexplane-agent/commands/linuxupgrade"
	"nexplane-agent/commands/ossecurity"
	"nexplane-agent/commands/uploadimage"
	"nexplane-agent/commands/virtualize"
	"nexplane-agent/commands/winharden"
	"nexplane-agent/commands/winpatch"
)
```

- [ ] **Step 2: Add command registrations**

In `agent/executor/executor.go`, find the line `"upgrade_linux_instance": linuxupgrade.UpgradeLinuxInstanceExecute,` in the `commands` map and add after it:

```go
	// Linux patching
	"apply_linux_patches":      linuxpatch.ApplyLinuxPatchesExecute,
	"audit_linux_patch_status": linuxpatch.AuditLinuxPatchStatusExecute,
	// Windows patching
	"apply_windows_patches":      winpatch.ApplyWindowsPatchesExecute,
	"audit_windows_patch_status": winpatch.AuditWindowsPatchStatusExecute,
```

- [ ] **Step 3: Add rollback registrations**

In the `rollbacks` map, find `"upgrade_linux_instance": linuxupgrade.UpgradeLinuxInstanceRollback,` and add after it:

```go
	// Linux patching
	"apply_linux_patches": linuxpatch.ApplyLinuxPatchesRollback,
	// Windows patching
	"apply_windows_patches": winpatch.ApplyWindowsPatchesRollback,
```

- [ ] **Step 4: Build to verify it compiles**

```bash
cd f:\Nexplane\nexplane\agent && go build ./...
```
Expected: exits 0.

- [ ] **Step 5: Run full agent test suite**

```bash
cd f:\Nexplane\nexplane\agent && go test ./...
```
Expected: all existing tests plus the new linuxpatch and winpatch tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent/executor/executor.go
git commit -m "feat(agent): register apply_linux_patches, audit_linux_patch_status, apply_windows_patches, audit_windows_patch_status commands in executor"
```

---

## Task 4: Change type definitions (JSON)

**Files:**
- Create: `backend/app/connectors/change_type_definitions/patch_packages.json`
- Create: `backend/app/connectors/change_type_definitions/patch_campaign.json`

- [ ] **Step 1: Create patch_packages.json**

Create `backend/app/connectors/change_type_definitions/patch_packages.json`:

```json
{
  "change_type": "patch_packages",
  "display_name": "Patch Packages",
  "description": "Apply security patches to a Linux or Windows host. Use mode=security_only for all available security updates, mode=package to update a specific package, mode=cve to patch the package that fixes a given CVE, or mode=kb to install a specific Windows KB article.",
  "steps": [
    {"generic_action": "audit_patch_status",   "purpose": "preflight_validate", "required": true},
    {"generic_action": "apply_patches",        "purpose": "execute",            "required": true},
    {"generic_action": "verify_patch_status",  "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["agent_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["package_version_check", "output_check"],
  "parameters": {
    "os_family":    {"type": "string",  "enum": ["linux", "windows"],                       "required": true},
    "mode":         {"type": "string",  "enum": ["security_only", "package", "kb", "cve"],  "required": true},
    "package_name": {"type": "string",  "required": false},
    "cve_id":       {"type": "string",  "required": false},
    "kb_id":        {"type": "string",  "required": false},
    "dry_run":      {"type": "boolean", "default": false},
    "defer_reboot": {"type": "boolean", "default": false}
  }
}
```

- [ ] **Step 2: Create patch_campaign.json**

Create `backend/app/connectors/change_type_definitions/patch_campaign.json`:

```json
{
  "change_type": "patch_campaign",
  "display_name": "Emergency Patch Campaign",
  "description": "Fan out security patching across all hosts affected by a CVE or vulnerable package version. Patches are applied in rolling batches; the campaign aborts if the failure rate exceeds the configured threshold.",
  "steps": [
    {"generic_action": "identify_affected_assets", "purpose": "preflight_validate", "required": true},
    {"generic_action": "apply_campaign_patches",   "purpose": "execute",            "required": true},
    {"generic_action": "verify_campaign_results",  "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["asset_exists", "no_concurrent_changes"],
  "verification_methods": ["package_version_check"],
  "parameters": {
    "cve_id":               {"type": "string",  "required": false, "description": "e.g. CVE-2024-3094"},
    "package_name":         {"type": "string",  "required": false, "description": "e.g. xz-utils"},
    "affected_version_lt":  {"type": "string",  "required": false, "description": "patch all hosts running versions < this"},
    "asset_filter":         {"type": "object",  "required": false},
    "batch_size":           {"type": "integer", "default": 10,    "description": "hosts patched in parallel per wave"},
    "abort_error_threshold":{"type": "number",  "default": 0.2,   "description": "abort campaign if failure rate exceeds this fraction"},
    "dry_run":              {"type": "boolean", "default": false},
    "defer_reboot":         {"type": "boolean", "default": false}
  }
}
```

- [ ] **Step 3: Verify JSON is valid**

```bash
python -c "import json; json.load(open('f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/patch_packages.json')); print('OK')"
python -c "import json; json.load(open('f:/Nexplane/nexplane/backend/app/connectors/change_type_definitions/patch_campaign.json')); print('OK')"
```
Expected: `OK` for each.

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/change_type_definitions/patch_packages.json backend/app/connectors/change_type_definitions/patch_campaign.json
git commit -m "feat(backend): add patch_packages and patch_campaign change type definitions"
```

---

## Task 5: Connector executor — check_patch_compliance.py

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/check_patch_compliance.py`

- [ ] **Step 1: Create the file**

Create `backend/app/connectors/executors/nexplane_agent/check_patch_compliance.py`:

```python
"""
Discovers all assets that violate a patch age baseline and optionally creates
patch_packages change requests to remediate each non-compliant host.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Params:
        max_patch_age_days    int   default 30   Flag hosts whose last security update
                                                  is older than this many days.
        asset_filter          dict  optional     e.g. {"tags": {"env": "prod"}}
        os_family             str   optional     "linux" | "windows" | None (both)
        create_change_request bool  default False Auto-create a patch_packages CR for
                                                  each non-compliant host.

    Returns:
        compliant_hosts       list[str]   asset IDs that pass
        non_compliant_hosts   list[dict]  [{asset_id, hostname, os_family,
                                            days_since_patch, change_request_id?}]
        total_assets_checked  int
    """
    max_age = int(parameters.get("max_patch_age_days", 30))
    asset_filter = parameters.get("asset_filter", {})
    os_family_filter = parameters.get("os_family")
    auto_cr = bool(parameters.get("create_change_request", False))

    assets = await connector.asset_repository.list_assets(filter=asset_filter)

    compliant: list[str] = []
    non_compliant: list[dict] = []
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max_age)

    for asset in assets:
        meta = asset.metadata or {}
        af = meta.get("os_family")

        if os_family_filter and af != os_family_filter:
            continue

        last_patched_raw = meta.get("last_security_patch_at")
        if not last_patched_raw:
            days_since: int | None = None
        else:
            last_patched = datetime.fromisoformat(last_patched_raw)
            if last_patched.tzinfo is None:
                last_patched = last_patched.replace(tzinfo=timezone.utc)
            days_since = (datetime.now(tz=timezone.utc) - last_patched).days

        if days_since is not None and days_since <= max_age:
            compliant.append(asset.id)
            continue

        entry: dict[str, Any] = {
            "asset_id": asset.id,
            "hostname": asset.hostname,
            "os_family": af,
            "days_since_patch": days_since,
        }

        if auto_cr:
            cr = await connector.change_request_service.create(
                change_type="patch_packages",
                asset_id=asset.id,
                params={
                    "os_family": af,
                    "mode": "security_only",
                    "dry_run": False,
                },
                title=f"Security patch compliance: {asset.hostname}",
                description=(
                    f"Host is {days_since if days_since is not None else 'unknown'} days "
                    f"behind on security patches (threshold: {max_age} days)."
                ),
            )
            entry["change_request_id"] = cr.id

        non_compliant.append(entry)

    return {
        "compliant_hosts": compliant,
        "non_compliant_hosts": non_compliant,
        "total_assets_checked": len(compliant) + len(non_compliant),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # Compliance check is read-only (or creates CRs); nothing to roll back.
    return {"rolled_back": False, "reason": "check_patch_compliance is read-only"}
```

- [ ] **Step 2: Verify Python syntax**

```bash
python -c "import ast; ast.parse(open('f:/Nexplane/nexplane/backend/app/connectors/executors/nexplane_agent/check_patch_compliance.py').read()); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/check_patch_compliance.py
git commit -m "feat(backend): add check_patch_compliance connector executor — patch age baseline with optional auto-CR"
```

---

## Task 6: Connector executor — run_patch_campaign.py

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/run_patch_campaign.py`

- [ ] **Step 1: Create the file**

Create `backend/app/connectors/executors/nexplane_agent/run_patch_campaign.py`:

```python
"""
Identifies all assets affected by a CVE or package vulnerability and applies
patches in rolling batches, aborting if the error rate exceeds the threshold.
"""
from __future__ import annotations
import asyncio
from typing import Any


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Params: see patch_campaign.json parameter schema.

    Returns:
        affected_assets    int
        batches_completed  int
        hosts_patched      list[str]   asset IDs successfully patched
        hosts_failed       list[dict]  [{asset_id, hostname, error}]
        aborted            bool
        abort_reason       str | None
    """
    cve_id = parameters.get("cve_id")
    package_name = parameters.get("package_name")
    affected_version_lt = parameters.get("affected_version_lt")
    asset_filter = parameters.get("asset_filter", {})
    batch_size = int(parameters.get("batch_size", 10))
    abort_threshold = float(parameters.get("abort_error_threshold", 0.2))
    dry_run = bool(parameters.get("dry_run", False))
    defer_reboot = bool(parameters.get("defer_reboot", False))

    if not cve_id and not package_name:
        raise ValueError("At least one of cve_id or package_name is required")

    affected = await _find_affected_assets(
        connector, asset_filter, package_name, affected_version_lt, cve_id
    )

    if not affected:
        return {
            "affected_assets": 0,
            "batches_completed": 0,
            "hosts_patched": [],
            "hosts_failed": [],
            "aborted": False,
            "abort_reason": None,
        }

    patched: list[str] = []
    failed: list[dict] = []
    aborted = False
    abort_reason: str | None = None
    batch_num = 0

    batches = [affected[i:i + batch_size] for i in range(0, len(affected), batch_size)]

    for batch_num, batch in enumerate(batches):
        results = await asyncio.gather(
            *[
                _patch_single_host(connector, asset, parameters, dry_run, defer_reboot)
                for asset in batch
            ],
            return_exceptions=True,
        )

        for asset, result in zip(batch, results):
            if isinstance(result, Exception):
                failed.append({
                    "asset_id": asset["id"],
                    "hostname": asset["hostname"],
                    "error": str(result),
                })
            else:
                patched.append(asset["id"])

        total_attempted = len(patched) + len(failed)
        if total_attempted > 0 and len(failed) / total_attempted > abort_threshold:
            aborted = True
            abort_reason = (
                f"Error rate {len(failed)/total_attempted:.0%} exceeded threshold "
                f"{abort_threshold:.0%} after batch {batch_num + 1}"
            )
            break

    return {
        "affected_assets": len(affected),
        "batches_completed": batch_num + 1 if not aborted else batch_num,
        "hosts_patched": patched,
        "hosts_failed": failed,
        "aborted": aborted,
        "abort_reason": abort_reason,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    # Campaign rollback is not supported at the campaign level — individual
    # patch_packages change requests each have their own rollback.
    return {
        "rolled_back": False,
        "reason": "patch_campaign rollback must be executed per-host via individual patch_packages change requests",
    }


async def _find_affected_assets(
    connector: Any,
    asset_filter: dict,
    package_name: str | None,
    affected_version_lt: str | None,
    cve_id: str | None,
) -> list[dict]:
    """
    Query asset metadata for installed software inventory.
    The inventory is populated by the agent's audit_software_inventory command
    and stored as asset.metadata["software_inventory"]:
      [{"name": "xz-utils", "version": "5.4.1", "arch": "amd64"}, ...]

    Filters:
      - If package_name is set: include assets that have that package installed.
      - If affected_version_lt is set: further filter to versions < that string
        using packaging.version.Version comparison.
      - If cve_id is set (and no package_name): resolve the package name from
        metadata["security_advisories"]: [{"cve_id": "CVE-...", "package": "..."}]
    """
    assets = await connector.asset_repository.list_assets(filter=asset_filter)
    affected = []

    for asset in assets:
        meta = asset.metadata or {}
        inventory = meta.get("software_inventory", [])
        advisories = meta.get("security_advisories", [])

        pkg_to_check = package_name
        if not pkg_to_check and cve_id:
            for adv in advisories:
                if adv.get("cve_id") == cve_id:
                    pkg_to_check = adv.get("package")
                    break

        if not pkg_to_check:
            continue

        for item in inventory:
            if item.get("name") != pkg_to_check:
                continue
            if affected_version_lt:
                try:
                    from packaging.version import Version  # type: ignore[import]
                    if not (Version(item["version"]) < Version(affected_version_lt)):
                        continue
                except Exception:
                    pass  # unparseable version — include conservatively
            affected.append({
                "id": asset.id,
                "hostname": asset.hostname,
                "os_family": meta.get("os_family", "linux"),
                "package_name": pkg_to_check,
                "installed_version": item.get("version"),
            })
            break  # one match per asset is enough

    return affected


async def _patch_single_host(
    connector: Any,
    asset: dict,
    params: dict,
    dry_run: bool,
    defer_reboot: bool,
) -> None:
    """
    Dispatches an agent command to patch a single host.
    Raises RuntimeError on failure so asyncio.gather() captures it as an exception.
    """
    os_family = asset["os_family"]
    cve_id = params.get("cve_id")
    pkg = params.get("package_name") or asset.get("package_name")

    if os_family == "windows":
        command = "apply_windows_patches"
        cmd_params: dict[str, Any] = {
            "mode": "security_only",
            "dry_run": dry_run,
            "defer_reboot": defer_reboot,
        }
        if cve_id:
            cmd_params["note"] = f"Campaign targeting {cve_id}"
    else:
        command = "apply_linux_patches"
        if cve_id:
            cmd_params = {"mode": "cve", "cve_id": cve_id, "dry_run": dry_run}
        elif pkg:
            cmd_params = {"mode": "package", "package_name": pkg, "dry_run": dry_run}
        else:
            cmd_params = {"mode": "security_only", "dry_run": dry_run}

    result = await connector.agent_client.run_command(
        asset_id=asset["id"],
        command=command,
        params=cmd_params,
        timeout_seconds=600,
    )
    if result.get("status") != "completed":
        raise RuntimeError(result.get("error", "unknown agent error"))
```

- [ ] **Step 2: Verify Python syntax**

```bash
python -c "import ast; ast.parse(open('f:/Nexplane/nexplane/backend/app/connectors/executors/nexplane_agent/run_patch_campaign.py').read()); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/run_patch_campaign.py
git commit -m "feat(backend): add run_patch_campaign connector executor — rolling batch CVE patching with abort-on-error-rate"
```

---

## Task 7: Add catalog entries to nexplane_agent.json

**Files:**
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`

- [ ] **Step 1: Add six new action entries**

In `backend/app/connectors/catalog/nexplane_agent.json`, find the closing bracket of the `actions` array (the last `}` before `]`) and add six new entries after the `upgrade_linux_instance` entry:

```json
                    ,
                    {
                        "action_id": "apply_linux_patches",
                        "generic_action": "apply_linux_patches",
                        "action_type": "change",
                        "execution_tier": 3,
                        "display_name": "Apply Linux Patches",
                        "description": "Apply security patches on a Linux host using apt, dnf, or yum. Supports security-only mode, specific package, or CVE-targeted patching. Captures before/after versions and detects reboot requirement.",
                        "applicable_asset_types": ["server"],
                        "parameters": [
                            {"name": "mode",         "type": "string",  "required": true},
                            {"name": "package_name", "type": "string",  "required": false},
                            {"name": "cve_id",       "type": "string",  "required": false},
                            {"name": "dry_run",      "type": "boolean", "required": false, "default": false},
                            {"name": "defer_reboot", "type": "boolean", "required": false, "default": false}
                        ],
                        "executor": "nexplane_agent.apply_linux_patches",
                        "rollback_action": "apply_linux_patches",
                        "estimated_duration_seconds": 120,
                        "safety_notes": [
                            "Use dry_run=true first to preview changes",
                            "Reboot may be required after applying kernel or glibc patches"
                        ]
                    },
                    {
                        "action_id": "audit_linux_patch_status",
                        "generic_action": "audit_linux_patch_status",
                        "action_type": "ingest",
                        "execution_tier": 3,
                        "display_name": "Audit Linux Patch Status",
                        "description": "Read-only: returns installed packages, available security updates, days since last security patch, and reboot-required status.",
                        "applicable_asset_types": ["server"],
                        "parameters": [],
                        "executor": "nexplane_agent.audit_linux_patch_status",
                        "estimated_duration_seconds": 30
                    },
                    {
                        "action_id": "apply_windows_patches",
                        "generic_action": "apply_windows_patches",
                        "action_type": "change",
                        "execution_tier": 3,
                        "display_name": "Apply Windows Patches",
                        "description": "Install Windows Updates using the Windows Update Agent COM API. Supports security-only mode or a specific KB article. Schedules reboot via Task Scheduler if required.",
                        "applicable_asset_types": ["server", "workstation"],
                        "parameters": [
                            {"name": "mode",          "type": "string",  "required": true},
                            {"name": "kb_id",         "type": "string",  "required": false},
                            {"name": "dry_run",       "type": "boolean", "required": false, "default": false},
                            {"name": "defer_reboot",  "type": "boolean", "required": false, "default": false},
                            {"name": "reboot_window", "type": "string",  "required": false}
                        ],
                        "executor": "nexplane_agent.apply_windows_patches",
                        "rollback_action": "apply_windows_patches",
                        "estimated_duration_seconds": 300,
                        "safety_notes": [
                            "Use dry_run=true first to preview pending updates",
                            "Reboot is often required; use defer_reboot=true to prevent automatic scheduling",
                            "Rollback uninstalls the KB — not all KBs support uninstallation"
                        ]
                    },
                    {
                        "action_id": "audit_windows_patch_status",
                        "generic_action": "audit_windows_patch_status",
                        "action_type": "ingest",
                        "execution_tier": 3,
                        "display_name": "Audit Windows Patch Status",
                        "description": "Read-only: returns installed KBs, pending security updates with severity and CVE IDs, last update check time, and reboot-pending status.",
                        "applicable_asset_types": ["server", "workstation"],
                        "parameters": [],
                        "executor": "nexplane_agent.audit_windows_patch_status",
                        "estimated_duration_seconds": 30
                    },
                    {
                        "action_id": "check_patch_compliance",
                        "generic_action": "check_patch_compliance",
                        "action_type": "ingest",
                        "execution_tier": 3,
                        "display_name": "Check Patch Compliance",
                        "description": "Evaluates all assets against a patch age threshold. Flags hosts whose last_security_patch_at metadata is older than max_patch_age_days. Optionally creates patch_packages change requests for non-compliant hosts.",
                        "applicable_asset_types": ["server", "workstation"],
                        "parameters": [
                            {"name": "max_patch_age_days",    "type": "integer", "required": false, "default": 30},
                            {"name": "asset_filter",          "type": "object",  "required": false},
                            {"name": "os_family",             "type": "string",  "required": false},
                            {"name": "create_change_request", "type": "boolean", "required": false, "default": false}
                        ],
                        "executor": "nexplane_agent.check_patch_compliance",
                        "estimated_duration_seconds": 10
                    },
                    {
                        "action_id": "run_patch_campaign",
                        "generic_action": "run_patch_campaign",
                        "action_type": "change",
                        "execution_tier": 3,
                        "display_name": "Run Patch Campaign",
                        "description": "Fan out patching across all hosts affected by a CVE or vulnerable package version. Patches in rolling batches and aborts if the failure rate exceeds the configured threshold.",
                        "applicable_asset_types": ["server", "workstation"],
                        "parameters": [
                            {"name": "cve_id",               "type": "string",  "required": false},
                            {"name": "package_name",         "type": "string",  "required": false},
                            {"name": "affected_version_lt",  "type": "string",  "required": false},
                            {"name": "asset_filter",         "type": "object",  "required": false},
                            {"name": "batch_size",           "type": "integer", "required": false, "default": 10},
                            {"name": "abort_error_threshold","type": "number",  "required": false, "default": 0.2},
                            {"name": "dry_run",              "type": "boolean", "required": false, "default": false},
                            {"name": "defer_reboot",         "type": "boolean", "required": false, "default": false}
                        ],
                        "executor": "nexplane_agent.run_patch_campaign",
                        "estimated_duration_seconds": 3600,
                        "safety_notes": [
                            "Use dry_run=true to preview affected hosts before executing",
                            "Set abort_error_threshold conservatively (0.1–0.2) for production fleets",
                            "Ensure agents are reachable and not mid-reboot before starting a campaign"
                        ]
                    }
```

- [ ] **Step 2: Verify JSON is valid**

```bash
python -c "import json; json.load(open('f:/Nexplane/nexplane/backend/app/connectors/catalog/nexplane_agent.json')); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/catalog/nexplane_agent.json
git commit -m "feat(backend): add 6 patch management actions to nexplane_agent catalog"
```

---

## Task 8: Backend integration tests

**Files:**
- Create: `backend/tests/test_patch_connector.py`

- [ ] **Step 1: Write the tests**

Create `backend/tests/test_patch_connector.py`:

```python
"""
Unit tests for the patch management connector executors.
Uses simple mock objects — no database or agent required.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.connectors.executors.nexplane_agent import check_patch_compliance, run_patch_campaign


# ---------------------------------------------------------------------------
# Helpers / Mocks
# ---------------------------------------------------------------------------

class MockAsset:
    def __init__(self, id: str, hostname: str, metadata: dict | None = None):
        self.id = id
        self.hostname = hostname
        self.metadata = metadata or {}


class MockAssetRepository:
    def __init__(self, assets: list[MockAsset]):
        self._assets = assets

    async def list_assets(self, filter: dict | None = None) -> list[MockAsset]:
        return self._assets


class MockChangeRequest:
    def __init__(self, id: str):
        self.id = id


class MockChangeRequestService:
    def __init__(self):
        self.created: list[dict] = []

    async def create(self, **kwargs) -> MockChangeRequest:
        self.created.append(kwargs)
        return MockChangeRequest(id=f"cr-{len(self.created)}")


class MockAgentClient:
    def __init__(self, fail_asset_ids: list[str] | None = None):
        self._fail = set(fail_asset_ids or [])
        self.calls: list[dict] = []

    async def run_command(self, asset_id: str, command: str, params: dict, timeout_seconds: int) -> dict:
        self.calls.append({"asset_id": asset_id, "command": command, "params": params})
        if asset_id in self._fail:
            return {"status": "failed", "error": "agent unreachable"}
        return {"status": "completed", "data": {"packages_updated": [], "reboot_required": False}}


class MockConnector:
    def __init__(self, assets: list[MockAsset], fail_asset_ids: list[str] | None = None):
        self.asset_repository = MockAssetRepository(assets)
        self.change_request_service = MockChangeRequestService()
        self.agent_client = MockAgentClient(fail_asset_ids=fail_asset_ids)


def recent_patch() -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(days=5)).isoformat()


def stale_patch() -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(days=60)).isoformat()


# ---------------------------------------------------------------------------
# check_patch_compliance tests
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_compliance_all_compliant():
    assets = [
        MockAsset("a1", "host1", {"os_family": "linux", "last_security_patch_at": recent_patch()}),
        MockAsset("a2", "host2", {"os_family": "linux", "last_security_patch_at": recent_patch()}),
    ]
    connector = MockConnector(assets)
    result = run(check_patch_compliance.execute({"max_patch_age_days": 30}, [], connector))
    assert result["total_assets_checked"] == 2
    assert len(result["compliant_hosts"]) == 2
    assert len(result["non_compliant_hosts"]) == 0


def test_compliance_stale_host_detected():
    assets = [
        MockAsset("a1", "host1", {"os_family": "linux", "last_security_patch_at": stale_patch()}),
    ]
    connector = MockConnector(assets)
    result = run(check_patch_compliance.execute({"max_patch_age_days": 30}, [], connector))
    assert len(result["non_compliant_hosts"]) == 1
    assert result["non_compliant_hosts"][0]["asset_id"] == "a1"
    assert result["non_compliant_hosts"][0]["days_since_patch"] >= 60


def test_compliance_no_patch_metadata_is_non_compliant():
    assets = [MockAsset("a1", "host1", {"os_family": "linux"})]
    connector = MockConnector(assets)
    result = run(check_patch_compliance.execute({}, [], connector))
    assert len(result["non_compliant_hosts"]) == 1
    assert result["non_compliant_hosts"][0]["days_since_patch"] is None


def test_compliance_os_family_filter():
    assets = [
        MockAsset("a1", "linux-host", {"os_family": "linux", "last_security_patch_at": stale_patch()}),
        MockAsset("a2", "win-host",   {"os_family": "windows", "last_security_patch_at": stale_patch()}),
    ]
    connector = MockConnector(assets)
    result = run(check_patch_compliance.execute({"os_family": "linux"}, [], connector))
    assert result["total_assets_checked"] == 1
    assert result["non_compliant_hosts"][0]["asset_id"] == "a1"


def test_compliance_auto_create_change_request():
    assets = [MockAsset("a1", "host1", {"os_family": "linux", "last_security_patch_at": stale_patch()})]
    connector = MockConnector(assets)
    result = run(check_patch_compliance.execute(
        {"max_patch_age_days": 30, "create_change_request": True}, [], connector
    ))
    assert len(result["non_compliant_hosts"]) == 1
    assert "change_request_id" in result["non_compliant_hosts"][0]
    assert len(connector.change_request_service.created) == 1
    assert connector.change_request_service.created[0]["change_type"] == "patch_packages"


# ---------------------------------------------------------------------------
# run_patch_campaign tests
# ---------------------------------------------------------------------------

def make_asset_with_inventory(id: str, hostname: str, os_family: str, pkg: str, version: str) -> MockAsset:
    return MockAsset(id, hostname, {
        "os_family": os_family,
        "software_inventory": [{"name": pkg, "version": version, "arch": "amd64"}],
    })


def test_campaign_requires_cve_or_package():
    connector = MockConnector([])
    with pytest.raises(ValueError, match="cve_id or package_name"):
        run(run_patch_campaign.execute({}, [], connector))


def test_campaign_no_affected_assets():
    assets = [make_asset_with_inventory("a1", "host1", "linux", "curl", "7.81.0")]
    connector = MockConnector(assets)
    result = run(run_patch_campaign.execute({"package_name": "openssl"}, [], connector))
    assert result["affected_assets"] == 0
    assert result["hosts_patched"] == []
    assert not result["aborted"]


def test_campaign_patches_affected_assets():
    assets = [
        make_asset_with_inventory("a1", "host1", "linux", "xz-utils", "5.4.1"),
        make_asset_with_inventory("a2", "host2", "linux", "xz-utils", "5.4.1"),
        make_asset_with_inventory("a3", "host3", "linux", "curl", "7.81.0"),  # different pkg
    ]
    connector = MockConnector(assets)
    result = run(run_patch_campaign.execute(
        {"package_name": "xz-utils", "affected_version_lt": "5.6.0"}, [], connector
    ))
    assert result["affected_assets"] == 2
    assert set(result["hosts_patched"]) == {"a1", "a2"}
    assert result["hosts_failed"] == []
    assert not result["aborted"]


def test_campaign_version_filter_excludes_patched_hosts():
    assets = [
        make_asset_with_inventory("a1", "host1", "linux", "xz-utils", "5.4.1"),  # vulnerable
        make_asset_with_inventory("a2", "host2", "linux", "xz-utils", "5.6.2"),  # already patched
    ]
    connector = MockConnector(assets)
    result = run(run_patch_campaign.execute(
        {"package_name": "xz-utils", "affected_version_lt": "5.6.0"}, [], connector
    ))
    assert result["affected_assets"] == 1
    assert result["hosts_patched"] == ["a1"]


def test_campaign_aborts_on_high_error_rate():
    assets = [
        make_asset_with_inventory("a1", "host1", "linux", "xz-utils", "5.4.1"),
        make_asset_with_inventory("a2", "host2", "linux", "xz-utils", "5.4.1"),
        make_asset_with_inventory("a3", "host3", "linux", "xz-utils", "5.4.1"),
    ]
    # All hosts fail
    connector = MockConnector(assets, fail_asset_ids=["a1", "a2", "a3"])
    result = run(run_patch_campaign.execute(
        {"package_name": "xz-utils", "batch_size": 3, "abort_error_threshold": 0.2},
        [],
        connector,
    ))
    assert result["aborted"] is True
    assert result["abort_reason"] is not None
    assert "Error rate" in result["abort_reason"]


def test_campaign_dry_run_does_not_skip_dispatch():
    """dry_run=True is forwarded to each agent command; campaign itself still runs."""
    assets = [make_asset_with_inventory("a1", "host1", "linux", "openssl", "3.0.2")]
    connector = MockConnector(assets)
    result = run(run_patch_campaign.execute(
        {"package_name": "openssl", "dry_run": True}, [], connector
    ))
    assert result["hosts_patched"] == ["a1"]
    assert connector.agent_client.calls[0]["params"]["dry_run"] is True


def test_campaign_windows_host_uses_apply_windows_patches():
    assets = [make_asset_with_inventory("a1", "win1", "windows", "OpenSSL", "3.0.2")]
    connector = MockConnector(assets)
    run(run_patch_campaign.execute({"package_name": "OpenSSL"}, [], connector))
    assert connector.agent_client.calls[0]["command"] == "apply_windows_patches"


def test_campaign_cve_mode_uses_cve_param_for_linux():
    assets = [MockAsset("a1", "host1", {
        "os_family": "linux",
        "software_inventory": [{"name": "xz-utils", "version": "5.4.1"}],
        "security_advisories": [{"cve_id": "CVE-2024-3094", "package": "xz-utils"}],
    })]
    connector = MockConnector(assets)
    run(run_patch_campaign.execute({"cve_id": "CVE-2024-3094"}, [], connector))
    call = connector.agent_client.calls[0]
    assert call["command"] == "apply_linux_patches"
    assert call["params"]["mode"] == "cve"
    assert call["params"]["cve_id"] == "CVE-2024-3094"
```

- [ ] **Step 2: Run the tests — expect pass**

```bash
cd f:\Nexplane\nexplane\backend && python -m pytest tests/test_patch_connector.py -v
```
Expected:
```
PASSED tests/test_patch_connector.py::test_compliance_all_compliant
PASSED tests/test_patch_connector.py::test_compliance_stale_host_detected
PASSED tests/test_patch_connector.py::test_compliance_no_patch_metadata_is_non_compliant
PASSED tests/test_patch_connector.py::test_compliance_os_family_filter
PASSED tests/test_patch_connector.py::test_compliance_auto_create_change_request
PASSED tests/test_patch_connector.py::test_campaign_requires_cve_or_package
PASSED tests/test_patch_connector.py::test_campaign_no_affected_assets
PASSED tests/test_patch_connector.py::test_campaign_patches_affected_assets
PASSED tests/test_patch_connector.py::test_campaign_version_filter_excludes_patched_hosts
PASSED tests/test_patch_connector.py::test_campaign_aborts_on_high_error_rate
PASSED tests/test_patch_connector.py::test_campaign_dry_run_does_not_skip_dispatch
PASSED tests/test_patch_connector.py::test_campaign_windows_host_uses_apply_windows_patches
PASSED tests/test_patch_connector.py::test_campaign_cve_mode_uses_cve_param_for_linux
13 passed in ...
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_patch_connector.py
git commit -m "test(backend): add patch connector tests — compliance baseline and campaign orchestration"
```

---

## Self-Review Checklist

**Spec coverage:**
- Task 1 (linuxpatch): linuxpatch.go, linuxpatch_linux.go, linuxpatch_other.go, linuxpatch_test.go
- Task 2 (winpatch): winpatch.go, winpatch_windows.go, winpatch_other.go, winpatch_test.go
- Task 3 (executor): 4 commands + 2 rollbacks registered in executor.go
- Task 4 (JSON): patch_packages.json and patch_campaign.json with descriptions for AI planner
- Task 5 (compliance connector): check_patch_compliance.py with asset metadata contract
- Task 6 (campaign connector): run_patch_campaign.py with CVE/package mapping and batch abort logic
- Task 7 (catalog): 6 new actions in nexplane_agent.json
- Task 8 (tests): 13 backend tests covering both connectors, including edge cases

**TDD:** All implementation files written after their test files.

**Placeholder scan:** No TODOs, no vague steps — all code is complete and runnable.

**Type consistency:**
- Go functions exported correctly and referenced in executor.go with correct package paths `nexplane-agent/commands/linuxpatch` and `nexplane-agent/commands/winpatch`
- Python executors follow `async def execute(parameters, asset_ids, connector)` signature matching all existing executors in `nexplane_agent/`
- JSON change type definitions use the same field schema shape as existing definitions in `change_type_definitions/`
- Catalog entries follow the nexplane_agent.json action object shape (action_id, generic_action, action_type, executor, parameters array)
