# Backup & Recovery and Scheduled Operations — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** On-demand backup creation, backup verification via restore-test, file-level restore, DR runbook execution with RTO measurement, maintenance window scheduling, scheduled access reviews, scheduled compliance scans, and scheduled reboots — all integrated into the existing FastAPI + PostgreSQL + Go agent + APScheduler stack.

---

## Background

Nexplane can already execute one-off change requests against managed assets and run APScheduler jobs for periodic work. What is missing is a coherent layer that ties backup/recovery operations to change requests and extends the scheduler to cover recurring compliance, access-review, and reboot work. This spec adds that layer without introducing new infrastructure: AWS snapshots go through the existing AWS connector, agent-side backup commands go through the existing polling loop, and all scheduled jobs use the existing `backend/app/scheduler.py` APScheduler instance.

---

## Design Decisions

- **restic as the default agent backup tool:** deduplicating, encrypted at rest, single static binary, available for Linux and Windows. Commands are `restic backup`, `restic restore`, `restic snapshots`, `restic check`.
- **AWS backup actions extend the existing AWS connector** (`backend/app/connectors/aws/`). No new connector; new action handlers are added to the existing `AWSConnector` class.
- **All backup/restore operations are change requests.** This gives audit trail, approval gating, rollback metadata, and status tracking for free.
- **Scheduled operations use the existing APScheduler instance** in `backend/app/scheduler.py`. New jobs are registered at startup alongside the existing poll jobs.
- **`AccessReviewSchedule` and schedule configuration** are stored in the database and read at startup so changes survive restarts without redeploying.
- **Scheduled reboot** stores `reboot_at` in change request metadata. A scheduler job (every 60 s) checks for approved reboot change requests whose `reboot_at` has passed and dispatches them to the agent.
- **Backup verification** always terminates the temporary RDS instance when done (`terminate_after_verify: true` default). The rollback step for a failed verify is the same cleanup — terminate and delete temp instance.

---

## Part A: Backup & Recovery

### Section A-1: On-Demand Backup

#### New Change Type: `create_backup`

| Field | Values |
|-------|--------|
| `change_type` | `"create_backup"` |
| `backup_type` | `"ebs_snapshot"` \| `"rds_snapshot"` \| `"agent_backup"` |
| `target_resource_id` | EBS volume ID, RDS instance identifier, or agent asset ID |
| `backup_name` | Human-readable label applied as a tag / restic tag |
| `retention_days` | Integer; lifecycle rule applied to snapshot / restic forget policy |

**AWS EBS/RDS path (`backup_type: "ebs_snapshot"` or `"rds_snapshot"`):**

New action handler in `backend/app/connectors/aws/actions.py`:

```python
async def create_backup(self, params: dict) -> dict:
    backup_type = params["backup_type"]
    resource_id  = params["target_resource_id"]
    name         = params["backup_name"]
    retention    = int(params.get("retention_days", 30))

    if backup_type == "ebs_snapshot":
        resp = self.ec2.create_snapshot(
            VolumeId=resource_id,
            Description=name,
            TagSpecifications=[{
                "ResourceType": "snapshot",
                "Tags": [
                    {"Key": "Name",          "Value": name},
                    {"Key": "RetentionDays", "Value": str(retention)},
                    {"Key": "ManagedBy",     "Value": "nexplane"},
                ],
            }],
        )
        return {"snapshot_id": resp["SnapshotId"], "state": resp["State"]}

    if backup_type == "rds_snapshot":
        snapshot_id = f"nexplane-{resource_id}-{int(time.time())}"
        resp = self.rds.create_db_snapshot(
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceIdentifier=resource_id,
            Tags=[
                {"Key": "Name",          "Value": name},
                {"Key": "RetentionDays", "Value": str(retention)},
                {"Key": "ManagedBy",     "Value": "nexplane"},
            ],
        )
        return {"snapshot_id": snapshot_id, "status": resp["DBSnapshot"]["Status"]}

    raise ValueError(f"AWS connector does not handle backup_type={backup_type}")
```

**Agent path (`backup_type: "agent_backup"`):**

New file: `agent/commands/backup/backup_linux.go`

