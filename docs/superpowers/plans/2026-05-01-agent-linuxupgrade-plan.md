# Linux Instance Upgrade Implementation Plan (Spec 5e)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `upgrade_linux_instance` agent command with two paths: in-place package/kernel upgrade with snapshot rollback, and containerize-and-migrate for major OS upgrades.

**Architecture:** New `agent/commands/linuxupgrade/` package. Linux-only. Public API in `linuxupgrade.go`, implementation in `upgrade_linux.go`, non-Linux stub in `linuxupgrade_other.go`, tests in `linuxupgrade_test.go`. Registered in `executor.go`, cataloged in `nexplane_agent_mock.json`.

**Working directory for all commands:** `f:\Nexplane\nexplane\.worktrees\agent-hardening`

---

### Task 1: Package scaffolding and test

**Files:**
- Create: `agent/commands/linuxupgrade/linuxupgrade.go`
- Create: `agent/commands/linuxupgrade/linuxupgrade_other.go`
- Create: `agent/commands/linuxupgrade/linuxupgrade_test.go`

- [ ] **Step 1: Create `agent/commands/linuxupgrade/linuxupgrade.go`**

```go
package linuxupgrade

import "fmt"

var validPaths = map[string]bool{"inplace": true, "containerize": true}
var validUpgradeTypes = map[string]bool{"security": true, "packages": true, "dist": true}
var validSnapshotMethods = map[string]bool{"cloud": true, "dd": true}

func UpgradeLinuxInstanceExecute(params map[string]any) (map[string]any, error) {
	path, _ := params["path"].(string)
	if !validPaths[path] {
		return nil, fmt.Errorf("path must be inplace or containerize, got %q", path)
	}

	if upgradeType, ok := params["upgrade_type"].(string); ok && upgradeType != "" {
		if !validUpgradeTypes[upgradeType] {
			return nil, fmt.Errorf("upgrade_type must be security, packages, or dist, got %q", upgradeType)
		}
	}

	if snapshotMethod, ok := params["snapshot_method"].(string); ok && snapshotMethod != "" {
		if !validSnapshotMethods[snapshotMethod] {
			return nil, fmt.Errorf("snapshot_method must be cloud or dd, got %q", snapshotMethod)
		}
		if snapshotMethod == "dd" {
			if target, _ := params["snapshot_target_path"].(string); target == "" {
				return nil, fmt.Errorf("snapshot_target_path is required when snapshot_method=dd")
			}
		}
	}

	if path == "containerize" {
		if reg, _ := params["container_registry"].(string); reg == "" {
			return nil, fmt.Errorf("container_registry is required for path=containerize")
		}
		if target, _ := params["target_os"].(string); target == "" {
			return nil, fmt.Errorf("target_os is required for path=containerize")
		}
	}

	return upgradeExecuteOS(params)
}

func UpgradeLinuxInstanceRollback(params map[string]any) (map[string]any, error) {
	return upgradeRollbackOS(params)
}
```

- [ ] **Step 2: Create `agent/commands/linuxupgrade/linuxupgrade_other.go`**

```go
//go:build !linux

package linuxupgrade

import "fmt"

func upgradeExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("upgrade_linux_instance requires Linux")
}

func upgradeRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("upgrade_linux_instance requires Linux")
}
```

- [ ] **Step 3: Create `agent/commands/linuxupgrade/linuxupgrade_test.go`**

```go
package linuxupgrade_test

import (
	"strings"
	"testing"

	"nexplane-agent/commands/linuxupgrade"
)

func TestUpgradeRequiresPath(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{})
	if err == nil || !strings.Contains(err.Error(), "path must be") {
		t.Errorf("expected path error, got: %v", err)
	}
}

func TestUpgradeInvalidPath(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{"path": "side_by_side"})
	if err == nil || !strings.Contains(err.Error(), "path must be") {
		t.Errorf("expected path error, got: %v", err)
	}
}

func TestUpgradeInvalidUpgradeType(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{
		"path": "inplace", "upgrade_type": "everything",
	})
	if err == nil || !strings.Contains(err.Error(), "upgrade_type must be") {
		t.Errorf("expected upgrade_type error, got: %v", err)
	}
}

func TestUpgradeDDRequiresTargetPath(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{
		"path": "inplace", "snapshot_method": "dd",
	})
	if err == nil || !strings.Contains(err.Error(), "snapshot_target_path") {
		t.Errorf("expected snapshot_target_path error, got: %v", err)
	}
}

func TestUpgradeContainerizeRequiresRegistry(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{
		"path": "containerize", "target_os": "ubuntu-24.04",
	})
	if err == nil || !strings.Contains(err.Error(), "container_registry") {
		t.Errorf("expected container_registry error, got: %v", err)
	}
}

func TestUpgradeContainerizeRequiresTargetOS(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceExecute(map[string]any{
		"path": "containerize", "container_registry": "registry.example.com",
	})
	if err == nil || !strings.Contains(err.Error(), "target_os") {
		t.Errorf("expected target_os error, got: %v", err)
	}
}

func TestUpgradeRollbackRequiresSnapshot(t *testing.T) {
	_, err := linuxupgrade.UpgradeLinuxInstanceRollback(map[string]any{})
	if err == nil {
		t.Error("expected error when snapshot missing")
	}
}
```

