# Fleet Operations — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Controlled bulk operations across managed host fleets — rolling restarts, canary config pushes, file distribution, maintenance windows, and preflight health checks.

---

## Background

Nexplane currently dispatches change requests to individual assets. Operators managing large fleets need higher-level primitives: restart a service across 200 hosts in safe batches, push a config change to one canary host before rolling it to production, distribute a CA certificate to every host in a tag group, and guarantee that no changes execute outside a scheduled maintenance window. This spec introduces five fleet-oriented capabilities built on the existing agent job/poll architecture without replacing it.

---

## Design Decisions

- **Orchestration in the backend, not the agent:** Batch sequencing, health checks between waves, and abort logic all live in the backend change executor. The agent remains a simple job runner — it receives individual commands and reports results.
- **New agent command package `agent/commands/fleet/`:** Rolling restart, config push, file distribution, and health check are implemented as discrete command handlers registered alongside the existing command set.
- **`MaintenanceWindow` gets its own router:** Maintenance window CRUD is independent of change requests and lives under `/api/maintenance-windows`.
- **Per-host status in step metadata:** Rolling restart and canary push record per-host outcomes in the `change_request` `step_metadata` JSONB column rather than a separate table, keeping the data model simple for MVP.
- **Canary verification runs via the agent's existing `remote_command` mechanism:** No new execution path; `verification_command` is dispatched as a `remote_command` job to the canary asset and the backend checks the exit code in the result payload.
- **Rollback is always idempotent:** Service restart rollback re-issues `restart_service`. Config push rollback restores the backed-up file using `push_config_file` with the backup path as source. File distribution has no automatic rollback (operator must redistribute the prior version).
- **Maintenance window resolution is eager:** When a change request is approved, the backend checks the window immediately. If no window is open, the request transitions to `queued_for_maintenance`. A scheduler running every 60 seconds promotes eligible queued requests.

---

## Section 1: New Agent Commands

### Package layout

```
agent/
  commands/
    fleet/
      restart_service.go
      push_config_file.go
      distribute_file.go
      health_check.go
      register.go        ← registers all fleet handlers
```

`register.go` calls `commands.Register(name, handler)` for each command so they appear in the existing dispatch table.

---

### 1.1 `restart_service`

**File:** `agent/commands/fleet/restart_service.go`

```go
package fleet

import (
    "context"
    "fmt"
    "os/exec"
    "runtime"
)

// RestartServiceParams are embedded in the job payload.
type RestartServiceParams struct {
    ServiceName string `json:"service_name"`
}

// RestartServiceResult is written back to the job result payload.
type RestartServiceResult struct {
    Running bool   `json:"running"`
    Output  string `json:"output,omitempty"`
    Error   string `json:"error,omitempty"`
}

func HandleRestartService(ctx context.Context, params RestartServiceParams) RestartServiceResult {
    var restartCmd, checkCmd []string
    if runtime.GOOS == "windows" {
        restartCmd = []string{"powershell", "-Command", fmt.Sprintf("Restart-Service -Name '%s' -Force", params.ServiceName)}
        checkCmd   = []string{"powershell", "-Command", fmt.Sprintf("(Get-Service -Name '%s').Status", params.ServiceName)}
    } else {
        restartCmd = []string{"systemctl", "restart", params.ServiceName}
        checkCmd   = []string{"systemctl", "is-active", "--quiet", params.ServiceName}
    }

    out, err := exec.CommandContext(ctx, restartCmd[0], restartCmd[1:]...).CombinedOutput()
    if err != nil {
        return RestartServiceResult{Running: false, Output: string(out), Error: err.Error()}
    }

    checkOut, checkErr := exec.CommandContext(ctx, checkCmd[0], checkCmd[1:]...).CombinedOutput()
    running := checkErr == nil
    return RestartServiceResult{Running: running, Output: string(checkOut)}
}
```

Health check after restart: the backend dispatches a second `restart_service` job with a `check_only: true` flag (or calls `health_check` for the specific service) and inspects `running` in the result before promoting the next batch.