```go
package backup

import (
    "context"
    "fmt"
    "os/exec"
    "strings"
    "time"
)

// BackupParams mirrors the create_backup change request parameters.
type BackupParams struct {
    BackupName      string   `json:"backup_name"`
    RetentionDays   int      `json:"retention_days"`
    Paths           []string `json:"paths"`           // directories/files to back up
    ResticRepo      string   `json:"restic_repo"`     // restic repository URL or path
    ResticPassword  string   `json:"restic_password"` // injected from agent secrets
}

// RunBackup invokes restic to create a snapshot and prune old snapshots.
// Returns the restic snapshot ID on success.
func RunBackup(ctx context.Context, p BackupParams) (string, error) {
    env := append(resticEnv(p.ResticRepo, p.ResticPassword))

    // restic backup <paths...> --tag <name>
    args := append([]string{"backup", "--json", "--tag", p.BackupName}, p.Paths...)
    out, err := runRestic(ctx, env, args...)
    if err != nil {
        return "", fmt.Errorf("restic backup failed: %w\n%s", err, out)
    }

    snapshotID := parseSnapshotID(out)

    // restic forget --keep-within <N>d --prune --tag <name>
    keepWithin := fmt.Sprintf("%dd", p.RetentionDays)
    _, err = runRestic(ctx, env,
        "forget", "--prune", "--tag", p.BackupName, "--keep-within", keepWithin)
    if err != nil {
        // Non-fatal: snapshot exists, pruning failed.
        return snapshotID, fmt.Errorf("backup succeeded (id=%s) but prune failed: %w", snapshotID, err)
    }

    return snapshotID, nil
}

func runRestic(ctx context.Context, env []string, args ...string) ([]byte, error) {
    cmd := exec.CommandContext(ctx, "restic", args...)
    cmd.Env = env
    return cmd.CombinedOutput()
}

func resticEnv(repo, password string) []string {
    return []string{
        "RESTIC_REPOSITORY=" + repo,
        "RESTIC_PASSWORD=" + password,
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
}

func parseSnapshotID(out []byte) string {
    // restic --json backup output contains `"snapshot_id":"<id>"` on success
    s := string(out)
    key := `"snapshot_id":"`
    idx := strings.Index(s, key)
    if idx == -1 {
        return "unknown"
    }
    rest := s[idx+len(key):]
    end := strings.Index(rest, `"`)
    if end == -1 {
        return "unknown"
    }
    return rest[:end]
}

// Timestamp helper used by callers for RTO measurement.
func NowISO() string {
    return time.Now().UTC().Format(time.RFC3339)
}
```

The agent command dispatcher (`agent/commands/dispatcher.go`) is extended to call `backup.RunBackup` when `command_type == "create_backup"` and `backup_type == "agent_backup"`.

---

### Section A-2: Backup Verification (Restore Test)

#### New Change Type: `verify_backup`

| Field | Type | Description |
|-------|------|-------------|
| `backup_id` | string | RDS snapshot ID to verify |
| `verification_query` | string | SQL query whose success confirms database health (e.g. `"SELECT COUNT(*) FROM users"`) |
| `terminate_after_verify` | bool | Default `true`. Always terminate and delete temp instance after verify. |

**Backend handler** (new method on `AWSConnector`):

```python
async def verify_backup(self, params: dict) -> dict:
    snapshot_id = params["backup_id"]
    query       = params["verification_query"]
    terminate   = params.get("terminate_after_verify", True)

    restore_start = time.time()

    # 1. Restore snapshot to a temp RDS instance
    temp_id = f"nexplane-verify-{int(restore_start)}"
    self.rds.restore_db_instance_from_db_snapshot(
        DBInstanceIdentifier=temp_id,
        DBSnapshotIdentifier=snapshot_id,
        DBInstanceClass="db.t3.micro",
        MultiAZ=False,
        PubliclyAccessible=False,
        Tags=[{"Key": "ManagedBy", "Value": "nexplane-verify"}],
    )

    # 2. Wait for available (poll with backoff, max 15 min)
    waiter = self.rds.get_waiter("db_instance_available")
    waiter.wait(DBInstanceIdentifier=temp_id,
                WaiterConfig={"Delay": 30, "MaxAttempts": 30})

    restore_elapsed = time.time() - restore_start

    # 3. Connect and run health query
    endpoint = self._get_rds_endpoint(temp_id)
    health_ok, health_detail = self._run_sql_check(endpoint, query)

    # 4. Terminate temp instance (always)
    if terminate:
        self.rds.delete_db_instance(
            DBInstanceIdentifier=temp_id,
            SkipFinalSnapshot=True,
        )

    restore_time = f"{int(restore_elapsed // 60)}m{int(restore_elapsed % 60)}s"
    status = "verified" if health_ok else "health_check_failed"
    return {
        "status": status,
        "restore_time": restore_time,
        "health_detail": health_detail,
        "temp_instance_terminated": terminate,
    }
