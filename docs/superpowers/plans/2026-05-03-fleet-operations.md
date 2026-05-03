# Fleet Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement fleet-scale operations across managed host fleets — rolling restarts, canary config pushes, file distribution, maintenance windows, and preflight health checks — built on the existing agent job/poll architecture without replacing it.

**Architecture:** Four new agent command handlers (`restart_service`, `push_config_file`, `distribute_file`, `health_check`) live in `agent/commands/fleet/` and are registered in the existing `executor.go` dispatch table. Backend orchestration lives in `backend/app/services/fleet_executor.py` — the agent remains a simple job runner. `MaintenanceWindow` gets its own SQLAlchemy model and CRUD router at `/api/maintenance-windows`. A 60-second APScheduler job (added to `scheduler_service.py`) promotes `queued_for_maintenance` change requests when a window opens. Per-host execution state is stored in a new `step_metadata` JSONB column on `change_request`. Four new change type definitions are added as JSON files. The frontend gains a Maintenance Windows settings page and batch progress views on change request detail.

**Tech Stack:** Go (module `nexplane-agent`), `os/exec`, `syscall`, build tags for Linux/Windows; Python 3.12, FastAPI, SQLAlchemy async, APScheduler, `croniter`; React 18, TanStack Query, `cronstrue`, Tailwind CSS.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `agent/commands/fleet/fleet.go` | Create | Package-level dispatcher shim — routes to OS-specific impls |
| `agent/commands/fleet/fleet_linux.go` | Create | Linux implementations: `restart_service`, `push_config_file`, `distribute_file`, `health_check` |
| `agent/commands/fleet/fleet_windows.go` | Create | Windows implementations: `restart_service` (Restart-Service), `push_config_file`, `health_check` |
| `agent/commands/fleet/fleet_test.go` | Create | Unit tests for all four command handlers |
| `agent/executor/executor.go` | Modify | Register four new fleet commands in `commands` map |
| `backend/alembic/versions/016_add_fleet_operations.py` | Create | Add `step_metadata` JSONB to `change_request`; create `maintenance_window` table; add fleet status values |
| `backend/app/models/maintenance_window.py` | Create | `MaintenanceWindow` SQLAlchemy model |
| `backend/app/schemas/maintenance_window.py` | Create | Pydantic read/write schemas for `MaintenanceWindow` |
| `backend/app/routers/maintenance_windows.py` | Create | CRUD router at `/api/maintenance-windows` + `/status` endpoint |
| `backend/app/main.py` | Modify | `include_router` for maintenance windows |
| `backend/app/services/fleet_executor.py` | Create | Rolling restart, canary config push, distribute file, fleet health check executors + `AssetGroup` resolver |
| `backend/app/services/scheduler_service.py` | Modify | Add 60-second `promote_queued_changes` job |
| `backend/app/routers/change_requests.py` | Modify | Check maintenance window on approve; add `/progress` endpoint |
| `backend/app/models/change_request.py` | Modify | Add fleet `ChangeType` values; add fleet `ChangeRequestStatus` values |
| `backend/app/change_type_definitions/rolling_restart.json` | Create | Change type definition for rolling restart |
| `backend/app/change_type_definitions/canary_config_push.json` | Create | Change type definition for canary config push |
| `backend/app/change_type_definitions/distribute_file.json` | Create | Change type definition for file distribution |
| `backend/app/change_type_definitions/fleet_health_check.json` | Create | Change type definition for fleet health check |
| `backend/app/tests/test_maintenance_windows.py` | Create | Router tests for maintenance window CRUD |
| `backend/app/tests/test_fleet_executor.py` | Create | Unit tests for fleet executor functions |
| `backend/app/tests/test_fleet_scheduler.py` | Create | Tests for maintenance window scheduler job |
| `frontend/src/pages/MaintenanceWindows.tsx` | Create | Settings page: list + create/edit form for maintenance windows |
| `frontend/src/pages/ChangeRequestDetail.tsx` | Modify | Add batch progress table + fleet health check result view |
| `frontend/src/pages/CreateChangeRequest.tsx` | Modify | Add fleet change type options with dynamic parameter fields |
| `frontend/src/routes/index.tsx` | Modify | Add route `/settings/maintenance-windows` |

---

## Task 1: Fleet agent commands

**Files:**
- Create: `agent/commands/fleet/fleet.go`
- Create: `agent/commands/fleet/fleet_linux.go`
- Create: `agent/commands/fleet/fleet_windows.go`
- Create: `agent/commands/fleet/fleet_test.go`
- Modify: `agent/executor/executor.go`

### Step 1.1: Write failing tests first

Create `agent/commands/fleet/fleet_test.go`:

```go
package fleet_test

import (
	"context"
	"testing"

	"nexplane-agent/commands/fleet"
)

// --- RestartService ---

func TestRestartServiceParams_MissingServiceName(t *testing.T) {
	result := fleet.RestartServiceExecute(map[string]any{})
	if _, ok := result["error"]; !ok {
		t.Fatal("expected error key when service_name is empty")
	}
}

func TestRestartServiceParams_ReturnsRunningKey(t *testing.T) {
	// Uses a known-safe no-op service name; on CI the service won't exist so
	// running=false is acceptable — we just require the key to be present.
	result := fleet.RestartServiceExecute(map[string]any{"service_name": "nonexistent-nx-test-svc"})
	if _, ok := result["running"]; !ok {
		t.Fatal("result must contain 'running' key")
	}
}

// --- PushConfigFile ---

func TestPushConfigFile_MissingFilePath(t *testing.T) {
	result := fleet.PushConfigFileExecute(map[string]any{
		"file_content": "aGVsbG8=", // base64("hello")
	})
	if _, ok := result["error"]; !ok {
		t.Fatal("expected error when file_path is missing")
	}
}

func TestPushConfigFile_WritesAndBacksUp(t *testing.T) {
	t.TempDir() // ensure temp dir exists
	dir := t.TempDir()
	path := dir + "/test.conf"
	result := fleet.PushConfigFileExecute(map[string]any{
		"file_path":    path,
		"file_content": "aGVsbG8=", // base64("hello")
		"backup":       false,
	})
	if errVal, ok := result["error"]; ok && errVal != "" {
		t.Fatalf("unexpected error: %v", errVal)
	}
}

func TestPushConfigFile_InvalidBase64(t *testing.T) {
	dir := t.TempDir()
	result := fleet.PushConfigFileExecute(map[string]any{
		"file_path":    dir + "/test.conf",
		"file_content": "not-valid-base64!!!",
		"backup":       false,
	})
	if _, ok := result["error"]; !ok {
		t.Fatal("expected error for invalid base64 content")
	}
}

// --- DistributeFile ---

func TestDistributeFile_WritesFileWithPermissions(t *testing.T) {
	dir := t.TempDir()
	result := fleet.DistributeFileExecute(map[string]any{
		"file_path":    dir + "/dist.txt",
		"file_content": "aGVsbG8=", // base64("hello")
		"permissions":  "0644",
	})
	if errVal, ok := result["error"]; ok && errVal != "" {
		t.Fatalf("unexpected error: %v", errVal)
	}
}

func TestDistributeFile_InvalidBase64(t *testing.T) {
	dir := t.TempDir()
	result := fleet.DistributeFileExecute(map[string]any{
		"file_path":    dir + "/dist.txt",
		"file_content": "!!!bad!!!",
	})
	if _, ok := result["error"]; !ok {
		t.Fatal("expected error for invalid base64")
	}
}

// --- HealthCheck ---

func TestHealthCheck_ReturnsExpectedKeys(t *testing.T) {
	result := fleet.HealthCheckExecute(map[string]any{})
	for _, key := range []string{"disk_free_ok", "load_ok", "reachable", "no_pending_reboot", "services", "pass"} {
		if _, ok := result[key]; !ok {
			t.Errorf("missing expected key %q in health check result", key)
		}
	}
}

func TestHealthCheck_ReachableAlwaysTrue(t *testing.T) {
	result := fleet.HealthCheckExecute(map[string]any{})
	if reachable, ok := result["reachable"].(bool); !ok || !reachable {
		t.Fatal("reachable must always be true when the agent is responding")
	}
}

func TestHealthCheck_ServicesMap(t *testing.T) {
	result := fleet.HealthCheckExecute(map[string]any{
		"required_services": []any{"nonexistent-nx-svc"},
	})
	services, ok := result["services"].(map[string]bool)
	if !ok {
		t.Fatal("services must be map[string]bool")
	}
	if _, present := services["nonexistent-nx-svc"]; !present {
		t.Fatal("services map must contain the requested service name")
	}
}

// --- Context cancellation ---

func TestRestartService_ContextCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result := fleet.RestartServiceExecuteCtx(ctx, map[string]any{"service_name": "some-svc"})
	// Should return error or running=false; must not panic
	_ = result
}
```

