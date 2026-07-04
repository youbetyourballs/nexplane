# Backup Strategy Implementations Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement all 6 stub backup strategies (`storage_sync`, `lvm_snapshot`, `nfs_files`, `managed_db_snapshot`, `disk2vhd`, `mgn_replication`) with live smoke tests that provision required infra, verify the full backup+rollback CR lifecycle, then tear everything down.

**Architecture:** Each strategy follows the same contract as `ebs_snapshot`: `backup(params, asset_ids, connector) -> dict` produces an artifact (S3 object or cloud-native snapshot) and returns `artifact_refs`; `rollback(params, execution_result, connector) -> dict` deletes the artifact. Smoke tests run the full Nexplane CR lifecycle (create → plan → approve → execute → verify artifact → rollback → verify gone) then destroy all provisioned infra. Strategies are implemented and smoked in order of increasing infra complexity.

**Tech Stack:** Python/asyncio, paramiko (SSH), WinRM (`pywinrm`), boto3 (S3 + RDS + MGN + EC2), existing `storage_backends.s3`, existing `_load_storage_config` / `_ssh_connect` helpers from `local_files.py`.

---

## Global Constraints

- SPDX header on every file: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`
- All smoke tests run on EC2 runner (Tailscale), never from the Windows laptop
- Smoke uses live infrastructure only — no mocks, no local Docker
- All provisioned infra (EC2, RDS, MGN source server, test instances) must be terminated/deleted at smoke end, including on failure paths
- AMI cache pattern applies to any infra taking >60s to provision: cache key written to SSM at `/nexplane/smoke-amis/{service}/{hash}`; use `get_or_create_smoke_ami()` helper if it exists, otherwise inline the pattern
- Smoke artifacts use bucket `nexplane-smoke-backup-scheduler`; each strategy uses a unique prefix like `smoke-{strategy}-{uuid}/`
- `disk2vhd.exe` is pre-uploaded to `s3://nexplane-smoke-backup-scheduler/tools/disk2vhd.exe` before smoke runs; do not download from the public internet on the Windows host
- All smoke phases append to `backend/tests/smoke/test_backup_scheduler_live.py`; phase names: `LVM_SNAPSHOT`, `NFS_FILES`, `STORAGE_SYNC`, `MANAGED_DB_SNAPSHOT`, `DISK2VHD`, `MGN_REPLICATION`
- Backup strategy files live at `backend/app/connectors/executors/nexplane_agent/backup_strategies/{name}.py`
- Unit tests in `backend/app/tests/test_backup_architecture.py` (existing file); add one test class per strategy
- Push to master after each strategy is complete and smoke is passing; do not batch

---

## Shared Patterns

### artifact_refs structure (all strategies)
```python
{
    "capture_strategy": str,          # strategy name
    "restore_strategy": str,          # paired restore strategy name
    "backup_tier": str,               # "machine" or "data"
    "captured_at": str,               # ISO 8601 UTC timestamp
    # strategy-specific fields below
}
```

### SSH helper
Re-use `_ssh_connect(creds: dict)` from `local_files.py` (supports Ed25519/ECDSA/RSA). Import it:
```python
from app.connectors.executors.nexplane_agent.backup_strategies.local_files import _ssh_connect
```

### Storage backend
```python
from app.connectors.executors.nexplane_agent.storage_backends import get_backend
from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config

storage_config = params.get("_storage_config") or await _load_storage_config(params["backup_storage_id"])
backend = get_backend(storage_config["storage_type"])
cfg = storage_config["config"]
```

### Smoke CR lifecycle pattern
```python
# Already defined in test_backup_scheduler_live.py — reuse these helpers:
_create_and_run_cr(client, title, change_type, asset_id, desired_outcome)
_plan_and_approve_cr(client, cr_id)
_execute_cr(client, cr_id)
_wait_cr_complete(client, cr_id, label, timeout=300)
_rollback_cr(client, cr_id)
_extract_artifact_refs(cr)
```

---

## Strategy 1: `storage_sync`

**File:** `backend/app/connectors/executors/nexplane_agent/backup_strategies/storage_sync.py`

**Infra needed:** None — uses existing S3 buckets.

### Implementation