```

**Result stored on the change request:**
```
backup verified successfully, restore took 4m32s, health check passed
```

**Rollback behavior:** If any step after restore creation fails, the rollback step (stored in change request `rollback_steps`) calls `delete_db_instance(temp_id, SkipFinalSnapshot=True)`. This is safe to call even if the instance is in a failed state.

---

### Section A-3: File-Level Restore

#### New Change Type: `restore_files`

| Field | Type | Description |
|-------|------|-------------|
| `backup_snapshot_id` | string | restic snapshot ID |
| `restore_paths` | list[string] | Paths within the snapshot to restore (e.g. `["/etc/nginx/nginx.conf"]`) |
| `destination_path` | string | Local path to restore into |
| `backup_tool` | string | Default `"restic"`. Extensible for duplicati. |

**New file: `agent/commands/backup/restore_linux.go`**

```go
package backup

import (
    "context"
    "crypto/sha256"
    "fmt"
    "io"
    "os"
    "path/filepath"
    "strings"
)

// RestoreParams mirrors the restore_files change request parameters.
type RestoreParams struct {
    BackupSnapshotID string   `json:"backup_snapshot_id"`
    RestorePaths     []string `json:"restore_paths"`
    DestinationPath  string   `json:"destination_path"`
    ResticRepo       string   `json:"restic_repo"`
    ResticPassword   string   `json:"restic_password"`
}

// RestoreFiles restores specific paths from a restic snapshot.
// Returns a map of restored path → SHA256 for verification.
func RestoreFiles(ctx context.Context, p RestoreParams) (map[string]string, error) {
    env := resticEnv(p.ResticRepo, p.ResticPassword)

    for _, path := range p.RestorePaths {
        // restic restore <snapshot_id> --target <dest> --include <path>
        _, err := runRestic(ctx, env,
            "restore", p.BackupSnapshotID,
            "--target", p.DestinationPath,
            "--include", path,
        )
        if err != nil {
            return nil, fmt.Errorf("restore of %s failed: %w", path, err)
        }
    }

    // Checksum all restored files for verification
    checksums := map[string]string{}
    for _, rp := range p.RestorePaths {
        localPath := filepath.Join(p.DestinationPath, rp)
        sum, err := sha256File(localPath)
        if err != nil {
            checksums[rp] = "error: " + err.Error()
        } else {
            checksums[rp] = sum
        }
    }
    return checksums, nil
}

func sha256File(path string) (string, error) {
    f, err := os.Open(path)
    if err != nil {
        return "", err
    }
    defer f.Close()
    h := sha256.New()
    if _, err := io.Copy(h, f); err != nil {
        return "", err
    }
    return fmt.Sprintf("%x", h.Sum(nil)), nil
}