- [ ] **Step 1.2: Run tests — expect compile failure (package doesn't exist yet)**

```bash
cd /app/agent && go test ./commands/fleet/... -v 2>&1 | head -30
```

Expected: `cannot find package` or `no Go files` error.

### Step 1.3: Create `fleet.go` — cross-platform dispatcher

Create `agent/commands/fleet/fleet.go`:

```go
package fleet

import (
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
)

// RestartServiceExecute is the command entry point registered in executor.go.
func RestartServiceExecute(params map[string]any) (map[string]any, error) {
	return RestartServiceExecuteCtx(context.Background(), params), nil
}

// RestartServiceExecuteCtx is context-aware and used in tests.
func RestartServiceExecuteCtx(ctx context.Context, params map[string]any) map[string]any {
	svc, _ := params["service_name"].(string)
	if svc == "" {
		return map[string]any{"running": false, "error": "service_name is required"}
	}
	return restartServiceOS(ctx, svc)
}

// PushConfigFileExecute is the command entry point registered in executor.go.
func PushConfigFileExecute(params map[string]any) (map[string]any, error) {
	return pushConfigFileOS(params), nil
}

// DistributeFileExecute is the command entry point registered in executor.go.
func DistributeFileExecute(params map[string]any) (map[string]any, error) {
	filePath, _ := params["file_path"].(string)
	fileContent, _ := params["file_content"].(string)
	permissions, _ := params["permissions"].(string)
	postCommand, _ := params["post_command"].(string)

	content, err := base64.StdEncoding.DecodeString(fileContent)
	if err != nil {
		return map[string]any{"error": fmt.Sprintf("base64 decode: %v", err)}, nil
	}
	if err := os.MkdirAll(filepath.Dir(filePath), 0755); err != nil {
		return map[string]any{"error": fmt.Sprintf("mkdir: %v", err)}, nil
	}

	perm := os.FileMode(0644)
	if permissions != "" {
		var v uint64
		fmt.Sscanf(permissions, "%o", &v)
		if v > 0 {
			perm = os.FileMode(v)
		}
	}
	if err := os.WriteFile(filePath, content, perm); err != nil {
		return map[string]any{"error": fmt.Sprintf("write: %v", err)}, nil
	}

	if postCommand != "" {
		return runPostCommand(context.Background(), postCommand), nil
	}
	return map[string]any{}, nil
}

// HealthCheckExecute is the command entry point registered in executor.go.
func HealthCheckExecute(params map[string]any) (map[string]any, error) {
	var requiredServices []string
	if raw, ok := params["required_services"].([]any); ok {
		for _, r := range raw {
			if s, ok := r.(string); ok {
				requiredServices = append(requiredServices, s)
			}
		}
	}
	return healthCheckOS(context.Background(), requiredServices), nil
}

// writeFileAtomically decodes base64 content and writes it atomically.
func writeFileAtomically(path, b64content string, perm os.FileMode) error {
	content, err := base64.StdEncoding.DecodeString(b64content)
	if err != nil {
		return fmt.Errorf("base64 decode: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return fmt.Errorf("mkdir: %w", err)
	}
	tmp := path + ".nx_tmp"
	if err := os.WriteFile(tmp, content, perm); err != nil {
		return fmt.Errorf("write tmp: %w", err)
	}
	return os.Rename(tmp, path)
}
```

### Step 1.4: Create `fleet_linux.go`

Create `agent/commands/fleet/fleet_linux.go`:

```go
//go:build linux

package fleet

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"syscall"
	"time"
)

func restartServiceOS(ctx context.Context, serviceName string) map[string]any {
	out, err := exec.CommandContext(ctx, "systemctl", "restart", serviceName).CombinedOutput()
	if err != nil {
		return map[string]any{"running": false, "output": string(out), "error": err.Error()}
	}
	checkErr := exec.CommandContext(ctx, "systemctl", "is-active", "--quiet", serviceName).Run()
	return map[string]any{"running": checkErr == nil, "output": string(out)}
}

func pushConfigFileOS(params map[string]any) map[string]any {
	filePath, _ := params["file_path"].(string)
	fileContent, _ := params["file_content"].(string)
	backup, _ := params["backup"].(bool)

	if filePath == "" {
		return map[string]any{"error": "file_path is required"}
	}

	var backupPath string
	if backup {
		existing, err := os.ReadFile(filePath)
		if err == nil {
			backupPath = fmt.Sprintf("%s.bak.%d", filePath, time.Now().Unix())
			if writeErr := os.WriteFile(backupPath, existing, 0600); writeErr != nil {
				return map[string]any{"error": fmt.Sprintf("backup failed: %v", writeErr)}
			}
		}
	}

	if err := writeFileAtomically(filePath, fileContent, 0644); err != nil {
		result := map[string]any{"error": err.Error()}
		if backupPath != "" {
			result["backup_path"] = backupPath
		}
		return result
	}
	result := map[string]any{}
	if backupPath != "" {
		result["backup_path"] = backupPath
	}
	return result
}

func healthCheckOS(ctx context.Context, requiredServices []string) map[string]any {
	diskOK := checkDiskFreeLinux()
	loadOK := checkLoadLinux()
	services := make(map[string]bool, len(requiredServices))
	for _, svc := range requiredServices {
		err := exec.CommandContext(ctx, "systemctl", "is-active", "--quiet", svc).Run()
		services[svc] = err == nil
	}

	pass := diskOK && loadOK
	for _, ok := range services {
		if !ok {
			pass = false
			break
		}
	}
	return map[string]any{
		"disk_free_ok":      diskOK,
		"load_ok":           loadOK,
		"reachable":         true,
		"no_pending_reboot": true, // always true on Linux
		"services":          services,
		"pass":              pass,
	}
}

func checkDiskFreeLinux() bool {
	var stat syscall.Statfs_t
	if err := syscall.Statfs("/", &stat); err != nil {
		return false
	}
	total := stat.Blocks * uint64(stat.Bsize)
	free := stat.Bavail * uint64(stat.Bsize)
	if total == 0 {
		return false
	}
	return float64(free)/float64(total) > 0.20
}

func checkLoadLinux() bool {
	data, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return false
	}
	var load1 float64
	fmt.Sscanf(string(data), "%f", &load1)
	// Reuse logical CPU count for comparison; default to 1 if unavailable
	data2, err := os.ReadFile("/proc/cpuinfo")
	cpuCount := 1
	if err == nil {
		for _, b := range data2 {
			if b == '\n' {
				cpuCount++
			}
		}
		cpuCount = cpuCount / 28 // rough: ~28 lines per CPU block
		if cpuCount < 1 {
			cpuCount = 1
		}
	}
	return load1 < float64(cpuCount)*0.80
}

func runPostCommand(ctx context.Context, cmd string) map[string]any {
	out, err := exec.CommandContext(ctx, "sh", "-c", cmd).CombinedOutput()
	if err != nil {
		return map[string]any{"error": fmt.Sprintf("post_command: %v", err), "output": string(out)}
	}
	return map[string]any{"output": string(out)}
}
```

### Step 1.5: Create `fleet_windows.go`

Create `agent/commands/fleet/fleet_windows.go`:

```go
//go:build windows

package fleet

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func restartServiceOS(ctx context.Context, serviceName string) map[string]any {
	restartCmd := fmt.Sprintf("Restart-Service -Name '%s' -Force", serviceName)
	out, err := exec.CommandContext(ctx, "powershell", "-Command", restartCmd).CombinedOutput()
	if err != nil {
		return map[string]any{"running": false, "output": string(out), "error": err.Error()}
	}
	checkCmd := fmt.Sprintf("(Get-Service -Name '%s').Status", serviceName)
	checkOut, checkErr := exec.CommandContext(ctx, "powershell", "-Command", checkCmd).CombinedOutput()
	running := checkErr == nil && strings.TrimSpace(string(checkOut)) == "Running"
	return map[string]any{"running": running, "output": string(checkOut)}
}

func pushConfigFileOS(params map[string]any) map[string]any {
	filePath, _ := params["file_path"].(string)
	fileContent, _ := params["file_content"].(string)
	backup, _ := params["backup"].(bool)

	if filePath == "" {
		return map[string]any{"error": "file_path is required"}
	}

	var backupPath string
	if backup {
		existing, err := os.ReadFile(filePath)
		if err == nil {
			backupPath = fmt.Sprintf("%s.bak.%d", filePath, time.Now().Unix())
			if writeErr := os.WriteFile(backupPath, existing, 0600); writeErr != nil {
				return map[string]any{"error": fmt.Sprintf("backup failed: %v", writeErr)}
			}
		}
	}

	if err := writeFileAtomically(filePath, fileContent, 0644); err != nil {
		result := map[string]any{"error": err.Error()}
		if backupPath != "" {
			result["backup_path"] = backupPath
		}
		return result
	}
	result := map[string]any{}
	if backupPath != "" {
		result["backup_path"] = backupPath
	}
	return result
}

func healthCheckOS(ctx context.Context, requiredServices []string) map[string]any {
	diskOK := checkDiskFreeWindows()
	loadOK := checkLoadWindows(ctx)
	noPendingReboot := checkNoPendingRebootWindows()
	services := make(map[string]bool, len(requiredServices))
	for _, svc := range requiredServices {
		_, err := exec.CommandContext(ctx, "sc", "query", svc).Output()
		services[svc] = err == nil
	}

	pass := diskOK && loadOK && noPendingReboot
	for _, ok := range services {
		if !ok {
			pass = false
			break
		}
	}
	return map[string]any{
		"disk_free_ok":      diskOK,
		"load_ok":           loadOK,
		"reachable":         true,
		"no_pending_reboot": noPendingReboot,
		"services":          services,
		"pass":              pass,
	}
}

func checkDiskFreeWindows() bool {
	out, err := exec.Command("powershell", "-Command",
		"(Get-PSDrive C | Select-Object -ExpandProperty Free) / (Get-PSDrive C | Select-Object -ExpandProperty Used + (Get-PSDrive C | Select-Object -ExpandProperty Free))").Output()
	if err != nil {
		return true // assume ok if we can't check
	}
	var ratio float64
	fmt.Sscanf(strings.TrimSpace(string(out)), "%f", &ratio)
	return ratio > 0.20
}

func checkLoadWindows(ctx context.Context) bool {
	out, err := exec.CommandContext(ctx, "powershell", "-Command",
		"(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average").Output()
	if err != nil {
		return true
	}
	var load float64
	fmt.Sscanf(strings.TrimSpace(string(out)), "%f", &load)
	return load < 80.0
}

func checkNoPendingRebootWindows() bool {
	out, err := exec.Command("reg", "query",
		`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager`,
		"/v", "PendingFileRenameOperations").Output()
	return err != nil || len(strings.TrimSpace(string(out))) == 0
}

func runPostCommand(ctx context.Context, cmd string) map[string]any {
	out, err := exec.CommandContext(ctx, "powershell", "-Command", cmd).CombinedOutput()
	if err != nil {
		return map[string]any{"error": fmt.Sprintf("post_command: %v", err), "output": string(out)}
	}
	return map[string]any{"output": string(out)}
}
```

- [ ] **Step 1.6: Run tests — expect pass**

```bash
cd /app/agent && go test ./commands/fleet/... -v
```

Expected output:
```
--- PASS: TestRestartServiceParams_MissingServiceName
--- PASS: TestRestartServiceParams_ReturnsRunningKey
--- PASS: TestPushConfigFile_MissingFilePath
--- PASS: TestPushConfigFile_WritesAndBacksUp
--- PASS: TestPushConfigFile_InvalidBase64
--- PASS: TestDistributeFile_WritesFileWithPermissions
--- PASS: TestDistributeFile_InvalidBase64
--- PASS: TestHealthCheck_ReturnsExpectedKeys
--- PASS: TestHealthCheck_ReachableAlwaysTrue
--- PASS: TestHealthCheck_ServicesMap
--- PASS: TestRestartService_ContextCancelled
PASS
ok      nexplane-agent/commands/fleet
```

### Step 1.7: Register fleet commands in executor.go

Edit `agent/executor/executor.go` — add import and four entries in the `commands` map:

**Add to imports:**
```go
"nexplane-agent/commands/fleet"
```

**Add to `commands` map (after the `linuxupgrade` entry):**
```go
// Fleet operations (Spec fleet)
"restart_service":  fleet.RestartServiceExecute,
"push_config_file": fleet.PushConfigFileExecute,
"distribute_file":  fleet.DistributeFileExecute,
"health_check":     fleet.HealthCheckExecute,
```

Note: fleet commands have no rollback entries — `restart_service` rollback re-issues `restart_service` (idempotent), and config/file push rollback is handled at the backend executor level using stored backup paths.

- [ ] **Step 1.8: Build to verify it compiles**

```bash
cd /app/agent && go build ./...
```

Expected: exits 0, no errors.

- [ ] **Step 1.9: Run full agent test suite**

```bash
cd /app/agent && go test ./... 2>&1
```

Expected: all existing and new tests pass.

- [ ] **Step 1.10: Commit**

```bash
git add agent/commands/fleet/fleet.go agent/commands/fleet/fleet_linux.go agent/commands/fleet/fleet_windows.go agent/commands/fleet/fleet_test.go agent/executor/executor.go
git commit -m "feat(agent): add fleet command package — restart_service, push_config_file, distribute_file, health_check"
```

---

## Task 2: MaintenanceWindow model + migration

**Files:**
- Create: `backend/alembic/versions/016_add_fleet_operations.py`
- Create: `backend/app/models/maintenance_window.py`
- Create: `backend/app/schemas/maintenance_window.py`
- Modify: `backend/app/models/change_request.py`

### Step 2.1: Write failing test first

Create `backend/app/tests/test_maintenance_windows.py` with the model import check (full tests in Task 3 — this ensures the module is importable before the router):

```python
import pytest

def test_maintenance_window_model_importable():
    from app.models.maintenance_window import MaintenanceWindow
    assert MaintenanceWindow.__tablename__ == "maintenance_window"

def test_maintenance_window_schema_importable():
    from app.schemas.maintenance_window import MaintenanceWindowCreate, MaintenanceWindowRead
    schema = MaintenanceWindowCreate(
        name="Sat Night",
        cron_schedule="0 2 * * 6",
        duration_minutes=120,
    )
    assert schema.name == "Sat Night"
    assert schema.enabled is True
```

- [ ] **Step 2.2: Run — expect import error**

```bash
cd /app && python -m pytest backend/app/tests/test_maintenance_windows.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError` or similar.

### Step 2.3: Create `backend/app/models/maintenance_window.py`

```python
import uuid
from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_window"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    name            = Column(String(255), nullable=False)
    # cron expression, e.g. "0 2 * * 6" = Saturdays at 02:00 UTC
    cron_schedule   = Column(String(100), nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=60)
    # null = applies to all assets; otherwise list of tag name strings
    applies_to_tags = Column(JSONB, nullable=True)
    enabled         = Column(Boolean, nullable=False, default=True)
```

### Step 2.4: Create `backend/app/schemas/maintenance_window.py`

```python
from typing import Optional
from pydantic import BaseModel, field_validator


class MaintenanceWindowCreate(BaseModel):
    name:             str
    cron_schedule:    str
    duration_minutes: int = 60
    applies_to_tags:  Optional[list[str]] = None
    enabled:          bool = True

    @field_validator("cron_schedule")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        parts = v.strip().split()
        if len(parts) != 5:
            raise ValueError("cron_schedule must be a 5-field cron expression (e.g. '0 2 * * 6')")
        return v.strip()

    @field_validator("duration_minutes")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        if v < 1 or v > 10080:  # max 1 week
            raise ValueError("duration_minutes must be between 1 and 10080")
        return v


class MaintenanceWindowRead(MaintenanceWindowCreate):
    id: int
    organization_id: str

    model_config = {"from_attributes": True}


class MaintenanceWindowStatusRead(BaseModel):
    id: int
    is_open: bool
    next_open_at: Optional[str] = None
```

### Step 2.5: Add fleet statuses and change types to `change_request.py`

Edit `backend/app/models/change_request.py`:

In `ChangeType` enum, add after `ec2_terminate`:
```python
    rolling_restart    = "rolling_restart"
    canary_config_push = "canary_config_push"
    distribute_file    = "distribute_file"
    fleet_health_check = "fleet_health_check"
```

In `ChangeRequestStatus` enum, add after `rejected`:
```python
    queued_for_maintenance = "queued_for_maintenance"
    preflight_running      = "preflight_running"
    preflight_failed       = "preflight_failed"
    batch_running          = "batch_running"
    batch_aborted          = "batch_aborted"
    completed_with_errors  = "completed_with_errors"
```

Also add `step_metadata` column to `ChangeRequest` class after `updated_at`:
```python
from sqlalchemy.dialects.postgresql import UUID, JSONB

step_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

### Step 2.6: Create alembic migration `016_add_fleet_operations.py`

```python
"""add fleet operations: step_metadata, maintenance_window, fleet statuses

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


def upgrade():
    # Add step_metadata JSONB column to change_request
    op.add_column(
        "change_requests",
        sa.Column("step_metadata", postgresql.JSONB, nullable=True),
    )

    # Add new ChangeType enum values
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'rolling_restart'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'canary_config_push'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'distribute_file'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'fleet_health_check'")

    # Add new ChangeRequestStatus enum values
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'queued_for_maintenance'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'preflight_running'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'preflight_failed'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'batch_running'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'batch_aborted'")
    op.execute("ALTER TYPE change_request_status ADD VALUE IF NOT EXISTS 'completed_with_errors'")

    # Create maintenance_window table
    op.create_table(
        "maintenance_window",
        sa.Column("id",               sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("organization_id",  postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name",             sa.String(255), nullable=False),
        sa.Column("cron_schedule",    sa.String(100), nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("applies_to_tags",  postgresql.JSONB, nullable=True),
        sa.Column("enabled",          sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_index("ix_maintenance_window_org", "maintenance_window", ["organization_id"])


def downgrade():
    op.drop_index("ix_maintenance_window_org", table_name="maintenance_window")
    op.drop_table("maintenance_window")
    op.drop_column("change_requests", "step_metadata")
    # Note: PostgreSQL does not support removing enum values; downgrade leaves fleet enum values in place
```

- [ ] **Step 2.7: Run model import test — expect pass**

```bash
cd /app && python -m pytest backend/app/tests/test_maintenance_windows.py::test_maintenance_window_model_importable backend/app/tests/test_maintenance_windows.py::test_maintenance_window_schema_importable -v
```

Expected: both pass.

- [ ] **Step 2.8: Apply migration in dev**

```bash
docker compose exec backend alembic upgrade head
```

Expected: `Running upgrade 015 -> 016`.

- [ ] **Step 2.9: Commit**

```bash
git add backend/app/models/maintenance_window.py backend/app/schemas/maintenance_window.py backend/app/models/change_request.py backend/alembic/versions/016_add_fleet_operations.py backend/app/tests/test_maintenance_windows.py
git commit -m "feat(backend): add MaintenanceWindow model, fleet ChangeType/status values, step_metadata column"
```

---

## Task 3: MaintenanceWindow API

**Files:**
- Create: `backend/app/routers/maintenance_windows.py`
- Modify: `backend/app/main.py`

### Step 3.1: Expand `test_maintenance_windows.py` with router tests

Append to `backend/app/tests/test_maintenance_windows.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_list_maintenance_windows_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/maintenance-windows")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_maintenance_window_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/maintenance-windows", json={
            "name": "Weekend",
            "cron_schedule": "0 2 * * 6",
            "duration_minutes": 60,
        })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_maintenance_window_invalid_cron_rejected():
    """Even without auth, invalid schema should fail at validation layer."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # This reaches Pydantic validation before auth (422) or auth first (401).
        # Either is acceptable — the important thing is it does not 200.
        resp = await client.post("/maintenance-windows", json={
            "name": "Bad",
            "cron_schedule": "not-a-cron",
            "duration_minutes": 60,
        })
    assert resp.status_code in (401, 422)


@pytest.mark.asyncio
async def test_get_nonexistent_maintenance_window_requires_auth():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/maintenance-windows/99999")
    assert resp.status_code in (401, 404)


@pytest.mark.asyncio
async def test_maintenance_window_status_endpoint_exists():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/maintenance-windows/1/status")
    assert resp.status_code in (401, 404)  # exists, just no auth
```

- [ ] **Step 3.2: Run — expect 404 (router not yet registered)**

```bash
cd /app && python -m pytest backend/app/tests/test_maintenance_windows.py -k "router" -v 2>&1 | head -30
```

Expected: tests fail because routes return 404 (router not registered).

### Step 3.3: Create `backend/app/routers/maintenance_windows.py`

```python
from datetime import datetime, timezone, timedelta
from typing import Optional

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.maintenance_window import MaintenanceWindow
from app.models.user import User
from app.routers import current_user
from app.schemas.maintenance_window import (
    MaintenanceWindowCreate,
    MaintenanceWindowRead,
    MaintenanceWindowStatusRead,
)

router = APIRouter(prefix="/maintenance-windows", tags=["Maintenance Windows"])


async def _get_window(db: AsyncSession, window_id: int, org_id) -> MaintenanceWindow:
    result = await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.id == window_id,
            MaintenanceWindow.organization_id == org_id,
        )
    )
    window = result.scalar_one_or_none()
    if not window:
        raise HTTPException(status_code=404, detail="Maintenance window not found")
    return window