---

### 1.2 `push_config_file`

**File:** `agent/commands/fleet/push_config_file.go`

```go
package fleet

import (
    "context"
    "fmt"
    "os"
    "path/filepath"
    "time"
)

type PushConfigFileParams struct {
    FilePath    string `json:"file_path"`
    FileContent string `json:"file_content"` // base64-encoded
    Backup      bool   `json:"backup"`        // default true
}

type PushConfigFileResult struct {
    BackupPath string `json:"backup_path,omitempty"`
    Error      string `json:"error,omitempty"`
}

func HandlePushConfigFile(ctx context.Context, params PushConfigFileParams) PushConfigFileResult {
    if params.Backup {
        backupPath := fmt.Sprintf("%s.bak.%d", params.FilePath, time.Now().Unix())
        existing, err := os.ReadFile(params.FilePath)
        if err == nil {
            if err := os.WriteFile(backupPath, existing, 0600); err != nil {
                return PushConfigFileResult{Error: fmt.Sprintf("backup failed: %v", err)}
            }
            // Return the backup path so the backend records it for rollback
            if err := writeContent(params.FilePath, params.FileContent); err != nil {
                return PushConfigFileResult{BackupPath: backupPath, Error: err.Error()}
            }
            return PushConfigFileResult{BackupPath: backupPath}
        }
    }
    if err := writeContent(params.FilePath, params.FileContent); err != nil {
        return PushConfigFileResult{Error: err.Error()}
    }
    return PushConfigFileResult{}
}

func writeContent(path, b64content string) error {
    if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
        return err
    }
    // Decode base64 and write atomically via temp file
    // ... (standard pattern: decode → write to path+".tmp" → os.Rename)
    return nil
}
```

---

### 1.3 `distribute_file`

**File:** `agent/commands/fleet/distribute_file.go`

```go
package fleet

import (
    "context"
    "encoding/base64"
    "fmt"
    "os"
    "os/exec"
    "path/filepath"
    "strconv"
)

type DistributeFileParams struct {
    FilePath      string `json:"file_path"`
    FileContent   string `json:"file_content"`   // base64-encoded
    Permissions   string `json:"permissions"`     // octal string, e.g. "0644"
    PostCommand   string `json:"post_command"`    // optional shell command after write
}

type DistributeFileResult struct {
    Error  string `json:"error,omitempty"`
    Output string `json:"output,omitempty"`
}

func HandleDistributeFile(ctx context.Context, params DistributeFileParams) DistributeFileResult {
    content, err := base64.StdEncoding.DecodeString(params.FileContent)
    if err != nil {
        return DistributeFileResult{Error: fmt.Sprintf("base64 decode: %v", err)}
    }
    if err := os.MkdirAll(filepath.Dir(params.FilePath), 0755); err != nil {
        return DistributeFileResult{Error: fmt.Sprintf("mkdir: %v", err)}
    }

    perm := os.FileMode(0644)
    if params.Permissions != "" {
        v, err := strconv.ParseUint(params.Permissions, 8, 32)
        if err == nil {
            perm = os.FileMode(v)
        }
    }
    if err := os.WriteFile(params.FilePath, content, perm); err != nil {
        return DistributeFileResult{Error: fmt.Sprintf("write: %v", err)}
    }

    if params.PostCommand != "" {
        out, err := exec.CommandContext(ctx, "sh", "-c", params.PostCommand).CombinedOutput()
        if err != nil {
            return DistributeFileResult{Error: fmt.Sprintf("post_command: %v", err), Output: string(out)}
        }
        return DistributeFileResult{Output: string(out)}
    }
    return DistributeFileResult{}
}
```

---

### 1.4 `health_check`

**File:** `agent/commands/fleet/health_check.go`

