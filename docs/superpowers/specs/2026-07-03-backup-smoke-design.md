# Backup & Restore — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a cloud-agnostic, forensics-grade backup/restore system with a live smoke test covering all capture modes, all restore scenarios, and FILO rollback verification.

**Philosophy:** Backups serve dual purpose — raw data (forensic evidence, compliance artifacts) AND infrastructure recovery. Every backup captures both the machine state and the infrastructure definition so restoration is possible even when hardware is gone. Ransomware resilience is a first-class design goal: the restore path must work without the original machine, the original cloud account, or the original OS.

**Architecture:** Four new change types (`server_backup`, `server_snapshot`, `server_capture`, `restore_server`) under the `nexplane_agent` connector. Agent-dispatched execution. Configurable object storage backend (S3, GCS, Azure Blob, NFS, local). `BackupStorage` is a first-class org-level config object with smart defaults.

**Tech Stack:** Python async executors, nexplane Go agent (`avml`/`LiME` for Linux memory, WinPMEM for Windows, osxpmem for macOS), boto3/google-cloud-storage/azure-storage-blob, APScheduler (existing), PostgreSQL JSONB for artifact_refs.

---

## Global Constraints

- All four change types must define both `execute()` and `rollback()` — no stubs, no no-ops
- Rollback for `server_backup` and `server_capture` = delete artifacts from storage
- Rollback for `server_snapshot` = deregister cloud image + delete underlying blocks
- Rollback for `restore_server` to new target = terminate new instance; same-target restore requires explicit `confirm_same_target: true` flag and is treated as irreversible (no rollback)
- All smoke phases run from EC2 inside the nexplane-backend-1 container against live AWS infrastructure
- No mocks — every assertion against a real artifact (S3 object, AMI ID, EC2 instance)
- FILO rollback must be enforced: platform blocks rollback of an earlier CR if a later CR on the same asset is still applied
- `BackupStorage` must be configurable per org; safe default = S3 with IAM role (no explicit credentials on EC2)
- Storage dropdown in UI defaults to the backend holding the most existing artifacts for the org
- `server_capture` always captures: memory dump + disk snapshot + process list + network state + kernel modules + infra config (packages, services, cron, container manifests, Nexplane CR history for the asset)
- Supported asset types: `server`, `workstation`, `shared_drive` — same executor, different agent commands
- Compliance artifact linkage: completed backup CRs are linkable to compliance frameworks via existing evidence-collection infrastructure

---

## New Models

### `BackupStorage`

```python
class BackupStorage(Base):
    id: UUID
    organization_id: UUID
    name: str                          # "Primary S3", "Offsite NAS", etc.
    storage_type: str                  # "s3" | "gcs" | "azure_blob" | "nfs" | "local"
    config: dict                       # type-specific: bucket/prefix/endpoint/credentials
    is_org_default: bool               # inherited by all backup jobs unless overridden
    created_at: datetime
```

Config shapes per type:
- `s3`: `{"bucket": str, "prefix": str, "region": str, "role_arn": str | None, "access_key": str | None, "secret_key": str | None}`
- `gcs`: `{"bucket": str, "prefix": str, "service_account_json": str | None}`
- `azure_blob`: `{"account_name": str, "container": str, "sas_token": str | None}`
- `nfs`: `{"host": str, "export": str, "mount_options": str}`
- `local`: `{"path": str}` — flagged as non-production in UI

### `artifact_refs` schema (on ChangeRequest)

```json
{
  "capture_type": "backup | snapshot | capture",
  "storage_type": "s3 | gcs | azure_blob | nfs | local",
  "storage_id": "<BackupStorage UUID>",
  "bucket_or_path": "nexplane-backups",
  "prefix": "backups/{org_id}/{asset_id}/{cr_id}/",
  "artifacts": {
    "filesystem_archive": "backups/.../fs.tar.zst",
    "disk_snapshot_id": "snap-0abc123",
    "ami_id": "ami-0abc123",
    "memory_dump": "captures/.../memory.lime.zst",
    "process_list": "captures/.../processes.json",
    "network_state": "captures/.../netstat.json",
    "kernel_modules": "captures/.../lsmod.json",
    "infra_config": "captures/.../infra.json"
  },
  "captured_at": "2026-07-03T04:00:00Z",
  "asset_type": "server | workstation | shared_drive",
  "size_bytes": 17179869184
}
```