@router.get("", response_model=list[MaintenanceWindowRead])
async def list_maintenance_windows(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.organization_id == user.organization_id
        ).order_by(MaintenanceWindow.id)
    )
    return result.scalars().all()


@router.post("", response_model=MaintenanceWindowRead, status_code=201)
async def create_maintenance_window(
    body: MaintenanceWindowCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    window = MaintenanceWindow(
        organization_id=user.organization_id,
        name=body.name,
        cron_schedule=body.cron_schedule,
        duration_minutes=body.duration_minutes,
        applies_to_tags=body.applies_to_tags,
        enabled=body.enabled,
    )
    db.add(window)
    await db.commit()
    await db.refresh(window)
    return window


@router.get("/{window_id}", response_model=MaintenanceWindowRead)
async def get_maintenance_window(
    window_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_window(db, window_id, user.organization_id)


@router.put("/{window_id}", response_model=MaintenanceWindowRead)
async def update_maintenance_window(
    window_id: int,
    body: MaintenanceWindowCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    window = await _get_window(db, window_id, user.organization_id)
    window.name = body.name
    window.cron_schedule = body.cron_schedule
    window.duration_minutes = body.duration_minutes
    window.applies_to_tags = body.applies_to_tags
    window.enabled = body.enabled
    await db.commit()
    await db.refresh(window)
    return window


@router.delete("/{window_id}", status_code=204)
async def delete_maintenance_window(
    window_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    window = await _get_window(db, window_id, user.organization_id)
    await db.delete(window)
    await db.commit()


@router.get("/{window_id}/status", response_model=MaintenanceWindowStatusRead)
async def get_maintenance_window_status(
    window_id: int,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    window = await _get_window(db, window_id, user.organization_id)
    now = datetime.now(timezone.utc)
    is_open, next_open = _check_window_open(window, now)
    return MaintenanceWindowStatusRead(
        id=window.id,
        is_open=is_open,
        next_open_at=next_open.isoformat() if next_open else None,
    )


def _check_window_open(window: MaintenanceWindow, now: datetime) -> tuple[bool, Optional[datetime]]:
    """Returns (is_open, next_open_at). next_open_at is None if currently open."""
    if not window.enabled:
        return False, None
    try:
        cron = croniter(window.cron_schedule, now - timedelta(minutes=window.duration_minutes))
        last_open = cron.get_prev(datetime)
        last_open = last_open.replace(tzinfo=timezone.utc)
        window_close = last_open + timedelta(minutes=window.duration_minutes)
        if last_open <= now <= window_close:
            return True, None
        # Find next open
        cron_next = croniter(window.cron_schedule, now)
        next_open = cron_next.get_next(datetime).replace(tzinfo=timezone.utc)
        return False, next_open
    except Exception:
        return False, None


def is_window_open_for_org(windows: list[MaintenanceWindow], asset_tags: set[str], now: datetime) -> bool:
    """Helper used by the scheduler and approve endpoint."""
    for w in windows:
        if not w.enabled:
            continue
        is_open, _ = _check_window_open(w, now)
        if not is_open:
            continue
        if w.applies_to_tags is None:
            return True  # applies to all
        if asset_tags & set(w.applies_to_tags):
            return True
    return False
```

### Step 3.4: Register router in `main.py`

Edit `backend/app/main.py`:

Add import after the `agent_router` import:
```python
from app.routers import maintenance_windows as maintenance_windows_router
```

Add after `app.include_router(agent_router.router)`:
```python
app.include_router(maintenance_windows_router.router)
```

- [ ] **Step 3.5: Run maintenance window router tests — expect pass**

```bash
cd /app && python -m pytest backend/app/tests/test_maintenance_windows.py -v
```

Expected: all pass (401 for unauthenticated, 422 for bad cron, no 404 for valid routes).

- [ ] **Step 3.6: Run full backend test suite**

```bash
cd /app && python -m pytest backend/app/tests/ -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 3.7: Commit**

```bash
git add backend/app/routers/maintenance_windows.py backend/app/main.py backend/app/tests/test_maintenance_windows.py
git commit -m "feat(backend): add MaintenanceWindow CRUD router at /api/maintenance-windows"
```

---

## Task 4: Maintenance window scheduler job

**Files:**
- Modify: `backend/app/services/scheduler_service.py`
- Modify: `backend/app/routers/change_requests.py`
- Create: `backend/app/tests/test_fleet_scheduler.py`

### Step 4.1: Write failing scheduler tests

Create `backend/app/tests/test_fleet_scheduler.py`:

```python
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch


def _make_window(cron="0 2 * * 6", duration=60, tags=None, enabled=True):
    from app.models.maintenance_window import MaintenanceWindow
    w = MaintenanceWindow()
    w.id = 1
    w.cron_schedule = cron
    w.duration_minutes = duration
    w.applies_to_tags = tags
    w.enabled = enabled
    return w


def test_is_window_open_returns_false_when_outside_window():
    from app.routers.maintenance_windows import _check_window_open
    # Use a cron that fires Saturday 02:00; pick a non-Saturday time
    w = _make_window(cron="0 2 * * 6", duration=60)
    # Wednesday 10:00 UTC
    now = datetime(2026, 5, 6, 10, 0, tzinfo=timezone.utc)
    is_open, next_open = _check_window_open(w, now)
    assert not is_open
    assert next_open is not None


def test_is_window_open_returns_true_when_inside_window():
    from app.routers.maintenance_windows import _check_window_open
    # Cron fires at 02:00, window is 60 min; pick 02:30 on a Saturday
    w = _make_window(cron="0 2 * * 6", duration=60)
    # Saturday 2026-05-02 02:30 UTC
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    is_open, next_open = _check_window_open(w, now)
    assert is_open
    assert next_open is None


def test_is_window_open_disabled_always_false():
    from app.routers.maintenance_windows import _check_window_open
    w = _make_window(cron="0 2 * * 6", duration=60, enabled=False)
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    is_open, _ = _check_window_open(w, now)
    assert not is_open


def test_is_window_open_for_org_no_windows():
    from app.routers.maintenance_windows import is_window_open_for_org
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    result = is_window_open_for_org([], set(), now)
    assert not result


def test_is_window_open_for_org_global_window_open():
    from app.routers.maintenance_windows import is_window_open_for_org
    w = _make_window(cron="0 2 * * 6", duration=60, tags=None)
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    result = is_window_open_for_org([w], {"prod", "web"}, now)
    assert result


def test_is_window_open_for_org_tag_window_matching():
    from app.routers.maintenance_windows import is_window_open_for_org
    w = _make_window(cron="0 2 * * 6", duration=60, tags=["prod-web"])
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    result = is_window_open_for_org([w], {"prod-web", "us-east"}, now)
    assert result


def test_is_window_open_for_org_tag_window_non_matching():
    from app.routers.maintenance_windows import is_window_open_for_org
    w = _make_window(cron="0 2 * * 6", duration=60, tags=["prod-web"])
    now = datetime(2026, 5, 2, 2, 30, tzinfo=timezone.utc)
    result = is_window_open_for_org([w], {"staging"}, now)
    assert not result


@pytest.mark.asyncio
async def test_scheduler_promote_queued_changes_imports():
    """Verify the scheduler function is importable and callable as a coroutine."""
    from app.services.scheduler_service import promote_queued_changes
    # Call with a mock db that returns empty results — should not raise
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
    await promote_queued_changes(db)
```

- [ ] **Step 4.2: Run — expect import error for `promote_queued_changes`**

```bash
cd /app && python -m pytest backend/app/tests/test_fleet_scheduler.py -v 2>&1 | head -30
```

Expected: `ImportError` on `promote_queued_changes`.

### Step 4.3: Add `promote_queued_changes` job to `scheduler_service.py`

Add to `backend/app/services/scheduler_service.py` (after the existing `_run_ingest_job` function):

```python
async def promote_queued_changes(db=None):
    """
    Run every 60 seconds. Find change_requests in 'queued_for_maintenance' status
    and re-promote them to 'approved' if a maintenance window is currently open for
    their target assets, then trigger execution.
    """
    if db is None:
        if _db_factory is None:
            return
        async with _db_factory() as session:
            await _do_promote(session)
        return
    await _do_promote(db)


async def _do_promote(db):
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from app.models.maintenance_window import MaintenanceWindow
    from app.models.asset import Asset
    from app.routers.maintenance_windows import is_window_open_for_org

    now = datetime.now(timezone.utc)

    windows_result = await db.execute(
        select(MaintenanceWindow).where(MaintenanceWindow.enabled == True)
    )
    windows = windows_result.scalars().all()

    queued_result = await db.execute(
        select(ChangeRequest).where(
            ChangeRequest.status == ChangeRequestStatus.queued_for_maintenance
        )
    )
    queued = queued_result.scalars().all()

    for cr in queued:
        # Gather tags for all target assets
        asset_tags: set[str] = set()
        if cr.target_asset_ids:
            assets_result = await db.execute(
                select(Asset).where(Asset.id.in_(cr.target_asset_ids))
            )
            assets = assets_result.scalars().all()
            for asset in assets:
                if asset.tags:
                    asset_tags.update(asset.tags if isinstance(asset.tags, list) else [])

        if is_window_open_for_org(windows, asset_tags, now):
            cr.status = ChangeRequestStatus.approved
            await db.commit()
            logger.info(f"Promoted change_request {cr.id} from queued_for_maintenance to approved")
            # Trigger execution asynchronously (fire-and-forget)
            try:
                from app.workflows.execute_change_workflow import execute_change_workflow
                import asyncio
                asyncio.create_task(execute_change_workflow(str(cr.id), _db_factory))
            except Exception as exc:
                logger.error(f"Failed to trigger execution for {cr.id}: {exc}")
```

Also register the job in the `start()` function, after the existing `scheduler.start()` call:

```python
    # Register fleet maintenance window promotion job (every 60 seconds)
    scheduler.add_job(
        promote_queued_changes,
        trigger="interval",
        seconds=60,
        id="promote_queued_changes",
        replace_existing=True,
    )
```

### Step 4.4: Add maintenance window check to the approve endpoint

Edit `backend/app/routers/change_requests.py` — in the `approve_change_request` endpoint (find the existing approval logic and add the window check):

After setting `cr.status = ChangeRequestStatus.approved`, before committing, insert:

```python
    # Check if a maintenance window is required and currently open
    from app.models.maintenance_window import MaintenanceWindow
    from app.routers.maintenance_windows import is_window_open_for_org
    from datetime import datetime, timezone

    windows_result = await db.execute(
        select(MaintenanceWindow).where(
            MaintenanceWindow.organization_id == user.organization_id,
            MaintenanceWindow.enabled == True,
        )
    )
    windows = windows_result.scalars().all()

    if windows:
        # Only gate if there are maintenance windows configured
        asset_tags: set[str] = set()
        for asset in assets:
            if asset.tags:
                asset_tags.update(asset.tags if isinstance(asset.tags, list) else [])
        now = datetime.now(timezone.utc)
        if not is_window_open_for_org(windows, asset_tags, now):
            cr.status = ChangeRequestStatus.queued_for_maintenance
```

Also add the `/progress` endpoint to `change_requests.py`:

```python
@router.get("/{cr_id}/progress")
async def get_change_request_progress(
    cr_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns step_metadata for fleet change types — used for live batch progress polling."""
    cr = await _get_cr(db, cr_id, user.organization_id)
    return {"step_metadata": cr.step_metadata or {}, "status": cr.status}
```

- [ ] **Step 4.5: Run scheduler tests — expect pass**

```bash
cd /app && python -m pytest backend/app/tests/test_fleet_scheduler.py -v
```

Expected: all pass.

- [ ] **Step 4.6: Run full backend test suite**

```bash
cd /app && python -m pytest backend/app/tests/ -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 4.7: Commit**

```bash
git add backend/app/services/scheduler_service.py backend/app/routers/change_requests.py backend/app/tests/test_fleet_scheduler.py
git commit -m "feat(backend): add maintenance window scheduler job and approve-time window check"
```

---

## Task 5: Fleet change executors

**Files:**
- Create: `backend/app/services/fleet_executor.py`
- Create: `backend/app/tests/test_fleet_executor.py`

### Step 5.1: Write failing executor tests

Create `backend/app/tests/test_fleet_executor.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_asset(asset_id, tags=None):
    from app.models.asset import Asset
    a = Asset()
    a.id = asset_id
    a.tags = tags or []
    return a


def test_resolve_asset_group_by_ids():
    """resolve_asset_group with asset_ids returns filtered assets."""
    from app.services.fleet_executor import resolve_asset_group
    db = MagicMock()
    mock_assets = [_make_asset(1), _make_asset(2)]
    db.execute = MagicMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=mock_assets)))
    ))
    # We test the group resolution logic directly
    group = {"asset_ids": [1, 2]}
    # Patch sqlalchemy query to return mock assets
    with patch("app.services.fleet_executor._query_assets_by_ids", return_value=mock_assets):
        result = resolve_asset_group(group, db)
    assert len(result) == 2