```go
package fleet

import (
    "context"
    "os/exec"
    "runtime"
    "syscall"
)

type HealthCheckParams struct {
    RequiredServices []string `json:"required_services,omitempty"`
}

type HealthCheckResult struct {
    DiskFreeOK      bool              `json:"disk_free_ok"`      // >20% free on root/C:
    LoadOK          bool              `json:"load_ok"`           // 1-min load <80% of CPU count (Linux) or CPU% <80 (Windows)
    Reachable       bool              `json:"reachable"`         // always true if we're responding
    NoPendingReboot bool              `json:"no_pending_reboot"` // Windows only; true on Linux
    Services        map[string]bool   `json:"services"`          // required_services → running?
    Pass            bool              `json:"pass"`              // all checks true
}

func HandleHealthCheck(ctx context.Context, params HealthCheckParams) HealthCheckResult {
    r := HealthCheckResult{Reachable: true}
    r.DiskFreeOK  = checkDiskFree()
    r.LoadOK      = checkLoad()
    r.NoPendingReboot = checkNoPendingReboot()
    r.Services    = checkServices(ctx, params.RequiredServices)

    r.Pass = r.DiskFreeOK && r.LoadOK && r.NoPendingReboot
    for _, ok := range r.Services {
        if !ok {
            r.Pass = false
            break
        }
    }
    return r
}

func checkNoPendingReboot() bool {
    if runtime.GOOS != "windows" {
        return true
    }
    // Check HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\PendingFileRenameOperations
    out, err := exec.Command("reg", "query",
        `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager`,
        "/v", "PendingFileRenameOperations").Output()
    return err != nil || len(out) == 0 // key absent = no pending reboot
}

func checkServices(ctx context.Context, services []string) map[string]bool {
    result := make(map[string]bool, len(services))
    for _, svc := range services {
        var err error
        if runtime.GOOS == "windows" {
            _, err = exec.CommandContext(ctx, "sc", "query", svc).Output()
        } else {
            err = exec.CommandContext(ctx, "systemctl", "is-active", "--quiet", svc).Run()
        }
        result[svc] = err == nil
    }
    return result
}

// checkDiskFree and checkLoad use syscall.Statfs (Linux) / GetDiskFreeSpaceEx (Windows).
// Implementations omitted for brevity — standard pattern using syscall.Statfs_t.
func checkDiskFree() bool  { _ = syscall.Statfs; return true }
func checkLoad() bool      { return true }
```

---

## Section 2: Backend Data Models

### 2.1 Extended `ChangeRequest` parameters

The existing `change_request` table's `parameters` JSONB column holds change-type-specific data. No schema migration is needed for rolling restart, canary push, distribute file, or fleet health check — new change types simply use new parameter shapes.

`step_metadata` JSONB column (add via migration if not present) stores per-host execution state:

```json
{
  "batches": [
    {
      "batch_index": 0,
      "asset_ids": [42, 43, 44],
      "status": "completed",
      "results": {
        "42": {"running": true},
        "43": {"running": true},
        "44": {"running": false, "error": "unit not found"}
      }
    },
    {
      "batch_index": 1,
      "asset_ids": [45, 46],
      "status": "pending"
    }
  ],
  "aborted": false,
  "failure_count": 1,
  "total_dispatched": 5
}
```

**Migration:** `backend/alembic/versions/xxxx_add_step_metadata_to_change_request.py`

```python
def upgrade():
    op.add_column("change_request",
        sa.Column("step_metadata", postgresql.JSONB, nullable=True))
```

---

### 2.2 `MaintenanceWindow` model

**New file:** `backend/app/models/maintenance_window.py`

```python
from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base

class MaintenanceWindow(Base):
    __tablename__ = "maintenance_window"

    id              = Column(Integer, primary_key=True)
    organization_id = Column(Integer, nullable=False, index=True)
    name            = Column(String(255), nullable=False)
    # cron expression, e.g. "0 2 * * 6" = Saturdays at 02:00
    cron_schedule   = Column(String(100), nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=60)
    # null = applies to all assets; otherwise list of tag names
    applies_to_tags = Column(JSONB, nullable=True)
    enabled         = Column(Boolean, nullable=False, default=True)
```