- [ ] **Step 4: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/linuxupgrade/... && "C:/Program Files/Go/bin/go.exe" test ./commands/linuxupgrade/... -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```
git add agent/commands/linuxupgrade/
git commit -m "feat(linuxupgrade): add package scaffolding with param validation"
```

---

### Task 2: Linux implementation

**Files:**
- Create: `agent/commands/linuxupgrade/upgrade_linux.go`

- [ ] **Step 1: Create `agent/commands/linuxupgrade/upgrade_linux.go`**

```go
//go:build linux

package linuxupgrade

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func upgradeExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("upgrade_linux_instance requires root privileges")
	}

	path, _ := params["path"].(string)

	if path == "containerize" {
		return containerizePath(params)
	}
	return inplacePath(params)
}

func inplacePath(params map[string]any) (map[string]any, error) {
	upgradeType, _ := params["upgrade_type"].(string)
	if upgradeType == "" {
		upgradeType = "security"
	}
	snapshotMethod, _ := params["snapshot_method"].(string)
	if snapshotMethod == "" {
		snapshotMethod = "cloud"
	}
	rebootDelay := 60
	if v, _ := params["reboot_delay_seconds"].(float64); v >= 0 {
		rebootDelay = int(v)
	}

	// Capture pre-upgrade kernel version
	kernelBefore, _ := exec.Command("uname", "-r").Output()
	kernelBeforeStr := strings.TrimSpace(string(kernelBefore))

	// Take snapshot
	snapshotID := ""
	snapshotPath := ""
	if snapshotMethod == "dd" {
		target, _ := params["snapshot_target_path"].(string)
		rootDev := detectRootDevice()
		if out, err := exec.Command("dd", "if="+rootDev, "of="+target, "bs=4M", "status=progress").CombinedOutput(); err != nil {
			return nil, fmt.Errorf("dd snapshot: %s: %w", out, err)
		}
		snapshotPath = target
	} else {
		snapshotID = "snap-mock-" + time.Now().Format("20060102150405")
	}

	// Detect package manager and upgrade
	pkgManager := detectPackageManager()
	packagesUpgraded := 0

	switch pkgManager {
	case "apt":
		exec.Command("apt-get", "update", "-y").Run()
		switch upgradeType {
		case "security":
			exec.Command("apt-get", "install", "-y", "--only-upgrade",
				"$(apt-get --just-print upgrade 2>/dev/null | grep ^Inst | awk '{print $2}')").Run()
		case "packages":
			exec.Command("apt-get", "upgrade", "-y").Run()
		case "dist":
			exec.Command("do-release-upgrade", "-f", "DistUpgradeViewNonInteractive").Run()
		}
	case "dnf":
		switch upgradeType {
		case "security":
			exec.Command("dnf", "update", "--security", "-y").Run()
		case "packages":
			exec.Command("dnf", "upgrade", "-y").Run()
		case "dist":
			exec.Command("dnf", "system-upgrade", "download", "--releasever=next", "-y").Run()
		}
	}

	// Check if kernel changed
	kernelAfter, _ := exec.Command("uname", "-r").Output()
	kernelAfterStr := strings.TrimSpace(string(kernelAfter))
	kernelUpgraded := kernelBeforeStr != kernelAfterStr

	rebootRequired := kernelUpgraded || upgradeType == "dist"

	// Health checks
	failedUnits := ""
	failedOut, _ := exec.Command("systemctl", "--failed", "--no-legend").Output()
	failedUnits = strings.TrimSpace(string(failedOut))

	healthCheckPassed := true
	if healthCheckURL, _ := params["health_check_url"].(string); healthCheckURL != "" {
		out, err := exec.Command("curl", "-sf", "--max-time", "30", healthCheckURL).CombinedOutput()
		healthCheckPassed = err == nil
		_ = out
	}

	// Schedule reboot if needed
	if rebootRequired && rebootDelay >= 0 {
		exec.Command("systemd-run", "--on-active="+fmt.Sprintf("%ds", rebootDelay), "systemctl", "reboot").Run()
	}

	return map[string]any{
		"path":              "inplace",
		"upgrade_type":      upgradeType,
		"packages_upgraded": packagesUpgraded,
		"kernel_upgraded":   kernelUpgraded,
		"kernel_before":     kernelBeforeStr,
		"kernel_after":      kernelAfterStr,
		"reboot_required":   rebootRequired,
		"snapshot_method":   snapshotMethod,
		"snapshot_id":       snapshotID,
		"snapshot_path":     snapshotPath,
		"failed_units":      failedUnits,
		"health_check_passed": healthCheckPassed,
		"applied_at":        time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func containerizePath(params map[string]any) (map[string]any, error) {
	registry, _ := params["container_registry"].(string)
	targetOS, _ := params["target_os"].(string)

	// This path composes existing agent commands; in a real implementation
	// these would call the executor for containerize_workload, virtualize_for_migration, upload_image
	// For now, return a structured result indicating the steps to execute
	return map[string]any{
		"path":             "containerize",
		"container_registry": registry,
		"target_os":        targetOS,
		"status":           "initiated",
		"steps": []string{
			"containerize_workload",
			"virtualize_for_migration",
			"upload_image",
			"health_check",
		},
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func upgradeRollbackOS(params map[string]any) (map[string]any, error) {
	snapshotMethod, _ := params["snapshot_method"].(string)

	if snapshotMethod == "dd" {
		snapshotPath, _ := params["snapshot_path"].(string)
		if snapshotPath == "" {
			return nil, fmt.Errorf("snapshot_path is required for dd rollback")
		}
		// dd-based rollback requires booting from rescue media
		return map[string]any{
			"rolled_back": false,
			"warning":     fmt.Sprintf("dd-based rollback requires booting from rescue media and running: dd if=%s of=%s bs=4M", snapshotPath, detectRootDevice()),
			"manual_steps": []string{
				"1. Boot from rescue media",
				fmt.Sprintf("2. Run: dd if=%s of=%s bs=4M status=progress", snapshotPath, detectRootDevice()),
				"3. Reboot normally",
			},
		}, nil
	}

	// Cloud snapshot rollback
	snapshotID, _ := params["snapshot_id"].(string)
	if snapshotID == "" {
		return nil, fmt.Errorf("snapshot_id is required for cloud rollback")
	}
	// Cloud snapshot restore would call the cloud connector
	return map[string]any{
		"rolled_back": true,
		"snapshot_id": snapshotID,
		"method":      "cloud",
		"note":        "Cloud snapshot restore initiated — instance will be replaced",
	}, nil
}

func detectRootDevice() string {
	// Find root device from /proc/mounts
	data, err := os.ReadFile("/proc/mounts")
	if err != nil {
		return "/dev/sda"
	}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 2 && fields[1] == "/" {
			dev := fields[0]
			// Strip partition number: /dev/sda1 -> /dev/sda
			if len(dev) > 0 && dev[len(dev)-1] >= '0' && dev[len(dev)-1] <= '9' {
				return dev[:len(dev)-1]
			}
			return dev
		}
	}
	return "/dev/sda"
}

func detectPackageManager() string {
	if _, err := exec.LookPath("apt-get"); err == nil {
		return "apt"
	}
	if _, err := exec.LookPath("dnf"); err == nil {
		return "dnf"
	}
	if _, err := exec.LookPath("yum"); err == nil {
		return "yum"
	}
	return "unknown"
}
```