// VerifyChecksums compares restored-file checksums against expected values
// from backup metadata. Returns a list of mismatches (empty = all OK).
func VerifyChecksums(restored, expected map[string]string) []string {
    var mismatches []string
    for path, expHash := range expected {
        if got, ok := restored[path]; !ok {
            mismatches = append(mismatches, fmt.Sprintf("%s: not restored", path))
        } else if !strings.EqualFold(got, expHash) {
            mismatches = append(mismatches, fmt.Sprintf("%s: expected %s got %s", path, expHash, got))
        }
    }
    return mismatches
}
```

**Change request result example:**
```json
{
  "restored_files": 3,
  "checksums": {
    "/etc/nginx/nginx.conf": "a3f1...",
    "/etc/nginx/conf.d/app.conf": "b9c2...",
    "/etc/ssl/certs/app.pem": "d4e5..."
  },
  "mismatches": []
}
```

---

### Section A-4: DR Runbook Execution

DR runbooks use the existing Composable Runbooks system. This section adds a DR-specific change type and RTO measurement.

#### New Change Type: `dr_failover`

| Field | Type | Description |
|-------|------|-------------|
| `runbook_id` | string | ID of the pre-defined DR runbook |
| `dr_region` | string | AWS region to fail over to (e.g. `"us-west-2"`) |
| `dns_provider` | string | `"route53"` \| `"cloudflare"` |
| `dns_record_id` | string | Route53 record set name or Cloudflare record ID |
| `dr_endpoint` | string | CNAME/IP of the DR environment |
| `target_rto_minutes` | int | SLA target; used in the final report |

**DNS failover step** (new action in AWS connector and Cloudflare connector):

```python
# route53 failover
async def dr_dns_failover_route53(self, params: dict) -> dict:
    record_name  = params["dns_record_id"]
    dr_endpoint  = params["dr_endpoint"]
    hosted_zone  = params["hosted_zone_id"]

    self.r53.change_resource_record_sets(
        HostedZoneId=hosted_zone,
        ChangeBatch={
            "Changes": [{
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name": record_name,
                    "Type": "CNAME",
                    "SetIdentifier": "dr-primary",
                    "Weight": 100,
                    "TTL": 60,
                    "ResourceRecords": [{"Value": dr_endpoint}],
                },
            }]
        },
    )
    return {"dns_updated": True, "new_endpoint": dr_endpoint}
```

**RTO measurement** — the runbook executor (backend) records `step_started_at` and `step_completed_at` for each runbook step. The final change request result includes:

```json
{
  "runbook_id": "dr-runbook-us-east-1",
  "steps_completed": 7,
  "rto_actual_minutes": 12,
  "rto_target_minutes": 15,
  "rto_met": true,
  "step_timings": [
    {"step": "promote_read_replica", "duration_s": 180},
    {"step": "update_route53",       "duration_s": 45},
    {"step": "verify_app_health",    "duration_s": 30}
  ]
}
```

---

## Part B: Scheduled Operations

### Section B-1: Scheduled Change Execution (Maintenance Windows)

Maintenance window support integrates with the existing APScheduler instance and change request approval flow.

**New DB model: `MaintenanceWindow`**

```python
# backend/app/models/maintenance_window.py
class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id            = Column(UUID, primary_key=True, default=uuid4)
    name          = Column(String, nullable=False)
    cron_expr     = Column(String, nullable=False)   # e.g. "0 2 * * 6"  (02:00 Saturday)
    duration_mins = Column(Integer, default=120)
    asset_tags    = Column(JSONB, default=list)       # assets in scope
    enabled       = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=func.now())
```

**APScheduler job registration** (`backend/app/scheduler.py`):

```python
def register_maintenance_windows(scheduler: AsyncIOScheduler, db_session_factory):
    """Load all enabled maintenance windows from DB and schedule them."""
    async def _load_and_register():
        async with db_session_factory() as db:
            windows = await db.execute(
                select(MaintenanceWindow).where(MaintenanceWindow.enabled == True)
            )
            for window in windows.scalars():
                scheduler.add_job(
                    execute_maintenance_window,
                    CronTrigger.from_crontab(window.cron_expr),
                    args=[window.id],
                    id=f"mw-{window.id}",
                    replace_existing=True,
                )

    scheduler.add_job(_load_and_register, "interval", minutes=5, id="mw-loader")
```

**`execute_maintenance_window`** dispatches all approved change requests whose `scheduled_window_id` matches this window and whose `scheduled_at` falls within the window's duration.

---

### Section B-2: Scheduled Access Reviews

**New DB model: `AccessReviewSchedule`**

```python
# backend/app/models/access_review.py
class AccessReviewSchedule(Base):
    __tablename__ = "access_review_schedules"

    id                      = Column(UUID, primary_key=True, default=uuid4)
    frequency_days          = Column(Integer, nullable=False)   # e.g. 90
    scope                   = Column(String, default="all_users")
                             # "all_users" | "by_tag:<tag_name>"
    reviewer_assignment_rule = Column(String, default="direct_manager")
                              # "direct_manager" | "security_team" | "asset_owner"
    last_review_created_at  = Column(DateTime, nullable=True)
    enabled                 = Column(Boolean, default=True)
    created_at              = Column(DateTime, default=func.now())