**Migration:** `backend/alembic/versions/xxxx_add_maintenance_window.py`

```python
def upgrade():
    op.create_table(
        "maintenance_window",
        sa.Column("id",               sa.Integer, primary_key=True),
        sa.Column("organization_id",  sa.Integer, nullable=False),
        sa.Column("name",             sa.String(255), nullable=False),
        sa.Column("cron_schedule",    sa.String(100), nullable=False),
        sa.Column("duration_minutes", sa.Integer, nullable=False, server_default="60"),
        sa.Column("applies_to_tags",  postgresql.JSONB, nullable=True),
        sa.Column("enabled",          sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_index("ix_maintenance_window_org", "maintenance_window", ["organization_id"])
```

---

### 2.3 New `ChangeRequest.status` values

Add to the existing status enum or string field:

| Value | Meaning |
|-------|---------|
| `queued_for_maintenance` | Approved but no open maintenance window; waiting |
| `preflight_running` | Fleet health check in progress |
| `preflight_failed` | Health check found hosts that did not pass |
| `batch_running` | At least one batch dispatched, more pending |
| `batch_aborted` | Failure threshold exceeded; operation stopped |

---

## Section 3: Backend Change Executor

### 3.1 Rolling Restart executor

**File:** `backend/app/executors/rolling_restart.py`

```python
import math, asyncio
from app.models import ChangeRequest, Asset
from app.jobs import dispatch_job, wait_for_job

async def execute_rolling_restart(cr: ChangeRequest, db):
    p = cr.parameters  # validated pydantic model
    assets = resolve_asset_group(p["asset_group"], db)
    batch_size = max(1, math.ceil(len(assets) * (p.get("batch_size_pct", 10) / 100)))
    abort_threshold = p.get("abort_threshold_pct", 25) / 100

    batches = [assets[i:i+batch_size] for i in range(0, len(assets), batch_size)]
    meta = {"batches": [], "failure_count": 0, "total_dispatched": 0, "aborted": False}

    for idx, batch in enumerate(batches):
        batch_record = {"batch_index": idx, "asset_ids": [a.id for a in batch],
                        "status": "running", "results": {}}
        meta["batches"].append(batch_record)
        _save_meta(cr, meta, db)

        # Dispatch all hosts in the batch concurrently
        jobs = [dispatch_job(a.id, "restart_service",
                             {"service_name": p["service_name"]}) for a in batch]
        results = await asyncio.gather(*[wait_for_job(j) for j in jobs])

        for asset, result in zip(batch, results):
            ok = result.get("running", False)
            batch_record["results"][asset.id] = result
            if not ok:
                meta["failure_count"] += 1

        meta["total_dispatched"] += len(batch)
        failures_so_far = meta["failure_count"] / meta["total_dispatched"]
        if failures_so_far >= abort_threshold:
            batch_record["status"] = "aborted"
            meta["aborted"] = True
            cr.status = "batch_aborted"
            _save_meta(cr, meta, db)
            return

        batch_record["status"] = "completed"
        _save_meta(cr, meta, db)

    cr.status = "completed"
    db.commit()
```

**Parameter schema** (`backend/app/schemas/change_request.py`):

```python
class RollingRestartParams(BaseModel):
    service_name:         str
    asset_group:          AssetGroup          # tag name or list of asset IDs
    batch_size_pct:       int = 10            # percent of fleet per wave
    abort_threshold_pct:  int = 25            # stop if this % of dispatched hosts fail
```

---

### 3.2 Canary Config Push executor

**File:** `backend/app/executors/canary_config_push.py`