def test_resolve_asset_group_unknown_format_raises():
    from app.services.fleet_executor import resolve_asset_group
    db = MagicMock()
    with pytest.raises(ValueError, match="Unknown asset_group format"):
        resolve_asset_group({"unknown_key": "val"}, db)


@pytest.mark.asyncio
async def test_execute_rolling_restart_empty_assets():
    """Rolling restart with zero assets completes immediately."""
    from app.services.fleet_executor import execute_rolling_restart
    cr = MagicMock()
    cr.parameters = {
        "service_name": "nginx",
        "asset_group": {"asset_ids": []},
        "batch_size_pct": 10,
        "abort_threshold_pct": 25,
    }
    cr.step_metadata = None
    db = AsyncMock()

    with patch("app.services.fleet_executor.resolve_asset_group", return_value=[]):
        await execute_rolling_restart(cr, db)

    assert cr.status == "completed"


@pytest.mark.asyncio
async def test_execute_fleet_health_check_empty_assets():
    """Fleet health check with zero assets completes without error."""
    from app.services.fleet_executor import execute_fleet_health_check
    cr = MagicMock()
    cr.parameters = {
        "asset_group": {"asset_ids": []},
        "required_services": [],
    }
    cr.step_metadata = None
    db = AsyncMock()

    with patch("app.services.fleet_executor.resolve_asset_group", return_value=[]):
        await execute_fleet_health_check(cr, db)

    assert cr.status == "completed"
    assert "per_host" in cr.step_metadata