`infra_config` artifact contains:
```json
{
  "packages": [{"name": str, "version": str}],
  "services": [{"name": str, "enabled": bool, "config_files": [str]}],
  "cron_jobs": [str],
  "containers": [{"image": str, "name": str, "compose_file": str | null}],
  "network": {"interfaces": [...], "routes": [...], "firewall_rules": [...]},
  "nexplane_cr_history": [{"cr_id": str, "change_type": str, "applied_at": str}]
}
```

---

## New Change Types & Executors

### `server_backup`
**File:** `backend/app/connectors/executors/nexplane_agent/server_backup.py`

Dispatches agent job `server_backup` with parameters:
```json
{
  "storage_type": "s3",
  "bucket": "nexplane-backups",
  "prefix": "backups/{org_id}/{asset_id}/{cr_id}/",
  "storage_credentials": {},
  "asset_type": "server",
  "exclude_paths": ["/proc", "/sys", "/dev", "/run"]
}
```

Agent runs: `rsync -aAX --exclude=... / | zstd | aws s3 cp - s3://bucket/prefix/fs.tar.zst`
For shared drives: replaces source path with UNC/NFS mount path.

Returns: `{"status": "completed", "artifact_refs": {...}, "_asset_ids": [...]}`

Rollback: delete all objects under prefix from storage backend.

### `server_snapshot`
**File:** `backend/app/connectors/executors/nexplane_agent/server_snapshot.py`

Split execution:
1. Dispatch agent job `server_snapshot_pre` → agent runs `sync; fsfreeze -f /` (Linux) or VSS flush (Windows)
2. Platform executor calls cloud API:
   - AWS: `ec2.create_image(InstanceId=..., NoReboot=True)`
   - Azure: `compute_client.virtual_machine_images.begin_create(...)`
   - GCP: `compute_v1.MachineImagesClient().insert(...)`
   - Bare metal: dispatch agent job `server_snapshot_dd` → `dd if=/dev/sda | zstd | upload to storage`
3. Dispatch agent job `server_snapshot_post` → agent runs `fsfreeze -u /`

Returns: `{"ami_id": "ami-...", "snapshot_ids": [...], "artifact_refs": {...}, "_asset_ids": [...]}`

Rollback (cloud): deregister AMI + delete all associated EBS snapshots.
Rollback (bare metal): delete storage artifact.

### `server_capture`
**File:** `backend/app/connectors/executors/nexplane_agent/server_capture.py`

Dispatches agent job `server_capture` with storage credentials. Agent runs in parallel:
- Memory: `avml /tmp/memory.lime && zstd /tmp/memory.lime && upload`  (Linux); `winpmem /tmp/memory.raw` (Windows); `osxpmem /tmp/memory.aff4` (macOS)
- Disk: same as `server_snapshot` cloud API path
- Metadata: `ps aux`, `ss -tunap`, `lsmod`, `systemctl list-units`, `dpkg -l / rpm -qa`, `docker ps`, CR history query

All artifacts uploaded to storage under same prefix.

Returns: `{"status": "completed", "artifact_refs": {...}, "_asset_ids": [...]}`

Rollback: delete all artifacts from storage (memory dump, metadata JSONs, disk snapshot/AMI).

### `restore_server`
**File:** `backend/app/connectors/executors/nexplane_agent/restore_server.py`

Parameters:
```json
{
  "source_backup_cr_id": "<UUID>",
  "restore_mode": "full | hybrid | rebuild",
  "target": {"type": "same"} | {"type": "new", "instance_type": "t3.medium", "subnet_id": "..."},
  "confirm_same_target": false,
  "recovery_token": "<one-time token, set if target is a fresh bootstrap machine>"
}
```

**`full` mode:** Stream storage artifact to target disk via agent job `restore_full`. Agent runs `aws s3 cp s3://... - | zstd -d | dd of=/dev/sda`. Requires reboot after.

**`hybrid` mode:** Launch new instance from AMI (cloud API) → deploy agent → dispatch `restore_config` job to apply infra_config corrections.

**`rebuild` mode:** Launch fresh OS → deploy agent (or await recovery token registration) → dispatch `restore_rebuild` job → agent replays `infra_config` (installs packages, enables services, restores cron, starts containers).

Rollback:
- New target: terminate new instance + delete any artifacts created
- Same target (`confirm_same_target: true`): no rollback — raises `IrreversibleOperationError`

---

## New API Endpoints