```

**APScheduler job** — runs daily at 06:00 UTC and creates `AccessReview` records when due:

```python
# backend/app/scheduler.py  (added job)

@scheduler.scheduled_job("cron", hour=6, minute=0, id="access-review-check")
async def check_access_review_schedules():
    async with get_db_session() as db:
        schedules = await db.execute(
            select(AccessReviewSchedule).where(AccessReviewSchedule.enabled == True)
        )
        for sched in schedules.scalars():
            due_at = (sched.last_review_created_at or datetime.min) + timedelta(days=sched.frequency_days)
            if datetime.utcnow() >= due_at:
                review = AccessReview(
                    schedule_id=sched.id,
                    scope=sched.scope,
                    reviewer_rule=sched.reviewer_assignment_rule,
                    status="pending",
                )
                db.add(review)
                sched.last_review_created_at = datetime.utcnow()
                await notify_reviewers(review)
        await db.commit()
```

**`notify_reviewers`** resolves reviewer emails based on `reviewer_assignment_rule` and sends a notification (using the existing notification layer). The `AccessReview` model is defined in the Identity Lifecycle spec.

---

### Section B-3: Scheduled Compliance Scans

**APScheduler job** — runs CIS audit on all managed hosts weekly (Sunday 01:00 UTC):

```python
# backend/app/scheduler.py  (added job)

@scheduler.scheduled_job("cron", day_of_week="sun", hour=1, minute=0, id="compliance-scan-weekly")
async def run_weekly_compliance_scans():
    async with get_db_session() as db:
        assets = await db.execute(
            select(Asset).where(Asset.managed == True, Asset.platform == "linux")
        )
        for asset in assets.scalars():
            await dispatch_agent_command(asset.id, {
                "command_type": "cis_audit",
                "store_results_in": "asset_metadata",
            })
```

**Score regression detection** — after each audit result is stored, a post-processing hook compares the new CIS score against the previous score in `asset_metadata`:

```python
async def on_cis_audit_complete(asset_id: UUID, new_score: float, db):
    prev = await get_previous_cis_score(asset_id, db)
    if prev is not None and prev > 0:
        drop_pct = (prev - new_score) / prev * 100
        if drop_pct >= 10:
            await create_alert(
                asset_id=asset_id,
                severity="high",
                title=f"CIS score dropped {drop_pct:.1f}% ({prev:.1f} → {new_score:.1f})",
            )
            if settings.AUTO_REMEDIATE_COMPLIANCE:
                await create_change_request(
                    change_type="remediate_cis_findings",
                    target_asset_id=asset_id,
                    params={"previous_score": prev, "new_score": new_score},
                )
```

**`asset_metadata` schema additions:**

```json
{
  "cis_score_history": [
    {"scanned_at": "2026-05-03T01:00:00Z", "score": 82.4, "findings": 14},
    {"scanned_at": "2026-04-27T01:00:00Z", "score": 91.0, "findings": 8}
  ]
}
```

---

### Section B-4: Scheduled Reboots

#### New Change Type: `scheduled_reboot`

| Field | Type | Description |
|-------|------|-------------|
| `target_asset_ids` | list[string] | Asset IDs to reboot |
| `reboot_at` | ISO-8601 datetime | When to execute the reboot |
| `verify_services` | list[string] | Service names to check after reboot (e.g. `["nginx", "postgresql"]`) |

**Change request metadata** stores `reboot_at` so the scheduler can pick it up:

```python
# Stored in change_request.metadata (JSONB)
{
  "reboot_at": "2026-05-04T02:00:00Z",
  "verify_services": ["nginx", "postgresql"],
  "graceful_delay_seconds": 60
}
```

**APScheduler job** — checks every 60 s for due reboot change requests:

```python
# backend/app/scheduler.py  (added job)