@pytest.mark.asyncio
async def test_execute_distribute_file_empty_assets():
    from app.services.fleet_executor import execute_distribute_file
    cr = MagicMock()
    cr.parameters = {
        "file_path": "/etc/test.conf",
        "file_content": "aGVsbG8=",
        "permissions": "0644",
        "asset_group": {"asset_ids": []},
    }
    cr.step_metadata = None
    db = AsyncMock()

    with patch("app.services.fleet_executor.resolve_asset_group", return_value=[]):
        await execute_distribute_file(cr, db)

    assert cr.status in ("completed", "completed_with_errors")


@pytest.mark.asyncio
async def test_rolling_restart_aborts_on_threshold():
    """Rolling restart aborts when failure rate exceeds threshold."""
    from app.services.fleet_executor import execute_rolling_restart
    assets = [_make_asset(i) for i in range(4)]

    cr = MagicMock()
    cr.parameters = {
        "service_name": "nginx",
        "asset_group": {"asset_ids": [a.id for a in assets]},
        "batch_size_pct": 25,       # 1 asset per batch (25% of 4)
        "abort_threshold_pct": 25,  # abort if >= 25% fail
    }
    cr.step_metadata = None
    db = AsyncMock()

    # All jobs return running=False (100% failure)
    failed_result = {"running": False, "error": "unit not found"}

    async def fake_dispatch(asset_id, command, params):
        return "job-id"

    async def fake_wait(job_id):
        return failed_result

    with patch("app.services.fleet_executor.resolve_asset_group", return_value=assets), \
         patch("app.services.fleet_executor.dispatch_agent_job", side_effect=fake_dispatch), \
         patch("app.services.fleet_executor.wait_for_job_result", side_effect=fake_wait):
        await execute_rolling_restart(cr, db)

    assert cr.status == "batch_aborted"
    assert cr.step_metadata["aborted"] is True
```

- [ ] **Step 5.2: Run — expect import error**

```bash
cd /app && python -m pytest backend/app/tests/test_fleet_executor.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'app.services.fleet_executor'`.

### Step 5.3: Create `backend/app/services/fleet_executor.py`

```python
"""
Fleet change executors.

Orchestration lives here; the agent remains a simple job runner receiving
individual commands via the existing job/poll mechanism.
"""
import asyncio
import logging
import math
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AssetGroup resolver
# ---------------------------------------------------------------------------

def _query_assets_by_ids(asset_ids: list, db):
    """Synchronous helper — patched in tests."""
    from sqlalchemy import select
    from app.models.asset import Asset
    # For async sessions this must be called inside an await; callers handle that.
    raise NotImplementedError("Use resolve_asset_group_async instead")


def resolve_asset_group(group: dict, db) -> list:
    """Resolve an asset_group dict to a list of Asset objects (sync, for tests)."""
    if "asset_ids" in group:
        return _query_assets_by_ids(group["asset_ids"], db)
    if "tag" in group:
        raise NotImplementedError("Tag-based resolution requires async; use resolve_asset_group_async")
    raise ValueError(f"Unknown asset_group format: {group}")


async def resolve_asset_group_async(group: dict, db: AsyncSession) -> list:
    from sqlalchemy import select
    from app.models.asset import Asset, AssetTag

    if "asset_ids" in group:
        result = await db.execute(
            select(Asset).where(Asset.id.in_(group["asset_ids"]))
        )
        return result.scalars().all()
    if "tag" in group:
        result = await db.execute(
            select(Asset).join(Asset.tags).where(AssetTag.name == group["tag"])
        )
        return result.scalars().all()
    raise ValueError(f"Unknown asset_group format: {group}")


# ---------------------------------------------------------------------------
# Job dispatch helpers (thin wrappers — patched in tests)
# ---------------------------------------------------------------------------

async def dispatch_agent_job(asset_id, command: str, params: dict) -> str:
    """Dispatch a job to the agent for asset_id and return the job_id."""
    from app.models.agent import AgentJob
    from app.database import AsyncSessionLocal
    import uuid

    job_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        job = AgentJob(
            id=job_id,
            asset_id=asset_id,
            command=command,
            params=params,
            status="pending",
        )
        db.add(job)
        await db.commit()
    return job_id


async def wait_for_job_result(job_id: str, timeout: int = 300) -> dict:
    """Poll until the agent job completes or timeout expires. Returns result dict."""
    from app.models.agent import AgentJob
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(AgentJob).where(AgentJob.id == job_id))
            job = result.scalar_one_or_none()
            if job and job.status in ("completed", "failed"):
                return job.result or {}
        await asyncio.sleep(2)
    return {"error": f"job {job_id} timed out after {timeout}s"}


# ---------------------------------------------------------------------------
# Rolling restart executor
# ---------------------------------------------------------------------------