```python
async def execute_canary_config_push(cr: ChangeRequest, db):
    p = cr.parameters
    canary_id = p["canary_asset_id"]

    # Step 1: push to canary
    job = await dispatch_job(canary_id, "push_config_file", {
        "file_path":    p["file_path"],
        "file_content": p["file_content"],
        "backup":       True,
    })
    result = await wait_for_job(job)
    if result.get("error"):
        cr.status = "failed"
        db.commit()
        return

    canary_backup_path = result.get("backup_path")

    # Step 2: run verification command via remote_command
    verify_job = await dispatch_job(canary_id, "remote_command", {
        "command": p["verification_command"]
    })
    verify_result = await wait_for_job(verify_job)
    if verify_result.get("exit_code", 1) != 0:
        # Rollback canary
        if canary_backup_path:
            await dispatch_and_wait(canary_id, "push_config_file", {
                "file_path":    p["file_path"],
                "file_content": read_backup_via_agent(canary_id, canary_backup_path),
                "backup":       False,
            })
        cr.status = "failed"
        db.commit()
        return

    # Step 3: push to remaining assets
    remaining = [a for a in resolve_asset_group(p["asset_group"], db)
                 if a.id != canary_id]
    for asset in remaining:
        await dispatch_and_wait(asset.id, "push_config_file", {
            "file_path":    p["file_path"],
            "file_content": p["file_content"],
            "backup":       True,
        })

    cr.status = "completed"
    db.commit()
```

**Parameter schema:**

```python
class CanaryConfigPushParams(BaseModel):
    file_path:            str
    file_content:         str           # base64-encoded
    canary_asset_id:      int
    verification_command: str           # must exit 0 on canary
    asset_group:          AssetGroup    # full target set (canary included)
```

---

### 3.3 Bulk File Distribution executor

**File:** `backend/app/executors/distribute_file.py`

```python
async def execute_distribute_file(cr: ChangeRequest, db):
    p = cr.parameters
    assets = resolve_asset_group(p["asset_group"], db)

    jobs = [dispatch_job(a.id, "distribute_file", {
        "file_path":    p["file_path"],
        "file_content": p["file_content"],
        "permissions":  p.get("permissions", "0644"),
        "post_command": p.get("post_command"),
    }) for a in assets]
    results = await asyncio.gather(*[wait_for_job(j) for j in jobs])

    failures = [r for r in results if r.get("error")]
    cr.status = "completed" if not failures else "completed_with_errors"
    cr.step_metadata = {"results": dict(zip([a.id for a in assets], results))}
    db.commit()
```

**Parameter schema:**

```python
class DistributeFileParams(BaseModel):
    file_path:    str
    file_content: str           # base64-encoded
    permissions:  str = "0644" # octal string
    post_command: Optional[str] = None
    asset_group:  AssetGroup
```

---

### 3.4 Fleet Health Check executor

**File:** `backend/app/executors/fleet_health_check.py`

```python
async def execute_fleet_health_check(cr: ChangeRequest, db):
    p = cr.parameters
    assets = resolve_asset_group(p["asset_group"], db)

    jobs = [dispatch_job(a.id, "health_check", {
        "required_services": p.get("required_services", [])
    }) for a in assets]
    results = await asyncio.gather(*[wait_for_job(j) for j in jobs])

    per_host = {a.id: r for a, r in zip(assets, results)}
    passed   = [aid for aid, r in per_host.items() if r.get("pass")]
    failed   = [aid for aid, r in per_host.items() if not r.get("pass")]

    cr.step_metadata = {"per_host": per_host, "passed": passed, "failed": failed}
    cr.status = "completed" if not failed else "preflight_failed"
    db.commit()
```

---

### 3.5 Maintenance Window scheduler

**File:** `backend/app/scheduler/maintenance_window.py`

