# Backup & Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Backup & Recovery page — intent-based coverage view, backup job history, context-aware restore CR generation, and a pre-change confidence banner on CR detail.

**Architecture:** Built on top of the RecurringJob scheduler (see `2026-05-28-recurring-job-scheduler-design.md`). A `backup_targets` table tracks declared protection intent and computed health. Artifact references live as JSONB on completed CRs. The restore flow generates a standard CR pre-populated from a selected backup artifact. A confidence banner appears on CR detail when the target has a recent healthy backup.

**Tech Stack:** Python/SQLAlchemy (backend), React/TypeScript/TanStack Query (frontend), existing CR creation and execution workflow

**Depends on:** RecurringJob scheduler spec delivered first.

---

## Data Model

### `backup_targets` table

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `organization_id` | UUID FK | |
| `recurring_job_id` | UUID FK → recurring_jobs nullable | null = unprotected marker |
| `asset_id` | UUID FK → assets nullable | optional asset linkage |
| `target_description` | text | "nexplane postgres DB" |
| `expected_cadence_hours` | int | 24 for daily, 168 for weekly |
| `last_successful_backup_cr_id` | UUID FK → change_requests nullable | |
| `last_successful_at` | timestamptz nullable | |
| `status` | enum | `healthy`, `overdue`, `unprotected` |

**Status computation:**
- `healthy` — `last_successful_at` is within `expected_cadence_hours * 1.1` of now
- `overdue` — has a job but `last_successful_at` is older than cadence window
- `unprotected` — `recurring_job_id` is null

**Unprotected targets** are surfaced by comparing asset inventory against `backup_targets`. Assets with no linked `backup_target` appear as unprotected cards in the coverage view.

### CR `artifact_refs` column

New JSONB column added to `change_requests` table. Populated by the CR executor on completion for backup-type CRs:

```json
{
  "type": "s3_object",
  "bucket": "nexplane-db-backups-614130399980",
  "key": "nexplane-20260528-020000.sql.gz",
  "size_bytes": 284921,
  "checksum_sha256": "abc123..."
}
```

EBS snapshot example:
```json
{
  "type": "ebs_snapshot",
  "snapshot_id": "snap-0abc123def456",
  "volume_id": "vol-0abc123",
  "region": "us-east-1"
}
```

---

## API Endpoints

```
GET  /backup-targets                         list all for org (with status)
POST /backup-targets                         create unprotected marker or link to job
GET  /backup-targets/{id}                    detail + recent backup CR history
GET  /backup-history                         all completed backup CRs for org, paginated
POST /restore-crs                            create a restore CR from artifact reference
GET  /change-requests/{id}/backup-context    backup health for CR's target asset
```

### `POST /restore-crs` body

```json
{
  "source_cr_id": "uuid-of-backup-cr",
  "target_description": "nexplane postgres DB",
  "restore_type": "full",
  "notes": "Restoring after failed migration"
}
```

Returns a standard `ChangeRequestRead` — the restore CR goes through the normal approve → execute lifecycle.

### `GET /change-requests/{id}/backup-context`

Returns backup health for the asset targeted by this CR. Used by the CR detail confidence banner:

```json
{
  "has_backup": true,
  "last_successful_at": "2026-05-28T02:00:00Z",
  "artifact": {
    "type": "s3_object",
    "key": "nexplane-20260528-020000.sql.gz"
  },
  "backup_cr_id": "uuid",
  "overdue": false
}
```

---

## Backup & Recovery UI (`/backup-recovery`)

### Section 1: Coverage Summary

Card grid, one card per `backup_target` plus one card per unprotected asset.

**Healthy card:**
```
nexplane postgres DB
✓ Healthy
Last backup: 2h ago
Daily · S3
[View history] [Restore]
```

**Overdue card:**
```
prod EC2 (AMI)
⚠ Overdue — 3 days since last backup
Expected: daily
[View history] [Restore] [Run backup now]
```

**Unprotected card:**
```
config-store S3
✗ Unprotected
No backup job configured
[Configure backup →]
```

"Configure backup" opens the Create Job drawer (from Scheduled Ops) pre-filtered to `job_type = backup` and pre-filled with the asset's details.

"Run backup now" calls `POST /recurring-jobs/{id}/run-now`.

### Section 2: Backup History

Filterable table of completed `create_backup` and `ssm_command` CRs where `artifact_refs` is populated. Sorted by most recent first.

Columns: `Target | Artifact | Size | Completed | Status | Actions`

Actions column: **Restore** button → opens restore drawer pre-populated with this artifact.

Filter controls: target (dropdown), date range, status (success / failed).

### Section 3: Restore Drawer

Triggered by clicking Restore on any history row, or "Restore" on a coverage card.

Fields:
1. **Restore point** — dropdown of recent successful backups for this target, default = most recent. Shows timestamp + artifact key.
2. **Context banner** (conditional) — shown when a recent CR was executed against this target:
   > "A change was executed against this target 2h ago (`Deploy schema migration v4`). This backup predates that change — restoring will undo it."
3. **Notes** — free text, pre-populated with "Restoring after [most recent CR title]" when context is detected
4. **Confirm** → calls `POST /restore-crs` → creates CR → navigates to CR detail for approval + execution

The restore CR goes through the normal approve → execute lifecycle. It is not auto-approved — restores are consequential and require human sign-off.

---

## CR Detail Confidence Banner

On `ChangeRequestDetail.tsx`, for any CR that is not itself a backup or restore:

Call `GET /change-requests/{id}/backup-context`. If response has `has_backup: true`:

```
✓ nexplane postgres DB has a backup from 2h ago
[View backup CR]  [Initiate restore if needed]
```

If `overdue: true`:

```
⚠ Last backup for this target was 3 days ago — consider running a fresh backup before proceeding
[Run backup now]
```

If `has_backup: false`:

```
✗ No backup found for this target — proceed with caution
[Configure backup]
```

Banner renders below the blast radius section, above execution steps. Fetched only when CR status is `planned`, `awaiting_approval`, or `approved` — not shown for draft or completed CRs.

---

## Backup Target Lifecycle

1. Admin creates a backup `RecurringJob` — a `backup_target` row is automatically created and linked.
2. Job fires → CR executes → on success, `artifact_refs` written to CR, `backup_target.last_successful_at` and `last_successful_backup_cr_id` updated, `status` recomputed.
3. If job is disabled or deleted, `backup_target.recurring_job_id` is set to null and status transitions to `unprotected`.
4. Unprotected assets surface in the coverage view with a prompt to configure a job.

---

## Out of Scope (deferred)

- Restore verification (automated test restore to confirm artifact is valid)
- Multi-region backup replication
- Backup encryption key management
- Scheduled restore jobs for dev/test instance refresh (uses RecurringJob with `job_type = scheduled_restore` — model supports it, UI defer to v2)
- Retention policy enforcement beyond S3 lifecycle rules