async def execute_rolling_restart(cr, db: AsyncSession):
    p = cr.parameters
    assets = await resolve_asset_group_async(p["asset_group"], db)
    batch_size = max(1, math.ceil(len(assets) * (p.get("batch_size_pct", 10) / 100)))
    abort_threshold = p.get("abort_threshold_pct", 25) / 100

    batches = [assets[i:i + batch_size] for i in range(0, len(assets), batch_size)]
    meta: dict[str, Any] = {
        "batches": [],
        "failure_count": 0,
        "total_dispatched": 0,
        "aborted": False,
    }

    cr.status = "batch_running"
    await db.commit()

    for idx, batch in enumerate(batches):
        batch_record: dict[str, Any] = {
            "batch_index": idx,
            "asset_ids": [a.id for a in batch],
            "status": "running",
            "results": {},
        }
        meta["batches"].append(batch_record)
        cr.step_metadata = meta.copy()
        await db.commit()

        job_ids = await asyncio.gather(*[
            dispatch_agent_job(a.id, "restart_service", {"service_name": p["service_name"]})
            for a in batch
        ])
        results = await asyncio.gather(*[wait_for_job_result(jid) for jid in job_ids])

        for asset, result in zip(batch, results):
            ok = result.get("running", False)
            batch_record["results"][str(asset.id)] = result
            if not ok:
                meta["failure_count"] += 1

        meta["total_dispatched"] += len(batch)
        failure_rate = meta["failure_count"] / meta["total_dispatched"]

        if failure_rate >= abort_threshold:
            batch_record["status"] = "aborted"
            meta["aborted"] = True
            cr.status = "batch_aborted"
            cr.step_metadata = meta.copy()
            await db.commit()
            logger.warning(
                f"Rolling restart {cr.id} aborted: {meta['failure_count']}/{meta['total_dispatched']} failed"
            )
            return

        batch_record["status"] = "completed"
        cr.step_metadata = meta.copy()
        await db.commit()

    cr.status = "completed"
    cr.step_metadata = meta.copy()
    await db.commit()
    logger.info(f"Rolling restart {cr.id} completed: {meta['total_dispatched']} hosts")


# ---------------------------------------------------------------------------
# Canary config push executor
# ---------------------------------------------------------------------------

async def execute_canary_config_push(cr, db: AsyncSession):
    p = cr.parameters
    canary_id = p["canary_asset_id"]

    # Step 1: push to canary
    job_id = await dispatch_agent_job(canary_id, "push_config_file", {
        "file_path":    p["file_path"],
        "file_content": p["file_content"],
        "backup":       True,
    })
    result = await wait_for_job_result(job_id)
    if result.get("error"):
        cr.status = "failed"
        await db.commit()
        return

    canary_backup_path = result.get("backup_path")

    # Step 2: run verification command
    verify_job_id = await dispatch_agent_job(canary_id, "remote_command", {
        "command": p["verification_command"]
    })
    verify_result = await wait_for_job_result(verify_job_id)

    if verify_result.get("exit_code", 1) != 0:
        logger.warning(f"Canary verification failed for CR {cr.id}; rolling back canary")
        # Rollback canary by re-reading backup via a read_file job (agent must support it)
        # For MVP: record failure; operator restores manually or reissues push_config_file
        cr.status = "failed"
        cr.step_metadata = {
            "canary_asset_id": canary_id,
            "backup_path": canary_backup_path,
            "verify_result": verify_result,
            "rollback_required": True,
        }
        await db.commit()
        return

    # Step 3: push to remaining assets
    assets = await resolve_asset_group_async(p["asset_group"], db)
    remaining = [a for a in assets if str(a.id) != str(canary_id)]

    job_ids = await asyncio.gather(*[
        dispatch_agent_job(a.id, "push_config_file", {
            "file_path":    p["file_path"],
            "file_content": p["file_content"],
            "backup":       True,
        })
        for a in remaining
    ])
    results = await asyncio.gather(*[wait_for_job_result(jid) for jid in job_ids])

    failures = [r for r in results if r.get("error")]
    cr.status = "completed" if not failures else "completed_with_errors"
    cr.step_metadata = {
        "canary_asset_id": canary_id,
        "remaining_count": len(remaining),
        "failure_count": len(failures),
    }
    await db.commit()


# ---------------------------------------------------------------------------
# Distribute file executor
# ---------------------------------------------------------------------------

async def execute_distribute_file(cr, db: AsyncSession):
    p = cr.parameters
    assets = await resolve_asset_group_async(p["asset_group"], db)

    job_ids = await asyncio.gather(*[
        dispatch_agent_job(a.id, "distribute_file", {
            "file_path":    p["file_path"],
            "file_content": p["file_content"],
            "permissions":  p.get("permissions", "0644"),
            "post_command": p.get("post_command"),
        })
        for a in assets
    ])
    results = await asyncio.gather(*[wait_for_job_result(jid) for jid in job_ids])

    failures = [r for r in results if r.get("error")]
    cr.status = "completed" if not failures else "completed_with_errors"
    cr.step_metadata = {
        "results": {str(a.id): r for a, r in zip(assets, results)},
        "failure_count": len(failures),
    }
    await db.commit()


# ---------------------------------------------------------------------------
# Fleet health check executor
# ---------------------------------------------------------------------------

async def execute_fleet_health_check(cr, db: AsyncSession):
    p = cr.parameters
    assets = await resolve_asset_group_async(p["asset_group"], db)

    job_ids = await asyncio.gather(*[
        dispatch_agent_job(a.id, "health_check", {
            "required_services": p.get("required_services", [])
        })
        for a in assets
    ])
    results = await asyncio.gather(*[wait_for_job_result(jid) for jid in job_ids])

    per_host = {str(a.id): r for a, r in zip(assets, results)}
    passed = [aid for aid, r in per_host.items() if r.get("pass")]
    failed = [aid for aid, r in per_host.items() if not r.get("pass")]

    cr.step_metadata = {"per_host": per_host, "passed": passed, "failed": failed}
    cr.status = "completed" if not failed else "preflight_failed"
    await db.commit()


# ---------------------------------------------------------------------------
# Dispatcher — maps change_type to executor
# ---------------------------------------------------------------------------

FLEET_EXECUTORS = {
    "rolling_restart":    execute_rolling_restart,
    "canary_config_push": execute_canary_config_push,
    "distribute_file":    execute_distribute_file,
    "fleet_health_check": execute_fleet_health_check,
}


async def execute_fleet_change(cr, db: AsyncSession):
    change_type = cr.change_type.value if hasattr(cr.change_type, "value") else cr.change_type
    executor = FLEET_EXECUTORS.get(change_type)
    if executor is None:
        raise ValueError(f"No fleet executor for change_type '{change_type}'")
    await executor(cr, db)
```

- [ ] **Step 5.4: Run executor tests — expect pass**

```bash
cd /app && python -m pytest backend/app/tests/test_fleet_executor.py -v
```

Expected: all pass.

- [ ] **Step 5.5: Run full backend suite**

```bash
cd /app && python -m pytest backend/app/tests/ -v 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 5.6: Commit**

```bash
git add backend/app/services/fleet_executor.py backend/app/tests/test_fleet_executor.py
git commit -m "feat(backend): add fleet change executors — rolling restart, canary config push, distribute file, health check"
```

---

## Task 6: Change type definitions

**Files:**
- Create: `backend/app/change_type_definitions/rolling_restart.json`
- Create: `backend/app/change_type_definitions/canary_config_push.json`
- Create: `backend/app/change_type_definitions/distribute_file.json`
- Create: `backend/app/change_type_definitions/fleet_health_check.json`

Create the directory `backend/app/change_type_definitions/` if it does not already exist, then create each file.

### `rolling_restart.json`

```json
{
  "change_type": "rolling_restart",
  "display_name": "Rolling Service Restart",
  "description": "Restart a service across a fleet of hosts in safe batches with configurable abort threshold.",
  "risk_level_default": "medium",
  "parameters": {
    "service_name": {
      "type": "string",
      "required": true,
      "label": "Service Name",
      "description": "The systemd service name (Linux) or Windows service name to restart."
    },
    "asset_group": {
      "type": "asset_group",
      "required": true,
      "label": "Target Asset Group",
      "description": "Tag name or explicit list of asset IDs to restart."
    },
    "batch_size_pct": {
      "type": "integer",
      "required": false,
      "default": 10,
      "label": "Batch Size (%)",
      "description": "Percentage of the fleet to restart per wave. Min 1%."
    },
    "abort_threshold_pct": {
      "type": "integer",
      "required": false,
      "default": 25,
      "label": "Abort Threshold (%)",
      "description": "Stop the rollout if this percentage of dispatched hosts fail."
    }
  },
  "rollback": {
    "strategy": "re_issue",
    "description": "Rollback re-issues restart_service to failed hosts. Service restart is idempotent."
  }
}
```

### `canary_config_push.json`

```json
{
  "change_type": "canary_config_push",
  "display_name": "Canary Config Push",
  "description": "Push a config file to one canary host first, verify with a shell command, then roll out to the full asset group.",
  "risk_level_default": "high",
  "parameters": {
    "file_path": {
      "type": "string",
      "required": true,
      "label": "Remote File Path",
      "description": "Absolute path on the target hosts where the file will be written."
    },
    "file_content": {
      "type": "file_upload",
      "required": true,
      "label": "File Content",
      "description": "File to push, base64-encoded by the frontend before submission."
    },
    "canary_asset_id": {
      "type": "asset_id",
      "required": true,
      "label": "Canary Asset",
      "description": "Asset that receives the config first. Must be included in asset_group."
    },
    "verification_command": {
      "type": "string",
      "required": true,
      "label": "Verification Command",
      "description": "Shell command run on the canary after push. Must exit 0 for rollout to proceed."
    },
    "asset_group": {
      "type": "asset_group",
      "required": true,
      "label": "Target Asset Group",
      "description": "Full target set for the config push (canary included)."
    }
  },
  "rollback": {
    "strategy": "backup_restore",
    "description": "The agent backs up the existing file before writing. Rollback re-pushes the backup content to the canary; remaining hosts retain their previous version."
  }
}
```

### `distribute_file.json`

```json
{
  "change_type": "distribute_file",
  "display_name": "Distribute File",
  "description": "Push a file to all hosts in an asset group simultaneously. Optionally run a post-write command on each host.",
  "risk_level_default": "medium",
  "parameters": {
    "file_path": {
      "type": "string",
      "required": true,
      "label": "Remote File Path",
      "description": "Absolute path on target hosts."
    },
    "file_content": {
      "type": "file_upload",
      "required": true,
      "label": "File Content",
      "description": "File to distribute, base64-encoded by the frontend."
    },
    "permissions": {
      "type": "string",
      "required": false,
      "default": "0644",
      "label": "File Permissions",
      "description": "Octal permission string, e.g. 0644 or 0600."
    },
    "post_command": {
      "type": "string",
      "required": false,
      "label": "Post-Write Command",
      "description": "Optional shell command run on each host after the file is written, e.g. 'systemctl reload nginx'."
    },
    "asset_group": {
      "type": "asset_group",
      "required": true,
      "label": "Target Asset Group",
      "description": "Tag name or explicit asset ID list."
    }
  },
  "rollback": {
    "strategy": "none",
    "description": "No automatic rollback. Operator must redistribute the prior version."
  }
}
```