```
POST /backup-storage                    Create BackupStorage config
GET  /backup-storage                    List org's storage backends
GET  /backup-storage/{id}              Get single backend
PUT  /backup-storage/{id}              Update
DELETE /backup-storage/{id}            Delete (blocks if any CRs reference it)

POST /assets/{id}/generate-recovery-token   Issue one-time bootstrap token (60min TTL)
GET  /backup-history?asset_id=&storage_id=  Existing endpoint, add storage_id filter
```

---

## Bootstrap / Recovery Harness

`POST /assets/{id}/generate-recovery-token` writes a `RecoveryToken` record:
```python
class RecoveryToken(Base):
    token: str          # one-time random hex, hashed in DB
    asset_id: UUID      # asset to restore
    restore_cr_id: UUID # pending restore_server CR to auto-dispatch
    expires_at: datetime  # 60 minutes
    used: bool
```

Bootstrap script (`user_data` on EC2, or embedded in recovery ISO):
```bash
#!/bin/bash
curl -fsSL https://<platform>/install-agent.sh | \
  NEXPLANE_URL=https://<platform> \
  NEXPLANE_RECOVERY_TOKEN=<token> \
  bash
```

Agent startup with `NEXPLANE_RECOVERY_TOKEN` set:
1. Registers as new asset in recovery mode
2. Platform detects token → validates → marks token used → auto-approves pending `restore_server` CR → dispatches restore job to the new agent

---

## Catalog Entry

Added to `backend/app/connectors/catalog/nexplane_agent.json`:
```json
[
  {
    "action_id": "server_backup",
    "display_name": "Server Backup",
    "executor": "nexplane_agent.server_backup",
    "rollback_action": "server_backup",
    "execution_tier": 2,
    "applicable_asset_types": ["server", "workstation", "shared_drive"]
  },
  {
    "action_id": "server_snapshot",
    "display_name": "Server Snapshot",
    "executor": "nexplane_agent.server_snapshot",
    "rollback_action": "server_snapshot",
    "execution_tier": 2,
    "applicable_asset_types": ["server", "workstation"]
  },
  {
    "action_id": "server_capture",
    "display_name": "Full Forensic Capture",
    "executor": "nexplane_agent.server_capture",
    "rollback_action": "server_capture",
    "execution_tier": 3,
    "applicable_asset_types": ["server", "workstation"]
  },
  {
    "action_id": "restore_server",
    "display_name": "Restore Server",
    "executor": "nexplane_agent.restore_server",
    "rollback_action": "restore_server",
    "execution_tier": 3,
    "applicable_asset_types": ["server", "workstation"]
  }
]
```

---

## Database Migration

New migration `bkp001`:
- `backup_storage` table (BackupStorage model)
- `recovery_tokens` table (RecoveryToken model)
- Add `storage_id` FK column to `backup_targets`
- Add `asset_type` column to `backup_targets`

---

## Smoke Test: `test_backup_scheduler_live.py` (rewritten)

Phases selectable via `--phases` flag. All run on EC2 from inside the nexplane-backend-1 container.

### Phase: BACKUP_SCHEDULER_SMOKE

**Setup:**
- Provision EC2 t3.small (AL2023) + deploy nexplane agent, or reuse `ASSET_ID` env var
- Create `BackupStorage` record pointing to `nexplane-smoke-backups-{hash}` S3 bucket (auto-created)

**B1 — server_backup:**
- Create + execute `server_backup` CR on the asset
- Assert CR status = `completed`, `artifact_refs.artifacts.filesystem_archive` key present
- Assert S3 object at that key exists, size > 0
- Write sentinel file `/tmp/nexplane-smoke-sentinel.txt` on the instance before backup

**B2 — server_snapshot:**
- Create + execute `server_snapshot` CR
- Assert `artifact_refs.artifacts.ami_id` present
- Assert `ec2.describe_images(ImageIds=[ami_id])` returns State=available
- Assert `artifact_refs.artifacts.snapshot_ids` non-empty

**B3 — server_capture:**
- Create + execute `server_capture` CR
- Assert all artifact keys present: `memory_dump`, `disk_snapshot_id`, `process_list`, `network_state`, `kernel_modules`, `infra_config`
- Assert S3 object size > 0 for each
- Assert `infra_config` JSON contains `packages` list (non-empty) and `nexplane_cr_history` (includes CR-B1, CR-B2)