@scheduler.scheduled_job("interval", seconds=60, id="scheduled-reboot-dispatcher")
async def dispatch_due_reboots():
    async with get_db_session() as db:
        now = datetime.utcnow()
        due = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.change_type == "scheduled_reboot",
                ChangeRequest.status == "approved",
                ChangeRequest.metadata["reboot_at"].astext.cast(DateTime) <= now,
            )
        )
        for cr in due.scalars():
            cr.status = "executing"
            await db.commit()
            await dispatch_agent_command(cr.target_asset_id, {
                "command_type": "reboot",
                "change_request_id": str(cr.id),
                "verify_services": cr.metadata.get("verify_services", []),
                "graceful_delay_seconds": cr.metadata.get("graceful_delay_seconds", 60),
            })
```

**Agent reboot command** (new file: `agent/commands/reboot/reboot.go`):

```go
package reboot

import (
    "context"
    "fmt"
    "os/exec"
    "runtime"
    "time"
)

type RebootParams struct {
    GracefulDelaySeconds int      `json:"graceful_delay_seconds"`
    VerifyServices       []string `json:"verify_services"`
}

// Reboot schedules a graceful reboot and returns immediately.
// Service verification is performed by the agent on the next startup.
func Reboot(ctx context.Context, p RebootParams) error {
    delay := p.GracefulDelaySeconds
    if delay < 60 {
        delay = 60 // minimum 1 minute grace period
    }

    switch runtime.GOOS {
    case "linux":
        delayMin := fmt.Sprintf("+%d", (delay+59)/60) // round up to whole minutes
        return exec.CommandContext(ctx, "shutdown", "-r", delayMin,
            "Nexplane scheduled reboot").Run()
    case "windows":
        return exec.CommandContext(ctx,
            "powershell", "-Command",
            fmt.Sprintf("Restart-Computer -Delay %d -Force", delay)).Run()
    default:
        return fmt.Errorf("scheduled reboot not supported on %s", runtime.GOOS)
    }
}

// VerifyServices checks that each named service is running.
// Called by the agent on startup after a reboot change request.
func VerifyServices(ctx context.Context, services []string) map[string]string {
    results := map[string]string{}
    for _, svc := range services {
        var cmd *exec.Cmd
        if runtime.GOOS == "linux" {
            cmd = exec.CommandContext(ctx, "systemctl", "is-active", svc)
        } else {
            cmd = exec.CommandContext(ctx, "sc", "query", svc)
        }
        out, err := cmd.CombinedOutput()
        if err != nil {
            results[svc] = "failed: " + string(out)
        } else {
            results[svc] = "running"
        }
    }
    return results
}
```

**Post-reboot flow:** On agent startup, if the agent finds an `executing` change request of type `scheduled_reboot` associated with its asset ID, it calls `VerifyServices` and posts results back to the control plane, which marks the change request `completed` or `failed`.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/connectors/aws/actions.py` | Add `create_backup`, `verify_backup`, `dr_dns_failover_route53` action handlers |
| `backend/app/models/maintenance_window.py` | New model: `MaintenanceWindow` |
| `backend/app/models/access_review.py` | Add `AccessReviewSchedule` model (alongside existing `AccessReview`) |
| `backend/app/scheduler.py` | Add jobs: `mw-loader`, `access-review-check`, `compliance-scan-weekly`, `scheduled-reboot-dispatcher` |
| `backend/app/migrations/` | New Alembic migrations for `maintenance_windows`, `access_review_schedules` |
| `agent/commands/backup/backup_linux.go` | New: `RunBackup` using restic |
| `agent/commands/backup/restore_linux.go` | New: `RestoreFiles`, `VerifyChecksums` using restic |
| `agent/commands/reboot/reboot.go` | New: `Reboot`, `VerifyServices` for Linux + Windows |
| `agent/commands/dispatcher.go` | Handle `create_backup`, `restore_files`, `reboot` command types |
| `backend/app/change_types.py` | Register `create_backup`, `verify_backup`, `restore_files`, `dr_failover`, `scheduled_reboot` |
| `frontend/src/pages/ChangeRequests.tsx` | Add form fields for new change types; display backup/RTO results |