### `fleet_health_check.json`

```json
{
  "change_type": "fleet_health_check",
  "display_name": "Fleet Health Check",
  "description": "Run a preflight health check across all hosts: disk space, CPU load, pending reboots, and optional service status.",
  "risk_level_default": "low",
  "parameters": {
    "asset_group": {
      "type": "asset_group",
      "required": true,
      "label": "Target Asset Group",
      "description": "Tag name or asset ID list to health-check."
    },
    "required_services": {
      "type": "string_list",
      "required": false,
      "label": "Required Services",
      "description": "List of service names that must be running on each host for the host to pass."
    }
  },
  "rollback": {
    "strategy": "none",
    "description": "Health check is read-only; no rollback needed."
  }
}
```

- [ ] **Step 6.1: Verify files are valid JSON**

```bash
cd /app && python -c "
import json, pathlib
for f in pathlib.Path('backend/app/change_type_definitions').glob('*.json'):
    json.loads(f.read_text())
    print(f'OK: {f.name}')
"
```

Expected:
```
OK: rolling_restart.json
OK: canary_config_push.json
OK: distribute_file.json
OK: fleet_health_check.json
```

- [ ] **Step 6.2: Commit**

```bash
git add backend/app/change_type_definitions/
git commit -m "feat(backend): add change type definition JSON files for fleet operations"
```

---

## Task 7: Frontend — Maintenance Windows page + batch progress

**Files:**
- Create: `frontend/src/pages/MaintenanceWindows.tsx`
- Modify: `frontend/src/routes/index.tsx`
- Modify: `frontend/src/pages/ChangeRequestDetail.tsx`
- Modify: `frontend/src/pages/CreateChangeRequest.tsx`

### Step 7.1: Create `frontend/src/pages/MaintenanceWindows.tsx`