```python
from croniter import croniter
from datetime import datetime, timezone, timedelta
from app.models import MaintenanceWindow, ChangeRequest

async def promote_queued_changes(db):
    """Run every 60 seconds. Promote queued_for_maintenance → executing if a window is now open."""
    now = datetime.now(timezone.utc)
    windows = db.query(MaintenanceWindow).filter_by(enabled=True).all()

    open_window_tags: set[str | None] = set()
    for w in windows:
        cron = croniter(w.cron_schedule, now - timedelta(minutes=w.duration_minutes))
        last_open = cron.get_prev(datetime)
        window_close = last_open + timedelta(minutes=w.duration_minutes)
        if last_open <= now <= window_close:
            # This window is currently open
            if w.applies_to_tags:
                open_window_tags.update(w.applies_to_tags)
            else:
                open_window_tags.add(None)  # None = applies to all

    queued = db.query(ChangeRequest).filter_by(status="queued_for_maintenance").all()
    for cr in queued:
        if _change_request_covered(cr, open_window_tags, db):
            cr.status = "approved"   # re-enters the normal execution pipeline
            db.commit()
            await execute_change_request(cr, db)

def _change_request_covered(cr, open_tags, db) -> bool:
    if None in open_tags:
        return True
    # Check if any target asset has a tag covered by an open window
    asset_tags = get_asset_tags_for_change_request(cr, db)
    return bool(asset_tags & open_tags)
```

Scheduler registered in `backend/app/main.py` via `asyncio` background task or APScheduler:

```python
@app.on_event("startup")
async def start_scheduler():
    asyncio.create_task(run_every(60, promote_queued_changes, get_db()))
```

Maintenance window check at approval time (`backend/app/routers/change_requests.py`):

```python
@router.post("/{cr_id}/approve")
async def approve_change_request(cr_id: int, db: Session = Depends(get_db)):
    cr = get_or_404(db, ChangeRequest, cr_id)
    cr.status = "approved"
    if not maintenance_window_open_for(cr, db):
        cr.status = "queued_for_maintenance"
    db.commit()
    if cr.status == "approved":
        background_tasks.add_task(execute_change_request, cr, db)
    return cr
```

---

## Section 4: API Endpoints

### 4.1 Fleet change types

All fleet operations are created via the existing `POST /api/change-requests` endpoint. The `change_type` field determines which executor runs. New valid values:

| `change_type` | Executor |
|---------------|----------|
| `rolling_restart` | `execute_rolling_restart` |
| `canary_config_push` | `execute_canary_config_push` |
| `distribute_file` | `execute_distribute_file` |
| `fleet_health_check` | `execute_fleet_health_check` |

### 4.2 Maintenance Window API

**New router:** `backend/app/routers/maintenance_windows.py`  
Registered at prefix `/api/maintenance-windows`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/maintenance-windows` | List all windows for the org |
| `POST` | `/api/maintenance-windows` | Create a window |
| `GET` | `/api/maintenance-windows/{id}` | Get a window |
| `PUT` | `/api/maintenance-windows/{id}` | Update a window |
| `DELETE` | `/api/maintenance-windows/{id}` | Delete a window |
| `GET` | `/api/maintenance-windows/{id}/status` | Is the window currently open? |

**Request/response schema:**

```python
class MaintenanceWindowSchema(BaseModel):
    name:             str
    cron_schedule:    str           # e.g. "0 2 * * 6"
    duration_minutes: int = 60
    applies_to_tags:  Optional[list[str]] = None  # null = all assets
    enabled:          bool = True
```

### 4.3 Change request step metadata endpoint

```
GET /api/change-requests/{id}/progress
```

Returns `step_metadata` JSONB for live polling of batch status from the frontend. No new DB query — just returns `cr.step_metadata`.

---

## Section 5: Frontend

### 5.1 Maintenance Windows settings page

**New file:** `frontend/src/pages/MaintenanceWindows.tsx`

- Table listing all windows: name, schedule (human-readable via `cronstrue`), duration, applies to, enabled toggle.
- "New Window" button → modal with form fields: name, cron expression (with preview), duration, tag selector, enabled.
- Inline enable/disable toggle via `PUT /api/maintenance-windows/{id}`.
- Route: `/settings/maintenance-windows`.

### 5.2 Change request detail — batch progress

**Update:** `frontend/src/pages/ChangeRequestDetail.tsx`

For fleet change types, show a batch progress table polled from `GET /api/change-requests/{id}/progress`:

- Columns: Batch #, Hosts, Status, Failures.
- Per-host expandable rows showing individual command results.
- Auto-refresh every 5 seconds while status is `batch_running`.

### 5.3 Fleet Health Check result view

On the change request detail page for `fleet_health_check`, render `step_metadata.per_host` as a table:

| Host | Disk OK | Load OK | No Pending Reboot | Services | Pass |
|------|---------|---------|-------------------|----------|------|

Red row = `pass: false`.

### 5.4 New change request form — fleet types

**Update:** `frontend/src/components/NewChangeRequestModal.tsx`

Add `change_type` options for the five fleet types with dynamic parameter fields rendered per type:

- `rolling_restart`: service name, asset group selector, batch size %, abort threshold %.
- `canary_config_push`: file path, file content upload, canary asset picker, verification command, asset group.
- `distribute_file`: file path, file upload (base64-encoded client-side), permissions, post command, asset group.
- `fleet_health_check`: asset group, optional required services list.

---

## Section 6: `AssetGroup` resolver

Used by all fleet executors to resolve the target asset list from a `change_request.parameters.asset_group` value:

```python
# backend/app/executors/utils.py