**B4 — scheduled backup:**
- Create `RecurringJob` (job_type=backup, change_type=server_backup, cron=`0 2 * * *`)
- Fire via `POST /recurring-jobs/{id}/run-now`
- Poll `/backup-history?asset_id=X` until new completed CR appears (60s timeout)
- Assert `BackupTarget.status` = healthy

**B5 — bare metal sim:**
- Create + execute `server_backup` CR with `{"bare_metal_sim": true}` parameter (disables cloud API path)
- Assert artifact in S3, no AMI created

**R1 — full restore to new cloud instance:**
- Create `restore_server` CR: `restore_mode=full`, `target={"type":"new","instance_type":"t3.micro",...}`, `source_backup_cr_id=B1`
- Execute CR, poll until completed
- SSH/SSM to new instance, assert sentinel file `/tmp/nexplane-smoke-sentinel.txt` present
- Rollback: terminate new instance

**R2 — hybrid restore to new cloud instance:**
- Create `restore_server` CR: `restore_mode=hybrid`, source=B2 (AMI)
- Execute, poll until completed
- Assert new instance launched from AMI, running
- Rollback: terminate new instance

**R3 — rebuild restore to new cloud instance:**
- Create `restore_server` CR: `restore_mode=rebuild`, source=B3 (infra_config)
- Execute, poll until completed
- Assert installed packages on new instance match B3 `infra_config.packages` (sample check)
- Rollback: terminate new instance

**R4 — same-instance restore (agent present, files corrupted):**
- Delete sentinel file on original instance: `rm /tmp/nexplane-smoke-sentinel.txt`
- Create `restore_server` CR: `restore_mode=full`, `target={"type":"same"}`, `confirm_same_target=true`, source=B1
- Execute, poll until completed
- Assert sentinel file present again on original instance

**R5 — clean hardware bootstrap (recovery token):**
- Terminate original instance (if provisioned in this run)
- `POST /assets/{asset_id}/generate-recovery-token` → get token
- Launch fresh EC2 t3.micro (AL2023) with `user_data` bootstrap script embedding token
- Poll `/assets` until new asset appears with `recovery_mode=true` (60s timeout)
- Assert pending `restore_server` CR auto-dispatched and completes (120s timeout)
- Assert sentinel file present on new instance via SSM

**R6 — rebuild from capture on clean hardware:**
- Launch fresh EC2 with recovery token from B3 capture
- Assert `restore_mode=rebuild` CR auto-dispatched, packages match B3 `infra_config.packages`

**FILO guard:**
- Run B1 then B2 sequentially (two separate CRs on same asset, different application_sequence)
- Attempt rollback of B1 while B2 still applied
- Assert `POST /change-requests/{B1_cr_id}/rollback` returns 409 with blocking_crs=[B2_cr_id]
- Rollback B2 first → assert deregistered AMI deleted
- Rollback B1 → assert S3 artifact deleted
- Assert `_count_s3_prefix(B1_prefix)` == 0

**Cleanup (finally block):**
- Terminate any provisioned instances
- Delete S3 bucket contents
- Deregister any remaining AMIs + delete snapshots
- Delete BackupStorage record

---

### Phase: PLATFORM_UPGRADE_ROLLBACK

Existing test — unchanged. Exercises `_simulate_post_migration_failure=True` flag on platform_upgrade CR, verifies pg_restore path in rollback result.

---

### Phase: AD_MEMBER_TIERS

Existing test — unchanged. Tier 1 EBS dry run + real, Tier 2 VSS dry run + real, FILO rollback (tier 2 then tier 1).

---

## Compliance Note

Completed `server_backup`, `server_snapshot`, and `server_capture` CRs against `workstation` and `shared_drive` asset types are automatically linkable to compliance evidence via the existing evidence-collection infrastructure. No new compliance code required — asset_type flows through to the CR record, and auditors can filter `GET /backup-history?asset_type=workstation` to produce evidence of endpoint backup cadence.

---

## Rollback Summary

| Change type | Rollback action | Irreversible? |
|---|---|---|
| `server_backup` | Delete all objects at `artifact_refs.prefix` from storage | No |
| `server_snapshot` | Deregister AMI + delete EBS snapshots; or delete storage artifact (bare metal) | No |
| `server_capture` | Delete all artifacts (memory dump, metadata JSONs, disk snapshot) | No |
| `restore_server` (new target) | Terminate new instance | No |
| `restore_server` (same target) | `IrreversibleOperationError` raised | Yes — requires explicit flag |