**backup(params, asset_ids, connector):**
- `params` keys: `source_storage_id` (UUID of a BackupStorage record with S3 config), `source_prefix` (str, e.g. `"data/server-a/"`), `backup_storage_id` (UUID of dest BackupStorage)
- Load source config: `source_config = params.get("_source_config") or await _load_storage_config(params["source_storage_id"])` — smoke passes `_source_config` inline to avoid needing a second BackupStorage DB record
- Load dest config via `_load_storage_config(params["backup_storage_id"])`
- Assert both are `storage_type == "s3"` (only S3→S3 in v1; raise `NotImplementedError` for other combos with a clear message)
- Use `boto3.client("s3")` to list objects under `source_prefix` in source bucket
- For each object: copy to dest bucket under `dest_prefix = f"{cfg_dest['prefix']}{asset_id}/{captured_at}/"`
- Return:
```python
{
    "status": "completed",
    "artifact_refs": {
        "capture_strategy": "storage_sync",
        "restore_strategy": "storage_restore",
        "backup_tier": "data",
        "captured_at": captured_at,
        "source_storage_type": "s3",
        "source_bucket": source_cfg["bucket"],
        "source_prefix": source_prefix,
        "dest_storage_type": "s3",
        "dest_bucket": dest_cfg["bucket"],
        "dest_prefix": dest_prefix,
        "synced_count": int,
        "synced_bytes": int,
    },
    "_asset_ids": [str(a) for a in asset_ids],
}
```

**rollback(params, execution_result, connector):**
- Extract `dest_bucket`, `dest_prefix`, `dest_storage_type` from `execution_result["artifact_refs"]`
- Call `backend.delete_prefix(dest_prefix, {"bucket": dest_bucket, ...})`
- Return `{"rolled_back": True, "deleted_prefix": dest_prefix}`

### Smoke phase: `STORAGE_SYNC`

Setup:
1. Put 3 test objects in `nexplane-smoke-backup-scheduler` under prefix `smoke-storage-sync-src-{uuid}/`
2. Create a `server_backup` CR (or use `catalog_action` directly) with `desired_outcome` including `_source_config` and `_storage_config` injected inline (avoids needing separate BackupStorage DB records)

Actually: smoke passes configs inline via `_source_config` / `_storage_config` in `desired_outcome`, using the existing nexplane-smoke-backup-scheduler bucket creds from SSM.

Verify:
- After execute: list dest prefix, confirm 3 objects present with matching sizes
- After rollback: list dest prefix, confirm 0 objects

Teardown: delete src prefix objects.

---

## Strategy 2: `lvm_snapshot`

**File:** `backend/app/connectors/executors/nexplane_agent/backup_strategies/lvm_snapshot.py`

**Infra needed:** Linux EC2 (t3.small, Amazon Linux 2023) with:
- Extra 10 GiB EBS volume (`/dev/xvdf`)
- LVM configured: VG `vg0`, LV `data` (5 GiB), test files written to `ext4` filesystem on it
- AMI cached in SSM at `/nexplane/smoke-amis/lvm-nfs/{hash}`

User-data script (run once, AMI cached):
```bash
#!/bin/bash
yum install -y lvm2 nfs-utils nfs-kernel-server
pvcreate /dev/xvdf
vgcreate vg0 /dev/xvdf
lvcreate -L 5G -n data vg0
mkfs.ext4 /dev/vg0/data
mkdir -p /mnt/data
mount /dev/vg0/data /mnt/data
echo "test-file-1" > /mnt/data/file1.txt
echo "test-file-2" > /mnt/data/file2.txt
# NFS export for nfs_files smoke
mkdir -p /srv/nfs-export
echo "nfs-test-file" > /srv/nfs-export/nfs1.txt
echo "/srv/nfs-export *(ro,sync,no_subtree_check)" >> /etc/exports
systemctl enable --now nfs-server
exportfs -a
```

### Implementation

**backup(params, asset_ids, connector):**
- `params` keys: `vg_name` (str), `lv_name` (str), `snapshot_size` (str, default `"1G"`), `backup_storage_id`, SSH creds via connector or `ssh_creds` inline param
- Connect via SSH using `_ssh_connect`
- Run: `lvcreate --snapshot -L {snapshot_size} -n {lv_name}_snap /dev/{vg_name}/{lv_name}`
- Run: `dd if=/dev/{vg_name}/{lv_name}_snap bs=4M | gzip > /tmp/lvm_snap_{ts}.img.gz`
- Poll for dd completion via `exec_command` exit status
- SFTP get `/tmp/lvm_snap_{ts}.img.gz` to platform tmp dir
- Upload to S3 via `backend.upload(local_tmp, archive_key, cfg)`
- Run: `lvremove -f /dev/{vg_name}/{lv_name}_snap`
- Delete platform tmp file
- Return artifact_refs with `vg_name`, `lv_name`, `artifact_uri`, `size_bytes`