from typing import Union
from app.models import Asset, AssetTag

AssetGroup = Union[
    dict[str, list[int]],   # {"asset_ids": [1, 2, 3]}
    dict[str, str],          # {"tag": "prod-web"}
]

def resolve_asset_group(group: dict, db) -> list[Asset]:
    if "asset_ids" in group:
        return db.query(Asset).filter(Asset.id.in_(group["asset_ids"])).all()
    if "tag" in group:
        return (db.query(Asset)
                  .join(AssetTag)
                  .filter(AssetTag.name == group["tag"])
                  .all())
    raise ValueError(f"Unknown asset_group format: {group}")
```

---

## Files Changed

| File | Change |
|------|--------|
| `agent/commands/fleet/restart_service.go` | New — `restart_service` command handler |
| `agent/commands/fleet/push_config_file.go` | New — `push_config_file` command handler with backup |
| `agent/commands/fleet/distribute_file.go` | New — `distribute_file` command handler with permissions and post-command |
| `agent/commands/fleet/health_check.go` | New — `health_check` command handler (disk, load, reboot, services) |
| `agent/commands/fleet/register.go` | New — registers all fleet command handlers in the dispatch table |
| `backend/app/models/maintenance_window.py` | New — `MaintenanceWindow` SQLAlchemy model |
| `backend/app/routers/maintenance_windows.py` | New — CRUD router at `/api/maintenance-windows` |
| `backend/app/schemas/change_request.py` | Add `RollingRestartParams`, `CanaryConfigPushParams`, `DistributeFileParams`, `AssetGroup` schemas |
| `backend/app/executors/rolling_restart.py` | New — batch orchestration with abort threshold |
| `backend/app/executors/canary_config_push.py` | New — canary → verify → rollout executor |
| `backend/app/executors/distribute_file.py` | New — parallel file push executor |
| `backend/app/executors/fleet_health_check.py` | New — parallel health check collector |
| `backend/app/executors/utils.py` | Add `resolve_asset_group` helper |
| `backend/app/scheduler/maintenance_window.py` | New — 60-second scheduler promoting `queued_for_maintenance` requests |
| `backend/app/routers/change_requests.py` | Add maintenance window check on approve; add `/progress` endpoint |
| `backend/app/main.py` | Register maintenance window router; start scheduler background task |
| `backend/alembic/versions/xxxx_add_step_metadata.py` | Add `step_metadata` JSONB column to `change_request` |
| `backend/alembic/versions/xxxx_add_maintenance_window.py` | Create `maintenance_window` table |
| `frontend/src/pages/MaintenanceWindows.tsx` | New — settings page for maintenance window CRUD |
| `frontend/src/pages/ChangeRequestDetail.tsx` | Add batch progress table and fleet health check result view |
| `frontend/src/components/NewChangeRequestModal.tsx` | Add fleet change types with dynamic parameter fields |
| `frontend/src/App.tsx` | Add route `/settings/maintenance-windows` |
