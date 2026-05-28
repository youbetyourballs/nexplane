# Recurring Job Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a first-class `RecurringJob` model and scheduler that fires on a cron schedule, creates and auto-executes CRs, and powers the Scheduled Ops UI.

**Architecture:** A `recurring_jobs` DB table is loaded into APScheduler at startup. Each job fires `_execute_recurring_job(job_id)` which creates, auto-approves, and executes a CR. CRUD operations keep APScheduler in sync without restarts. The Scheduled Ops page (`/scheduled-operations`) is the full management UI for all job types.

**Tech Stack:** Python/SQLAlchemy/APScheduler (backend), React/TypeScript/TanStack Query (frontend), existing CR execution workflow, existing APScheduler `scheduler_service.py`

---

## Data Model

### `recurring_jobs` table

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID FK | |
| `name` | text | "Daily nexplane DB backup" |
| `job_type` | enum | `backup`, `scheduled_restore`, `scheduled_op` |
| `connector_id` | UUID FK → connectors | nullable |
| `action_id` | text | "run_ssm_command", "create_backup" |
| `parameters` | JSONB | action-specific params |
| `target_description` | text | human label for coverage view |
| `cron_expression` | text | "0 2 * * *" |
| `schedule_preset` | text nullable | "daily", "weekly", "hourly", null = custom |
| `schedule_hour` | int nullable | for preset UI rendering |
| `enabled` | bool | default true |
| `last_run_at` | timestamptz nullable | |
| `last_cr_id` | UUID FK → change_requests nullable | |
| `next_run_at` | timestamptz | computed on save, updated after each fire |
| `created_by` | UUID FK → users | |
| `created_at` | timestamptz | |

### CR `execution_result` addition

Existing CRs gain an `artifact_refs` JSONB field on the `change_plans` or as a new column on `change_requests`. Populated by the executor on completion:

```json
{
  "type": "s3_object",
  "bucket": "nexplane-db-backups-614130399980",
  "key": "nexplane-20260528-020000.sql.gz",
  "size_bytes": 284921,
  "checksum_sha256": "abc123..."
}
```

Or for snapshots: `{"type": "ebs_snapshot", "snapshot_id": "snap-0abc123"}`.

---

## API Endpoints

```
GET    /recurring-jobs                    list all for org
POST   /recurring-jobs                    create + register in APScheduler
GET    /recurring-jobs/{id}               detail + recent CR history
PUT    /recurring-jobs/{id}               update + re-register in APScheduler
DELETE /recurring-jobs/{id}               remove + deregister
POST   /recurring-jobs/{id}/enable        enable + register
POST   /recurring-jobs/{id}/disable       disable + deregister
POST   /recurring-jobs/{id}/run-now       fire immediately (outside schedule)
```

---

## Scheduler Behaviour

**Startup:** `scheduler_service.py` queries all `recurring_jobs WHERE enabled = true` and registers each as an APScheduler `CronTrigger` job.

**Fire function `_execute_recurring_job(job_id)`:**
1. Load `RecurringJob` from DB
2. Create CR: `title = job.name`, `change_type` derived from `job.action_id`, `desired_outcome = job.parameters`, `source = "recurring_job"`, metadata includes `recurring_job_id`
3. Auto-approve the CR (no human approval — the act of configuring the job is the approval)
4. Execute via existing CR execution workflow
5. Update `job.last_run_at`, `job.last_cr_id`, `job.next_run_at`

**CRUD sync:** Create → `scheduler.add_job()`. Update → `scheduler.reschedule_job()`. Enable → `scheduler.resume_job()`. Disable → `scheduler.pause_job()`. Delete → `scheduler.remove_job()`. All in same request, no restart needed.

**Failure:** If the CR fails, no automatic retry. Next scheduled fire handles it. The failed CR is visible in job history.

---

## Scheduled Ops UI (`/scheduled-operations`)

### Summary strip
- Total jobs / N enabled / N disabled / N overdue

### Main table columns
`Name | Type | Target | Schedule | Last Run | Next Run | Status`

Status values: `✓ Healthy`, `⚠ Overdue`, `✗ Disabled`, `⚡ Running`

### Row expansion (inline)
- Last 5 CR executions: status badge + timestamp + link to CR detail
- Edit / Enable / Disable / Run Now buttons
- Cron expression display with Edit toggle

### Create Job drawer
Fields in order:
1. Name
2. Job type: Backup / Scheduled Restore / Scheduled Op
3. Connector (dropdown, filtered by org)
4. Action (filtered by connector type and job type)
5. Parameters (dynamic form matching action schema)
6. Target description (free text)
7. Schedule:
   - Preset tabs: Hourly / Daily / Weekly — each with a time-of-day picker
   - "Custom (cron)" tab — raw cron expression input with preview of next 3 fire times
8. Save → job created and registered immediately

### Run Now
Creates an immediate CR with `source = "recurring_job_manual"`. Same auto-approval. Shows in job history alongside scheduled runs.

---

## Cron Expression Helpers

- Preset "daily" + hour 2 → `0 2 * * *`
- Preset "weekly" + Monday + hour 2 → `0 2 * * 1`
- Preset "hourly" → `0 * * * *`
- Custom: validate with `croniter` library, show next 3 fire times as preview

---

## Out of Scope (deferred)

- Retry on failure
- Job dependencies (run B after A)
- Per-execution approval override flag
- Notification on job failure (covered by existing notification system, wired separately)