**rollback(params, execution_result, connector):**
- Extract `artifact_uri`, storage config from `execution_result["artifact_refs"]`
- `backend.delete(artifact_uri, cfg)`
- Return `{"rolled_back": True}`

### Smoke phase: `LVM_SNAPSHOT`

1. Get-or-create LVM/NFS AMI (shared with NFS_FILES — provision once, both phases use same instance)
2. Launch EC2 from AMI, wait for SSH-ready
3. Create `server_backup` CR with `capture_strategy=lvm_snapshot`, inline creds
4. Execute CR, verify `artifact_uri` exists in S3
5. Rollback CR, verify S3 object gone
6. Do NOT terminate EC2 yet — NFS_FILES smoke runs next on same instance

---

## Strategy 3: `nfs_files`

**File:** `backend/app/connectors/executors/nexplane_agent/backup_strategies/nfs_files.py`

**Infra needed:** Same EC2 as `lvm_snapshot` (already running). NFS server already configured by user-data.

### Implementation

**backup(params, asset_ids, connector):**
- `params` keys: `nfs_export_path` (str, the path of the NFS export directory on the server, e.g. `/srv/nfs-export`), `backup_storage_id`, SSH creds
- Connect via SSH to the NFS server host
- Run `find {nfs_export_path} -type f | wc -l` and `du -sb {nfs_export_path} | cut -f1` for stats
- Run `tar czf - {nfs_export_path}` piped to tmp file on server, SFTP get, upload to S3
- Return artifact_refs with `nfs_export_path`, `file_count`, `size_bytes`, `artifact_uri`

**rollback:** identical to `lvm_snapshot` rollback — delete S3 artifact.

### Smoke phase: `NFS_FILES`

1. Reuse same EC2 from LVM_SNAPSHOT phase (already running)
2. Verify `/srv/nfs-export/nfs1.txt` exists via SSH
3. Create CR with `capture_strategy=nfs_files`, `nfs_export_path=/srv/nfs-export`
4. Execute, verify S3 artifact present
5. Rollback, verify S3 artifact gone
6. Terminate EC2 after this phase completes

---

## Strategy 4: `managed_db_snapshot`

**File:** `backend/app/connectors/executors/nexplane_agent/backup_strategies/managed_db_snapshot.py`

**Infra needed:** RDS `db.t3.micro` postgres instance in the platform VPC. Provisioned fresh for smoke, deleted after. No AMI cache (RDS provision ~5min).

### Implementation

**backup(params, asset_ids, connector):**
- `params` keys: `aws_connector_id`, `db_instance_identifier` (str, the RDS DB instance ID)
- Load AWS creds via `_load_aws_creds(aws_connector_id, connector)` (re-use from `ebs_snapshot.py`)
- `rds = boto3.client("rds", **aws_creds)`
- `snapshot_id = f"nexplane-snap-{db_instance_identifier}-{ts_safe}"`
- `rds.create_db_snapshot(DBSnapshotIdentifier=snapshot_id, DBInstanceIdentifier=db_instance_identifier)`
- Poll `rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)` until `Status == "available"` (timeout 600s, poll every 10s)
- Return:
```python
{
    "status": "completed",
    "artifact_refs": {
        "capture_strategy": "managed_db_snapshot",
        "restore_strategy": "database_restore",
        "backup_tier": "data",
        "captured_at": captured_at,
        "snapshot_id": snapshot_id,
        "snapshot_arn": response["DBSnapshot"]["DBSnapshotArn"],
        "engine": response["DBSnapshot"]["Engine"],
        "allocated_storage_gb": response["DBSnapshot"]["AllocatedStorage"],
        "db_instance_identifier": db_instance_identifier,
        "aws_connector_id": aws_connector_id,
    },
    "_asset_ids": [str(a) for a in asset_ids],
}
```

**rollback(params, execution_result, connector):**
- Extract `snapshot_id`, `aws_connector_id` from `execution_result["artifact_refs"]`
- `rds.delete_db_snapshot(DBSnapshotIdentifier=snapshot_id)`
- Poll until snapshot no longer exists (or get `DBSnapshotNotFound` exception)
- Return `{"rolled_back": True, "deleted_snapshot_id": snapshot_id}`

### Smoke phase: `MANAGED_DB_SNAPSHOT`