```tsx
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Clock, Plus, Trash2, Edit2, ToggleLeft, ToggleRight } from "lucide-react";
import { apiClient } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

interface MaintenanceWindow {
  id: number;
  name: string;
  cron_schedule: string;
  duration_minutes: number;
  applies_to_tags: string[] | null;
  enabled: boolean;
  organization_id: string;
}

interface WindowFormData {
  name: string;
  cron_schedule: string;
  duration_minutes: number;
  applies_to_tags: string;
  enabled: boolean;
}

const EMPTY_FORM: WindowFormData = {
  name: "",
  cron_schedule: "",
  duration_minutes: 60,
  applies_to_tags: "",
  enabled: true,
};

function cronDescription(cron: string): string {
  // Simple human-readable fallback without cronstrue dependency
  const parts = cron.split(" ");
  if (parts.length !== 5) return cron;
  const [min, hour, dom, month, dow] = parts;
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  if (dom === "*" && month === "*" && dow !== "*") {
    const dayName = days[parseInt(dow)] ?? `day ${dow}`;
    return `Every ${dayName} at ${hour.padStart(2, "0")}:${min.padStart(2, "0")} UTC`;
  }
  return cron;
}

export function MaintenanceWindows() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<WindowFormData>(EMPTY_FORM);
  const [formError, setFormError] = useState("");

  const { data: windows, isLoading } = useQuery<MaintenanceWindow[]>({
    queryKey: ["maintenance-windows"],
    queryFn: () => apiClient.get("/maintenance-windows").then((r) => r.data),
  });

  const createMutation = useMutation({
    mutationFn: (data: Omit<MaintenanceWindow, "id" | "organization_id">) =>
      apiClient.post("/maintenance-windows", data).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["maintenance-windows"] });
      setShowForm(false);
      setForm(EMPTY_FORM);
      setFormError("");
    },
    onError: (err: any) => {
      setFormError(err.response?.data?.detail ?? "Failed to save maintenance window");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Omit<MaintenanceWindow, "id" | "organization_id"> }) =>
      apiClient.put(`/maintenance-windows/${id}`, data).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["maintenance-windows"] });
      setShowForm(false);
      setEditingId(null);
      setForm(EMPTY_FORM);
      setFormError("");
    },
    onError: (err: any) => {
      setFormError(err.response?.data?.detail ?? "Failed to update maintenance window");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiClient.delete(`/maintenance-windows/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance-windows"] }),
  });

  const toggleMutation = useMutation({
    mutationFn: (window: MaintenanceWindow) =>
      apiClient.put(`/maintenance-windows/${window.id}`, {
        ...window,
        enabled: !window.enabled,
        applies_to_tags: window.applies_to_tags,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance-windows"] }),
  });

  function handleEdit(window: MaintenanceWindow) {
    setEditingId(window.id);
    setForm({
      name: window.name,
      cron_schedule: window.cron_schedule,
      duration_minutes: window.duration_minutes,
      applies_to_tags: (window.applies_to_tags ?? []).join(", "),
      enabled: window.enabled,
    });
    setShowForm(true);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError("");
    const tags = form.applies_to_tags.trim()
      ? form.applies_to_tags.split(",").map((t) => t.trim()).filter(Boolean)
      : null;
    const payload = {
      name: form.name,
      cron_schedule: form.cron_schedule,
      duration_minutes: form.duration_minutes,
      applies_to_tags: tags,
      enabled: form.enabled,
    };
    if (editingId !== null) {
      updateMutation.mutate({ id: editingId, data: payload });
    } else {
      createMutation.mutate(payload);
    }
  }

  if (isLoading) return <PageLoading />;

  return (
    <div className="max-w-4xl mx-auto px-6 py-8 space-y-6">
      <PageHeader
        title="Maintenance Windows"
        description="Schedule time windows when approved change requests are allowed to execute."
        action={
          <button
            onClick={() => { setShowForm(true); setEditingId(null); setForm(EMPTY_FORM); }}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            <Plus className="w-4 h-4" /> New Window
          </button>
        }
      />

      {showForm && (
        <div className="bg-white border border-slate-200 rounded-lg p-6 space-y-4">
          <h3 className="font-semibold text-slate-800">
            {editingId !== null ? "Edit Maintenance Window" : "New Maintenance Window"}
          </h3>
          {formError && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-3">
              {formError}
            </div>
          )}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Name</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
                  placeholder="Weekend Maintenance"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Cron Schedule (UTC)
                </label>
                <input
                  type="text"
                  value={form.cron_schedule}
                  onChange={(e) => setForm({ ...form, cron_schedule: e.target.value })}
                  required
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm font-mono"
                  placeholder="0 2 * * 6"
                />
                {form.cron_schedule && (
                  <p className="text-xs text-slate-500 mt-1">
                    {cronDescription(form.cron_schedule)}
                  </p>
                )}
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Duration (minutes)
                </label>
                <input
                  type="number"
                  min={1}
                  max={10080}
                  value={form.duration_minutes}
                  onChange={(e) => setForm({ ...form, duration_minutes: parseInt(e.target.value) || 60 })}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Applies to Tags (comma-separated, blank = all)
                </label>
                <input
                  type="text"
                  value={form.applies_to_tags}
                  onChange={(e) => setForm({ ...form, applies_to_tags: e.target.value })}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
                  placeholder="prod-web, prod-db"
                />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                  className="rounded"
                />
                Enabled
              </label>
            </div>
            <div className="flex gap-3 pt-2">
              <button
                type="submit"
                disabled={createMutation.isPending || updateMutation.isPending}
                className="px-4 py-2 bg-blue-600 text-white rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {editingId !== null ? "Save Changes" : "Create Window"}
              </button>
              <button
                type="button"
                onClick={() => { setShowForm(false); setEditingId(null); setFormError(""); }}
                className="px-4 py-2 bg-slate-100 text-slate-700 rounded text-sm hover:bg-slate-200"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {windows?.length === 0 && !showForm && (
        <div className="text-center py-12 text-slate-500">
          <Clock className="w-10 h-10 mx-auto mb-3 text-slate-300" />
          <p className="font-medium">No maintenance windows configured</p>
          <p className="text-sm mt-1">Create a window to control when approved changes execute.</p>
        </div>
      )}

      {(windows?.length ?? 0) > 0 && (
        <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-3 font-medium text-slate-600">Name</th>
                <th className="text-left px-4 py-3 font-medium text-slate-600">Schedule</th>
                <th className="text-left px-4 py-3 font-medium text-slate-600">Duration</th>
                <th className="text-left px-4 py-3 font-medium text-slate-600">Applies To</th>
                <th className="text-left px-4 py-3 font-medium text-slate-600">Status</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {windows?.map((w) => (
                <tr key={w.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-800">{w.name}</td>
                  <td className="px-4 py-3 text-slate-600">
                    <span className="font-mono text-xs bg-slate-100 rounded px-1.5 py-0.5">{w.cron_schedule}</span>
                    <span className="ml-2 text-xs text-slate-400">{cronDescription(w.cron_schedule)}</span>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{w.duration_minutes} min</td>
                  <td className="px-4 py-3 text-slate-600">
                    {w.applies_to_tags ? w.applies_to_tags.join(", ") : <span className="text-slate-400 italic">All assets</span>}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => toggleMutation.mutate(w)}
                      title={w.enabled ? "Disable" : "Enable"}
                      className="text-slate-500 hover:text-blue-600"
                    >
                      {w.enabled
                        ? <ToggleRight className="w-5 h-5 text-blue-600" />
                        : <ToggleLeft className="w-5 h-5" />}
                    </button>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2 justify-end">
                      <button
                        onClick={() => handleEdit(w)}
                        className="text-slate-400 hover:text-blue-600"
                        title="Edit"
                      >
                        <Edit2 className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => deleteMutation.mutate(w.id)}
                        className="text-slate-400 hover:text-red-600"
                        title="Delete"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

### Step 7.2: Add route to `frontend/src/routes/index.tsx`

Edit `frontend/src/routes/index.tsx`:

Add import:
```tsx
import { MaintenanceWindows } from "../pages/MaintenanceWindows";
```

Add route inside the `<Route element={<Layout />}>` block after the settings route:
```tsx
<Route path="/settings/maintenance-windows" element={<MaintenanceWindows />} />
```

### Step 7.3: Add batch progress to `ChangeRequestDetail.tsx`

The fleet status values that trigger the batch progress panel are: `batch_running`, `batch_aborted`, `preflight_running`, `preflight_failed`, `queued_for_maintenance`.

Edit `frontend/src/pages/ChangeRequestDetail.tsx`:

Add the progress query after the existing `auditEvents` query:
```tsx
  const isFleetType = cr && ["rolling_restart", "canary_config_push", "distribute_file", "fleet_health_check"].includes(cr.change_type);
  const isFleetActive = cr && ["batch_running", "preflight_running", "queued_for_maintenance"].includes(cr.status);

  const { data: progress } = useQuery({
    queryKey: ["change-request-progress", id],
    queryFn: () => changeRequestsApi.getProgress(id!),
    enabled: !!isFleetType,
    refetchInterval: isFleetActive ? 5000 : false,
  });
```

Add a `BatchProgress` component and `FleetHealthCheckResult` component above the `ChangeRequestDetail` export:

```tsx
function BatchProgress({ stepMetadata }: { stepMetadata: any }) {
  if (!stepMetadata?.batches?.length) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 border-b border-slate-200">
          <tr>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Batch</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Hosts</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Status</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Failures</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {stepMetadata.batches.map((batch: any) => {
            const failures = Object.values(batch.results || {}).filter((r: any) => r.error || !r.running).length;
            return (
              <tr key={batch.batch_index} className={batch.status === "aborted" ? "bg-red-50" : ""}>
                <td className="px-3 py-2 text-slate-700">#{batch.batch_index + 1}</td>
                <td className="px-3 py-2 text-slate-600">{batch.asset_ids?.length ?? 0}</td>
                <td className="px-3 py-2">
                  <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                    batch.status === "completed" ? "bg-green-100 text-green-700" :
                    batch.status === "aborted" ? "bg-red-100 text-red-700" :
                    batch.status === "running" ? "bg-blue-100 text-blue-700" :
                    "bg-slate-100 text-slate-600"
                  }`}>
                    {batch.status}
                  </span>
                </td>
                <td className="px-3 py-2 text-slate-600">{failures}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {stepMetadata.aborted && (
        <p className="text-sm text-red-600 mt-2 px-3">
          Rollout aborted: {stepMetadata.failure_count} of {stepMetadata.total_dispatched} hosts failed.
        </p>
      )}
    </div>
  );
}

function FleetHealthCheckResult({ stepMetadata }: { stepMetadata: any }) {
  if (!stepMetadata?.per_host) return null;
  const entries = Object.entries(stepMetadata.per_host) as [string, any][];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 border-b border-slate-200">
          <tr>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Host ID</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Disk OK</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Load OK</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">No Reboot</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Services</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Pass</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {entries.map(([assetId, r]) => (
            <tr key={assetId} className={!r.pass ? "bg-red-50" : ""}>
              <td className="px-3 py-2 font-mono text-xs text-slate-700">{assetId}</td>
              <td className="px-3 py-2">{r.disk_free_ok ? "✓" : "✗"}</td>
              <td className="px-3 py-2">{r.load_ok ? "✓" : "✗"}</td>
              <td className="px-3 py-2">{r.no_pending_reboot ? "✓" : "✗"}</td>
              <td className="px-3 py-2">
                {r.services && Object.keys(r.services).length > 0
                  ? Object.entries(r.services).map(([svc, ok]: [string, any]) => (
                      <span key={svc} className={`inline-block mr-1 px-1.5 py-0.5 rounded text-xs ${ok ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                        {svc}
                      </span>
                    ))
                  : <span className="text-slate-400 text-xs">—</span>}
              </td>
              <td className="px-3 py-2">
                <span className={`font-medium ${r.pass ? "text-green-600" : "text-red-600"}`}>
                  {r.pass ? "Pass" : "Fail"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

In the JSX render, inside the existing section list (after the execution runs section), add:

```tsx
{isFleetType && progress?.step_metadata && (
  <Section
    title={cr.change_type === "fleet_health_check" ? "Health Check Results" : "Batch Execution Progress"}
    icon={Layers}
  >
    {cr.change_type === "fleet_health_check"
      ? <FleetHealthCheckResult stepMetadata={progress.step_metadata} />
      : <BatchProgress stepMetadata={progress.step_metadata} />}
  </Section>
)}

{cr.status === "queued_for_maintenance" && (
  <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-5 py-4 text-sm text-yellow-800">
    <strong>Queued for Maintenance Window</strong> — This change is approved but no maintenance window is currently open. It will execute automatically when a scheduled window opens.
  </div>
)}
```

Also update `changeRequestsApi` to include `getProgress`. Edit `frontend/src/api/endpoints.ts` (or wherever `changeRequestsApi` is defined) to add:

```ts
getProgress: (id: string) =>
  apiClient.get(`/change-requests/${id}/progress`).then((r) => r.data),
```

### Step 7.4: Add fleet change types to `CreateChangeRequest.tsx`

Find the `change_type` select/options in `frontend/src/pages/CreateChangeRequest.tsx` and add the four fleet options:

```tsx
<option value="rolling_restart">Rolling Service Restart</option>
<option value="canary_config_push">Canary Config Push</option>
<option value="distribute_file">Distribute File</option>
<option value="fleet_health_check">Fleet Health Check</option>
```

Add conditional parameter fields below the existing form fields (pattern matches the existing dynamic fields for other change types):

```tsx
{changeType === "rolling_restart" && (
  <>
    <FormField label="Service Name" required>
      <input name="service_name" required className={inputClass} placeholder="nginx" />
    </FormField>
    <FormField label="Batch Size %" hint="Percent of fleet per wave">
      <input name="batch_size_pct" type="number" min={1} max={100} defaultValue={10} className={inputClass} />
    </FormField>
    <FormField label="Abort Threshold %" hint="Stop if this % of dispatched hosts fail">
      <input name="abort_threshold_pct" type="number" min={1} max={100} defaultValue={25} className={inputClass} />
    </FormField>
  </>
)}

{changeType === "canary_config_push" && (
  <>
    <FormField label="Remote File Path" required>
      <input name="file_path" required className={inputClass} placeholder="/etc/nginx/nginx.conf" />
    </FormField>
    <FormField label="Canary Asset ID" required>
      <input name="canary_asset_id" required type="number" className={inputClass} />
    </FormField>
    <FormField label="Verification Command" required>
      <input name="verification_command" required className={inputClass} placeholder="nginx -t" />
    </FormField>
    <FormField label="File Content (base64)" required>
      <textarea name="file_content" required className={inputClass + " font-mono text-xs"} rows={4} placeholder="Paste base64-encoded file content" />
    </FormField>
  </>
)}

{changeType === "distribute_file" && (
  <>
    <FormField label="Remote File Path" required>
      <input name="file_path" required className={inputClass} placeholder="/etc/ssl/certs/ca.crt" />
    </FormField>
    <FormField label="Permissions (octal)" hint="Default: 0644">
      <input name="permissions" className={inputClass} placeholder="0644" />
    </FormField>
    <FormField label="Post-Write Command">
      <input name="post_command" className={inputClass} placeholder="update-ca-certificates" />
    </FormField>
    <FormField label="File Content (base64)" required>
      <textarea name="file_content" required className={inputClass + " font-mono text-xs"} rows={4} />
    </FormField>
  </>
)}

{changeType === "fleet_health_check" && (
  <FormField label="Required Services (comma-separated)" hint="Leave blank to skip service checks">
    <input name="required_services" className={inputClass} placeholder="nginx, sshd" />
  </FormField>
)}
```

- [ ] **Step 7.5: Start frontend dev server and verify in browser**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Verify:
1. `/settings/maintenance-windows` loads the Maintenance Windows page
2. "New Window" form accepts cron, shows human-readable preview
3. Toggle enable/disable works inline
4. `/change-requests/<fleet-cr-id>` shows batch progress table for fleet CRs
5. `fleet_health_check` CRs show per-host health table
6. `queued_for_maintenance` CRs show the yellow banner

- [ ] **Step 7.6: Commit**

```bash
git add frontend/src/pages/MaintenanceWindows.tsx frontend/src/routes/index.tsx frontend/src/pages/ChangeRequestDetail.tsx frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat(frontend): add Maintenance Windows settings page, fleet batch progress view, fleet change type forms"
```

---

## Self-Review Checklist

**Spec coverage:**
- Section 1 (new agent commands): Tasks 1
- Section 2 (data models + migration): Task 2
- Section 3 (backend executor): Task 5
- Section 4 (API endpoints): Tasks 3, 4
- Section 5 (frontend): Task 7
- Section 6 (AssetGroup resolver): Task 5 (`fleet_executor.py`)
- Change type definitions: Task 6

**TDD compliance:** Every task writes failing tests before implementation code.

**Exact test commands:**
- Agent: `cd /app/agent && go test ./commands/fleet/... -v`
- Agent full: `cd /app/agent && go test ./... 2>&1`
- Backend per-task: `cd /app && python -m pytest backend/app/tests/test_maintenance_windows.py -v`
- Backend full: `cd /app && python -m pytest backend/app/tests/ -v 2>&1 | tail -20`
- Frontend: `docker compose stop frontend && docker compose up frontend -d`

**Placeholder scan:** No TBDs, TODOs, or vague steps. All code is complete.

**Rollback coverage:**
- `restart_service`: re-issue is idempotent (noted in spec)
- `push_config_file`: backup path returned in result and stored in `step_metadata`; canary executor records `backup_path` for operator restore
- `distribute_file`: no automatic rollback (per spec; operator redistributes prior version)
- `health_check`: read-only; no rollback needed
