# Backup Target UI Design

**Goal:** Add self-service backup target creation and editing to the BackupRecovery page, surfacing the new `backup_tier` and `capture_strategy` fields through a user-friendly "backup type" abstraction, with inline prerequisite guidance.

---

## Section 1: Backup Type → Strategy Mapping

Operators select a user-facing backup type; the platform auto-selects the safest capture strategy based on asset context. Operators can override via an Advanced expander.

| Backup type | Asset context | Auto-selected strategy | Backup tier |
|---|---|---|---|
| Full machine image | EC2 instance | `ebs_snapshot` | machine |
| Full machine image | Windows bare metal / Hyper-V | `disk2vhd` | machine |
| Full machine image | Linux bare metal | `lvm_snapshot` | machine |
| Data files | Any Linux/Windows host | `local_files` | data |
| Data files | NFS share | `nfs_files` | data |
| Database dump | Self-hosted DB | `database_dump` | data |
| Database snapshot | RDS / Cloud SQL / Azure DB | `managed_db_snapshot` | data |

Strategies for Azure, GCP, and OCI assets are included in the mapping but marked with a "Not yet available" badge in the UI — they are stubs that will be fully implemented alongside their connector smoke suites. Saving a target with a stub strategy is allowed; execution surfaces a `plan_blocked` CR status.

The mapping is resolved by `GET /backup-targets/recommend-strategy?asset_id=&backup_type=` (see Section 3).

---

## Section 2: Slide-Over Form

A single slide-over component handles both create and edit. It is opened from two entry points:

- **BackupRecovery page** — "Add backup target" button in the page header. Asset field starts empty.
- **Asset detail page** — "Enable backup" action in the asset actions menu. Asset field is pre-populated and locked.

### Form fields (in order)

**1. Asset** — Searchable dropdown of fleet assets. Pre-populated and read-only when opened from an asset detail page. Required.

**2. Backup type** — Segmented control or radio group:
- Full machine image
- Data files
- Database dump
- Database snapshot

Selecting a type triggers `GET /backup-targets/recommend-strategy`, which populates field 3.

**3. Strategy** — Read-only display showing the auto-selected strategy name and a one-line reason (e.g., "EBS snapshot — native AWS, lowest RTO"). An "Advanced" disclosure link beneath it opens a dropdown listing all strategies in the same tier as alternatives. Stub strategies show a "Not yet available" badge but remain selectable.

**4. Storage destination** — Dropdown of existing `BackupStorage` records (name + type badge).
- If only one storage destination exists: auto-selected, no dropdown shown.
- If none exist: field shows "No destinations configured" with a "Set up storage" inline link that opens the BackupStorage creation flow.

**5. Cadence** — Preset buttons: Daily (24h) / Weekly (168h) / Monthly (720h) + "Custom" option that reveals a number input (`expected_cadence_hours`). Default: Daily.

**6. Description** — Optional free-text label (`target_description`).

### Inline prerequisite guidance

Warnings appear inline beneath the relevant field — non-blocking, operator can save and fix later:

- Asset has no matching connector for the selected strategy → "AWS connector required. [Configure connector]"
- Selected strategy is a stub → "Not yet available — execution will be blocked until this strategy is implemented."
- No storage destinations configured → shown in field 4 as described above.

All targets saved with unmet prerequisites surface as `unprotected` status on the BackupRecovery list, which already communicates the target is not ready.

### Submit behavior

- **Create:** `POST /backup-targets` with all form fields
- **Edit:** `PATCH /backup-targets/{id}` with changed fields only
- On success: slide-over closes, BackupRecovery list refreshes

---

## Section 3: Backend Changes

### Schema updates

**`BackupTargetCreate`** — add optional fields:
- `backup_tier: str | None` — defaults to `"machine"` server-side if omitted
- `capture_strategy: str | None` — defaults to `"ebs_snapshot"` server-side if omitted
- `storage_id: uuid.UUID | None`

**`BackupTargetRead`** — expose:
- `backup_tier: str`
- `capture_strategy: str`
- `storage_id: uuid.UUID | None`

### New endpoint: `PATCH /backup-targets/{id}`

Accepts partial updates: `target_description`, `expected_cadence_hours`, `capture_strategy`, `backup_tier`, `storage_id`. Returns updated `BackupTargetRead`. Requires org ownership check (same pattern as existing GET).

### New endpoint: `GET /backup-targets/recommend-strategy`

Query params: `asset_id: UUID`, `backup_type: str` (one of: `full_machine_image`, `data_files`, `database_dump`, `database_snapshot`)

Response:
```json
{
  "capture_strategy": "ebs_snapshot",
  "backup_tier": "machine",
  "reason": "EC2 instance — EBS snapshot is the native AWS approach with lowest RTO",
  "alternatives": ["mgn_replication"]
}
```

Logic is a pure function: reads the asset record (cloud platform, OS type, connector type) and returns the best strategy from the mapping table in Section 1. `alternatives` lists other valid strategies for the same tier, used to populate the Advanced override dropdown. No external calls.

---

## Section 4: Frontend Components

**`BackupTargetForm.tsx`** — slide-over component. Accepts optional `assetId` (locks asset field when provided) and optional `targetId` (edit mode). Manages form state, calls recommend-strategy on type change, submits create or update.

**`BackupRecovery.tsx`** — add "Add backup target" button to page header. Add edit action to each target row (opens slide-over with `targetId`).

**Asset detail page** — add "Enable backup" to asset actions menu. Opens `BackupTargetForm` with `assetId` pre-set.

**API client additions** (`endpoints.ts`):
- `backupApi.createTarget` — update to accept `backup_tier`, `capture_strategy`, `storage_id`
- `backupApi.updateTarget(id, data)` — new, calls `PATCH /backup-targets/{id}`
- `backupApi.recommendStrategy(assetId, backupType)` — new, calls the recommend endpoint

**Type updates** (`types/api.ts`):
- `BackupTarget` — add `backup_tier`, `capture_strategy`, `storage_id`
- `BackupTargetCreate` — add optional `backup_tier`, `capture_strategy`, `storage_id`

---

## Section 5: Out of Scope

- BackupStorage creation flow (linked from the form but not built here — existing flow)
- Connector creation flow (linked from prerequisite warnings but not built here)
- Bulk "protect everything" action (future — MCP-assisted project planning path)
- Strategy implementation for stubs (each ships with its own connector smoke suite)