Setup:
1. `boto3.rds.create_db_instance(DBInstanceIdentifier="nexplane-smoke-rds-{uuid}", DBInstanceClass="db.t3.micro", Engine="postgres", MasterUsername="smoke", MasterUserPassword="...", AllocatedStorage=20, VpcSecurityGroupIds=[...], DBSubnetGroupName="...", MultiAZ=False, PubliclyAccessible=False, BackupRetentionPeriod=0)` — ~5min
2. Poll until `status == "available"`

Smoke:
3. Create CR with `capture_strategy=managed_db_snapshot`, `db_instance_identifier="nexplane-smoke-rds-{uuid}"`
4. Execute, verify `snapshot_id` in artifact_refs, verify snapshot exists via `describe_db_snapshots`
5. Rollback, verify snapshot deleted

Teardown (always, including on failure):
6. `rds.delete_db_snapshot(...)` if rollback didn't happen
7. `rds.delete_db_instance(DBInstanceIdentifier="nexplane-smoke-rds-{uuid}", SkipFinalSnapshot=True, DeleteAutomatedBackups=True)`

---

## Strategy 5: `disk2vhd`

**File:** `backend/app/connectors/executors/nexplane_agent/backup_strategies/disk2vhd.py`

**Infra needed:** `t3.medium` Windows Server 2022 EC2 (Base AMI). AMI cached after first boot+WinRM-ready state.

**Prerequisite (one-time):** Upload `disk2vhd.exe` to `s3://nexplane-smoke-backup-scheduler/tools/disk2vhd.exe` before smoke runs. This avoids downloading from the public internet on the Windows host.

### Implementation

**backup(params, asset_ids, connector):**
- `params` keys: `winrm_host`, `winrm_username`, `winrm_password`, `disk_list` (list of drive letters, default `["C:"]`), `backup_storage_id`, optionally `_disk2vhd_s3_key` (default `"tools/disk2vhd.exe"`)
- `pip` dep: `pywinrm` (add to `requirements.txt` if not present)
- Connect: `winrm.connect(winrm_host, auth=(username, password), transport="ntlm")`
- Download disk2vhd from S3 to platform tmp dir, SFTP to `C:\Windows\Temp\disk2vhd.exe` on Windows host
- Run: `C:\Windows\Temp\disk2vhd.exe {drives_str} C:\Windows\Temp\backup_{ts}.vhdx /accepteula`
  - `drives_str` = space-joined drive letters e.g. `"C:"`
  - Poll WinRM for process completion (disk2vhd is synchronous; WinRM exec blocks until done)
- SFTP get `C:\Windows\Temp\backup_{ts}.vhdx` from Windows host to platform tmp
- Upload to S3 via `backend.upload(local_tmp, archive_key, cfg)`
- Delete platform tmp file + remote `C:\Windows\Temp\backup_{ts}.vhdx` via WinRM `Remove-Item`
- Return artifact_refs with `disk_list`, `vhdx_filename`, `artifact_uri`, `size_bytes`

**rollback(params, execution_result, connector):**
- Delete S3 artifact: `backend.delete(artifact_uri, cfg)`
- Return `{"rolled_back": True}`

### Smoke phase: `DISK2VHD`

Setup:
1. Verify `disk2vhd.exe` exists in S3 tools prefix; if not, print instructions and skip phase
2. Get-or-create Windows EC2 AMI (AMI cache key: `/nexplane/smoke-amis/windows-2022/base`)
3. Launch `t3.medium` Windows EC2 from AMI, wait for WinRM-ready (~3min for cached AMI)

Smoke:
4. Create CR with inline WinRM creds, `disk_list=["C:"]`
5. Execute, verify `artifact_uri` in S3 with `.vhdx` suffix, verify file size > 0 bytes
6. Rollback, verify S3 object gone

Teardown:
7. Terminate Windows EC2

---

## Strategy 6: `mgn_replication`

**File:** `backend/app/connectors/executors/nexplane_agent/backup_strategies/mgn_replication.py`

**Infra needed:**
- AWS MGN (Application Migration Service) enabled in the target region
- Source EC2 (t3.small, Amazon Linux 2023) with MGN agent installed and initial sync completed (~2h first run)
- AMI cached at `/nexplane/smoke-amis/mgn-source/{hash}` after agent installation

**Initial sync wait:** MGN initial replication takes ~2h on a t3.small with 8 GiB root volume. The smoke must wait for `status == "READY_FOR_TEST"` before proceeding.

### Implementation