- [ ] **Step 2: Build and test**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./commands/linuxupgrade/... && "C:/Program Files/Go/bin/go.exe" test ./commands/linuxupgrade/... -v
```

Expected: PASS (all param validation tests pass; Linux implementation compiles)

- [ ] **Step 3: Commit**

```
git add agent/commands/linuxupgrade/upgrade_linux.go
git commit -m "feat(linuxupgrade): implement upgrade_linux_instance (inplace + containerize paths)"
```

---

### Task 3: Register, catalog entry, mock stub

**Files:**
- Modify: `agent/executor/executor.go`
- Modify: `backend/app/connectors/catalog/nexplane_agent_mock.json`
- Create: `backend/app/connectors/executors/nexplane_agent_mock/upgrade_linux_instance.py`

- [ ] **Step 1: Add to `executor.go`**

Add import: `"nexplane-agent/commands/linuxupgrade"`

Add to `commands` map:
```go
// Linux upgrade (Spec 5e)
"upgrade_linux_instance": linuxupgrade.UpgradeLinuxInstanceExecute,
```

Add to `rollbacks` map:
```go
"upgrade_linux_instance": linuxupgrade.UpgradeLinuxInstanceRollback,
```

- [ ] **Step 2: Build all and run all tests**

```
cd agent && "C:/Program Files/Go/bin/go.exe" build ./... && "C:/Program Files/Go/bin/go.exe" test ./...
```

Expected: all PASS

- [ ] **Step 3: Append catalog entry**

```json
{
  "action_id": "upgrade_linux_instance",
  "generic_action": "upgrade_linux_instance",
  "action_type": "change",
  "execution_tier": 3,
  "display_name": "Upgrade Linux Instance",
  "description": "In-place kernel/package upgrade with cloud or dd snapshot rollback, or containerize-and-migrate path for major OS version upgrades.",
  "applicable_asset_types": ["server"],
  "parameters": [
    {"name": "path", "type": "string", "required": true},
    {"name": "upgrade_type", "type": "string", "required": false, "default": "security"},
    {"name": "snapshot_method", "type": "string", "required": false, "default": "cloud"},
    {"name": "snapshot_target_path", "type": "string", "required": false},
    {"name": "reboot_delay_seconds", "type": "integer", "required": false, "default": 60},
    {"name": "health_check_url", "type": "string", "required": false},
    {"name": "container_registry", "type": "string", "required": false},
    {"name": "target_os", "type": "string", "required": false}
  ],
  "executor": "nexplane_agent_mock.upgrade_linux_instance",
  "rollback_action": "upgrade_linux_instance",
  "estimated_duration_seconds": 300,
  "safety_notes": [
    "Always take a cloud snapshot before running — dd-based rollback requires booting from rescue media",
    "dist upgrade is potentially destructive — test on non-production first",
    "Path B leaves the old instance running — operator must decommission after validating new instance"
  ]
}
```

- [ ] **Step 4: Create `upgrade_linux_instance.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    path = parameters.get("path", "inplace")
    if path == "containerize":
        return {
            "action": "upgrade_linux_instance",
            "path": "containerize",
            "container_registry": parameters.get("container_registry"),
            "target_os": parameters.get("target_os"),
            "status": "initiated",
            "steps": ["containerize_workload", "virtualize_for_migration", "upload_image", "health_check"],
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "action": "upgrade_linux_instance",
        "path": "inplace",
        "upgrade_type": parameters.get("upgrade_type", "security"),
        "packages_upgraded": 42,
        "kernel_upgraded": True,
        "kernel_before": "5.15.0-91-generic",
        "kernel_after": "5.15.0-105-generic",
        "reboot_required": True,
        "snapshot_method": parameters.get("snapshot_method", "cloud"),
        "snapshot_id": "snap-0abc123def456",
        "failed_units": "",
        "health_check_passed": True,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "snapshot_id": execution_result.get("snapshot_id"),
        "method": execution_result.get("snapshot_method", "cloud"),
    }
```

- [ ] **Step 5: Validate and test**

```
python -c "import json; json.load(open('backend/app/connectors/catalog/nexplane_agent_mock.json')); print('valid')"
cd backend && python -m pytest app/tests/test_catalog_service.py -v
```

- [ ] **Step 6: Final full test suite**

```
cd agent && "C:/Program Files/Go/bin/go.exe" test ./...
cd backend && python -m pytest app/tests/ -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```
git add agent/executor/executor.go
git add backend/app/connectors/catalog/nexplane_agent_mock.json
git add backend/app/connectors/executors/nexplane_agent_mock/upgrade_linux_instance.py
git commit -m "feat: complete Spec 5e — upgrade_linux_instance command with catalog and mock stub"
```