**backup(params, asset_ids, connector):**
- `params` keys: `aws_connector_id`, `mgn_source_server_id` (str, the MGN source server ID, e.g. `"s-abcdef1234567890a"`)
- Load AWS creds via `_load_aws_creds`
- `mgn = boto3.client("mgn", **aws_creds)`
- Verify source server is in state `READY_FOR_TEST` or `READY_FOR_CUTOVER`:
  `mgn.describe_source_servers(filters={"sourceServerIDs": [mgn_source_server_id]})`
- Launch test instance: `mgn.launch_test_instances(sourceServerIDs=[mgn_source_server_id])`
  - Returns a `Job` object with `jobID`
- Poll `mgn.describe_jobs(filters={"jobIDs": [job_id]})` until `status == "COMPLETED"` or `"FAILED"` (timeout 1800s, poll every 30s)
- From completed job, extract `participatingServers[0].launchedEc2InstanceID` → the test instance ID
- From test instance, get AMI ID via `ec2.describe_instances(InstanceIds=[test_instance_id])` → `ImageId`
- Return:
```python
{
    "status": "completed",
    "artifact_refs": {
        "capture_strategy": "mgn_replication",
        "restore_strategy": "launch_ami",
        "backup_tier": "machine",
        "captured_at": captured_at,
        "mgn_source_server_id": mgn_source_server_id,
        "launch_job_id": job_id,
        "test_instance_id": test_instance_id,
        "ami_id": ami_id,
        "aws_connector_id": aws_connector_id,
    },
    "_asset_ids": [str(a) for a in asset_ids],
}
```

**rollback(params, execution_result, connector):**
- Extract `test_instance_id`, `ami_id`, `aws_connector_id` from artifact_refs
- `mgn.terminate_target_instances(instanceIDs=[test_instance_id])`
- `ec2.deregister_image(ImageId=ami_id)`
- Return `{"rolled_back": True, "terminated_instance": test_instance_id, "deregistered_ami": ami_id}`

### Smoke phase: `MGN_REPLICATION`

Setup:
1. Enable MGN in region if not already: `mgn.initialize_service()`
2. Get-or-create MGN source EC2: check SSM cache `/nexplane/smoke-amis/mgn-source/al2023-base`
   - If no cache: launch t3.small AL2023 EC2, install MGN agent via user-data, register with MGN using the replication server settings template, wait for `status == "READY_FOR_TEST"` (up to 3h), AMI-cache the source server EC2 state (so next run skips install)
   - **Note**: the MGN source server registration persists in MGN even after the EC2 is AMI-cached — store `mgn_source_server_id` alongside the AMI ID in SSM
3. If cache exists: launch EC2 from cached AMI, verify MGN source server is still registered and in `READY_FOR_TEST` state

Smoke:
4. Create CR with `capture_strategy=mgn_replication`, `mgn_source_server_id` from SSM
5. Execute, verify `test_instance_id` launched, `ami_id` present in artifact_refs
6. Rollback, verify test instance terminated and AMI deregistered

Teardown:
7. Terminate source EC2 (MGN registration persists in AWS — leave it for next run, or call `mgn.delete_source_server` if complete cleanup required; delete for full teardown)
8. Delete MGN replication server (if one was auto-provisioned by MGN)

---

## Test File Structure

All 6 smoke phases go into `backend/tests/smoke/test_backup_scheduler_live.py` as new `run_phase_*` functions following the existing pattern.

Unit tests go into `backend/app/tests/test_backup_architecture.py` as new test classes:
- `TestStorageSyncStrategy` — verify synced_count in result, rollback calls delete_prefix
- `TestLvmSnapshotStrategy` — mock SSH exec_command sequence, verify artifact_uri in result
- `TestNfsFilesStrategy` — same as lvm_snapshot pattern
- `TestManagedDbSnapshotStrategy` — mock boto3 RDS create/describe/delete calls
- `TestDisk2VhdStrategy` — mock WinRM session + boto3, verify vhdx artifact
- `TestMgnReplicationStrategy` — mock MGN launch_test_instances + poll, verify ami_id in result

---

## Implementation Order

1. `storage_sync` — no infra, runs immediately; commit + push + smoke
2. `lvm_snapshot` — provision LVM/NFS EC2 (shared); commit + push + smoke
3. `nfs_files` — same EC2 still running; commit + push + smoke; terminate EC2
4. `managed_db_snapshot` — provision RDS; commit + push + smoke; delete RDS
5. `disk2vhd` — provision Windows EC2; commit + push + smoke; terminate EC2
6. `mgn_replication` — provision MGN source (longest lead time); commit + push + smoke; full teardown

Each strategy is committed to master and smoke-verified before the next begins.
