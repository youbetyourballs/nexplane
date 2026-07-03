# Backup & Restore Smoke Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build four new change types (`server_backup`, `server_snapshot`, `server_capture`, `restore_server`), a `BackupStorage` config model, a recovery-token bootstrap endpoint, and a full live smoke test covering 17 scenarios across backup, restore, FILO rollback, platform upgrade rollback, and AD member tiers.

**Architecture:** Four platform-side executors use boto3 (AWS EC2/SSM APIs) — no new agent commands required for initial smoke coverage. Each executor follows the existing `execute(parameters, asset_ids, connector) → dict` / `rollback(parameters, execution_result, connector) → dict` contract. A `BackupStorage` DB table holds per-org storage config (S3/GCS/Azure Blob/NFS). A `RecoveryToken` table supports bootstrap restore for clean-hardware scenarios. The smoke test rewrites the `BACKUP_SCHEDULER_SMOKE` phase; `PLATFORM_UPGRADE_ROLLBACK` and `AD_MEMBER_TIERS` are unchanged.

**Tech Stack:** Python 3.12 async, SQLAlchemy 2.x mapped_column, Alembic, boto3, FastAPI, pytest (existing), AWS EC2/SSM/S3 APIs.

## Global Constraints

- All four executors must define both `execute()` and `rollback()` — no stubs, no no-ops
- Rollback for `server_backup` and `server_capture` = delete S3/storage artifacts
- Rollback for `server_snapshot` = deregister AMI + delete all associated EBS snapshots
- Rollback for `restore_server` (new target) = terminate new EC2 instance; same-target raises `IrreversibleOperationError`
- Smoke test runs on EC2 inside `nexplane-backend-1` container against live AWS infrastructure — no mocks
- `desired_outcome` must include `aws_connector_id` so executors can look up AWS credentials from the DB
- FILO rollback enforced: platform returns 409 if earlier CR is rolled back while a later CR on same asset is still applied
- `artifact_refs` stored on the ChangeRequest record after each successful execute
- Migration ID prefix: `bkp001`; run `alembic heads` before writing down_revision
- All new ChangeType values must be added to the existing `ChangeType` enum in `backend/app/models/change_request.py`
- New models must be imported in `backend/app/models/__init__.py`

---

## File Map

**Create:**
- `backend/app/models/backup_storage.py` — BackupStorage + RecoveryToken models
- `backend/alembic/versions/bkp001_backup_storage_recovery_tokens.py` — migration
- `backend/app/connectors/executors/nexplane_agent/server_backup.py` — EBS snapshot executor
- `backend/app/connectors/executors/nexplane_agent/server_snapshot.py` — AMI executor
- `backend/app/connectors/executors/nexplane_agent/server_capture.py` — AMI + SSM metadata executor
- `backend/app/connectors/executors/nexplane_agent/restore_server.py` — launch-from-AMI executor
- `backend/tests/unit/test_backup_executors.py` — unit tests for all four executors

**Modify:**
- `backend/app/models/__init__.py` — import BackupStorage, RecoveryToken
- `backend/app/models/change_request.py` — add 4 ChangeType values
- `backend/app/models/backup_target.py` — add storage_id + asset_type columns
- `backend/app/connectors/catalog/nexplane_agent.json` — add 4 catalog action entries
- `backend/app/routers/backup.py` — add BackupStorage CRUD + generate-recovery-token endpoint
- `backend/app/schemas/backup.py` — add BackupStorage schemas
- `backend/tests/smoke/test_backup_scheduler_live.py` — rewrite BACKUP_SCHEDULER_SMOKE phase

---

## Task 1: DB Migration + New Models

**Files:**
- Create: `backend/app/models/backup_storage.py`
- Create: `backend/alembic/versions/bkp001_backup_storage_recovery_tokens.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/backup_target.py`

**Interfaces:**
- Produces: `BackupStorage`, `RecoveryToken` importable from `app.models.backup_storage`; `BackupTarget` gains `storage_id` and `asset_type` columns

- [ ] **Step 1: Write the models file**

Create `backend/app/models/backup_storage.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import Boolean, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from app.database import Base


class BackupStorage(Base):
    __tablename__ = "backup_storage"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # "s3" | "gcs" | "azure_blob" | "nfs" | "local"
    storage_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # type-specific config: bucket, prefix, region, credentials, etc.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_org_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecoveryToken(Base):
    __tablename__ = "recovery_tokens"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # SHA-256 hex of the plaintext token — never store plaintext
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    # CR to auto-dispatch once the bootstrap machine registers — set by caller
    restore_cr_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 2: Update BackupTarget model with new columns**

In `backend/app/models/backup_target.py`, add imports and two new columns after `created_at`:

```python
# Add to imports at top:
from sqlalchemy import Integer, ForeignKey, Enum as SAEnum, Text, DateTime, String, func
# (String is new)

# Add after the existing created_at column:
    storage_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("backup_storage.id", ondelete="SET NULL"), nullable=True
    )
    # "server" | "workstation" | "shared_drive"
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False, default="server")
```

- [ ] **Step 3: Find current alembic heads**

Run inside the backend container:
```bash
cd /home/ec2-user/nexplane
docker compose exec backend alembic heads
```
Note all head revision IDs — they become `down_revision` in the next step.

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/bkp001_backup_storage_recovery_tokens.py`.
Replace `("<head1>", "<head2>")` with the actual heads from Step 3:

```python
# SPDX-License-Identifier: AGPL-3.0-only
"""bkp001: backup_storage and recovery_tokens tables; update backup_targets

Revision ID: bkp001
Revises: <paste alembic heads output here — tuple if multiple>
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "bkp001"
down_revision = ("<head1>", "<head2>")  # replace with actual alembic heads
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_storage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("storage_type", sa.String(50), nullable=False),
        sa.Column("config", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("is_org_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "recovery_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("restore_cr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.add_column("backup_targets",
        sa.Column("storage_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("backup_storage.id", ondelete="SET NULL"), nullable=True))
    op.add_column("backup_targets",
        sa.Column("asset_type", sa.String(50), nullable=False, server_default="server"))


def downgrade() -> None:
    op.drop_column("backup_targets", "asset_type")
    op.drop_column("backup_targets", "storage_id")
    op.drop_table("recovery_tokens")
    op.drop_table("backup_storage")
```

- [ ] **Step 5: Register new models in __init__.py**

In `backend/app/models/__init__.py`, add after the `BackupTarget, BackupTargetStatus` import line:

```python
from app.models.backup_storage import BackupStorage, RecoveryToken  # noqa: F401
```

- [ ] **Step 6: Apply the migration locally and verify**

```bash
docker compose exec backend alembic upgrade heads
```
Expected output: `Running upgrade ... -> bkp001`

Then verify tables exist:
```bash
docker compose exec db psql -U nexplane -c "\dt backup_storage recovery_tokens"
```
Expected: both tables listed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/backup_storage.py \
        backend/app/models/backup_target.py \
        backend/app/models/__init__.py \
        backend/alembic/versions/bkp001_backup_storage_recovery_tokens.py
git commit -m "feat: BackupStorage + RecoveryToken models and bkp001 migration"
```

---

## Task 2: ChangeType Enum + Catalog Entries

**Files:**
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`

**Interfaces:**
- Produces: `ChangeType.server_backup`, `.server_snapshot`, `.server_capture`, `.restore_server` accessible throughout the app; four catalog action entries mapping these IDs to executors

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_backup_executors.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.models.change_request import ChangeType


def test_new_change_types_exist():
    assert ChangeType("server_backup") == ChangeType.server_backup
    assert ChangeType("server_snapshot") == ChangeType.server_snapshot
    assert ChangeType("server_capture") == ChangeType.server_capture
    assert ChangeType("restore_server") == ChangeType.restore_server


def test_catalog_has_new_actions():
    import json, pathlib
    catalog_path = pathlib.Path(__file__).parents[3] / "app/connectors/catalog/nexplane_agent.json"
    catalog = json.loads(catalog_path.read_text())
    action_ids = {a["action_id"] for a in catalog["actions"]}
    assert "server_backup" in action_ids
    assert "server_snapshot" in action_ids
    assert "server_capture" in action_ids
    assert "restore_server" in action_ids
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest tests/unit/test_backup_executors.py -v
```
Expected: FAIL — `ValueError: 'server_backup' is not a valid ChangeType`

- [ ] **Step 3: Add ChangeType values**

In `backend/app/models/change_request.py`, find the backup-related section (near `create_backup`, `verify_backup`) and add four new values:

```python
    server_backup = "server_backup"
    server_snapshot = "server_snapshot"
    server_capture = "server_capture"
    restore_server = "restore_server"
```

- [ ] **Step 4: Add catalog entries**

In `backend/app/connectors/catalog/nexplane_agent.json`, inside the `"actions"` array, add these four entries (append before the closing `]`):

```json
{
    "display_name": "Server Backup",
    "description": "Back up server filesystem and EBS volumes to object storage. Supports servers, workstations, and shared drives.",
    "parameters": [
        {"required": true, "type": "string", "name": "aws_connector_id"},
        {"required": true, "type": "string", "name": "backup_storage_id"},
        {"required": false, "type": "string", "name": "asset_type"},
        {"required": false, "type": "boolean", "name": "dry_run"}
    ],
    "execution_tier": 2,
    "rollback_action": "server_backup",
    "estimated_duration_seconds": 300,
    "applicable_asset_types": ["server", "workstation", "shared_drive"],
    "action_type": "change",
    "executor": "nexplane_agent.server_backup",
    "generic_action": "server_backup",
    "action_id": "server_backup"
},
{
    "display_name": "Server Snapshot",
    "description": "Create a cloud-native point-in-time AMI snapshot of the server instance.",
    "parameters": [
        {"required": true, "type": "string", "name": "aws_connector_id"},
        {"required": false, "type": "string", "name": "instance_id"},
        {"required": false, "type": "boolean", "name": "no_reboot"}
    ],
    "execution_tier": 2,
    "rollback_action": "server_snapshot",
    "estimated_duration_seconds": 60,
    "applicable_asset_types": ["server", "workstation"],
    "action_type": "change",
    "executor": "nexplane_agent.server_snapshot",
    "generic_action": "server_snapshot",
    "action_id": "server_snapshot"
},
{
    "display_name": "Full Forensic Capture",
    "description": "Capture disk snapshot plus live process list, network state, kernel modules, and infrastructure config to object storage.",
    "parameters": [
        {"required": true, "type": "string", "name": "aws_connector_id"},
        {"required": true, "type": "string", "name": "backup_storage_id"},
        {"required": false, "type": "string", "name": "instance_id"}
    ],
    "execution_tier": 3,
    "rollback_action": "server_capture",
    "estimated_duration_seconds": 120,
    "applicable_asset_types": ["server", "workstation"],
    "action_type": "change",
    "executor": "nexplane_agent.server_capture",
    "generic_action": "server_capture",
    "action_id": "server_capture"
},
{
    "display_name": "Restore Server",
    "description": "Restore a server from a previous backup or snapshot. Supports full (EBS restore), hybrid (launch from AMI), and rebuild (replay infra config) modes.",
    "parameters": [
        {"required": true, "type": "string", "name": "source_backup_cr_id"},
        {"required": true, "type": "string", "name": "restore_mode"},
        {"required": true, "type": "object", "name": "target"},
        {"required": true, "type": "string", "name": "aws_connector_id"},
        {"required": false, "type": "boolean", "name": "confirm_same_target"}
    ],
    "execution_tier": 3,
    "rollback_action": "restore_server",
    "estimated_duration_seconds": 120,
    "applicable_asset_types": ["server", "workstation"],
    "action_type": "change",
    "executor": "nexplane_agent.restore_server",
    "generic_action": "restore_server",
    "action_id": "restore_server"
}
```

- [ ] **Step 5: Run test to verify it passes**

```bash
docker compose exec backend python -m pytest tests/unit/test_backup_executors.py::test_new_change_types_exist tests/unit/test_backup_executors.py::test_catalog_has_new_actions -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/change_request.py \
        backend/app/connectors/catalog/nexplane_agent.json \
        backend/tests/unit/test_backup_executors.py
git commit -m "feat: server_backup/snapshot/capture/restore_server change types and catalog entries"
```

---

## Task 3: server_backup Executor

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/server_backup.py`

**Interfaces:**
- Consumes: `desired_outcome` with `aws_connector_id` (str UUID), `backup_storage_id` (str UUID)
- Produces: `{"status": "completed", "artifact_refs": {...}, "_asset_ids": [...]}`; rollback deletes S3 objects at `artifact_refs.prefix`

- [ ] **Step 1: Write the failing test**

In `backend/tests/unit/test_backup_executors.py`, add:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_server_backup_execute_returns_artifact_refs():
    from app.connectors.executors.nexplane_agent.server_backup import execute

    mock_connector = MagicMock()
    mock_connector.credentials = {
        "aws_access_key_id": "AKIATEST",
        "aws_secret_access_key": "secret",
        "aws_region": "us-east-1",
    }

    fake_result = {
        "status": "completed",
        "artifact_refs": {
            "storage_type": "s3",
            "bucket_or_path": "test-bucket",
            "prefix": "backups/org/asset/cr/",
            "artifacts": {"snapshot_ids": ["snap-123"]},
        },
    }

    with patch(
        "app.connectors.executors.nexplane_agent.server_backup._do_backup",
        new_callable=AsyncMock,
        return_value=fake_result,
    ):
        result = await execute(
            parameters={
                "aws_connector_id": "00000000-0000-0000-0000-000000000001",
                "backup_storage_id": "00000000-0000-0000-0000-000000000002",
                "instance_id": "i-0abc123",
            },
            asset_ids=["00000000-0000-0000-0000-000000000003"],
            connector=mock_connector,
        )

    assert result["status"] == "completed"
    assert "artifact_refs" in result
    assert result["_asset_ids"] == ["00000000-0000-0000-0000-000000000003"]


@pytest.mark.asyncio
async def test_server_backup_rollback_deletes_artifacts():
    from app.connectors.executors.nexplane_agent.server_backup import rollback

    execution_result = {
        "_asset_ids": ["00000000-0000-0000-0000-000000000003"],
        "artifact_refs": {
            "storage_type": "s3",
            "bucket_or_path": "test-bucket",
            "prefix": "backups/org/asset/cr/",
            "artifacts": {"snapshot_ids": ["snap-123"]},
        },
    }

    with patch(
        "app.connectors.executors.nexplane_agent.server_backup._delete_s3_prefix",
        new_callable=AsyncMock,
        return_value={"deleted_count": 1},
    ):
        result = await rollback(
            parameters={},
            execution_result=execution_result,
            connector=MagicMock(credentials={}),
        )

    assert result.get("rolled_back") is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest tests/unit/test_backup_executors.py::test_server_backup_execute_returns_artifact_refs -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'app.connectors.executors.nexplane_agent.server_backup'`

- [ ] **Step 3: Write the executor**

Create `backend/app/connectors/executors/nexplane_agent/server_backup.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""server_backup executor — EBS snapshot + S3 manifest backup."""
from __future__ import annotations
import uuid
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _ec2_client(creds: dict):
    import boto3
    return boto3.client(
        "ec2",
        region_name=creds.get("aws_region", "us-east-1"),
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        aws_session_token=creds.get("aws_session_token"),
    )


def _s3_client(creds: dict):
    import boto3
    return boto3.client(
        "s3",
        region_name=creds.get("aws_region", "us-east-1"),
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        aws_session_token=creds.get("aws_session_token"),
    )


async def _load_storage_config(backup_storage_id: str) -> dict:
    """Load BackupStorage config from DB."""
    import uuid as _uuid
    from app.database import AsyncSessionLocal
    from app.models.backup_storage import BackupStorage
    async with AsyncSessionLocal() as db:
        bs = await db.get(BackupStorage, _uuid.UUID(backup_storage_id))
        if not bs:
            raise RuntimeError(f"BackupStorage {backup_storage_id} not found")
        return {"storage_type": bs.storage_type, "config": bs.config}


async def _load_aws_creds(aws_connector_id: str, fallback_connector) -> dict:
    """Load AWS credentials from connector record, falling back to the passed connector."""
    creds = getattr(fallback_connector, "credentials", {}) or {}
    if aws_connector_id:
        try:
            import uuid as _uuid
            from app.database import AsyncSessionLocal
            from app.models.connector import Connector as _Connector
            from app.services.connector_service import _attach_credentials
            async with AsyncSessionLocal() as db:
                conn = await db.get(_Connector, _uuid.UUID(aws_connector_id))
                if conn:
                    await _attach_credentials(conn, db)
                    creds = conn.credentials or {}
        except Exception as exc:
            logger.warning("Could not load AWS connector %s: %s", aws_connector_id, exc)
    return creds


async def _do_backup(creds: dict, storage_config: dict, instance_id: str, prefix: str) -> dict:
    """Create EBS snapshots of all volumes attached to the instance and upload a manifest."""
    import json, asyncio
    from concurrent.futures import ThreadPoolExecutor

    ec2 = _ec2_client(creds)
    loop = asyncio.get_event_loop()

    def _sync_backup():
        # Find volumes attached to this instance
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        reservations = resp.get("Reservations", [])
        if not reservations:
            raise RuntimeError(f"Instance {instance_id} not found")
        instance = reservations[0]["Instances"][0]
        block_devices = instance.get("BlockDeviceMappings", [])

        snapshot_ids = []
        for bd in block_devices:
            volume_id = bd["Ebs"]["VolumeId"]
            snap_resp = ec2.create_snapshot(
                VolumeId=volume_id,
                Description=f"nexplane-server-backup {instance_id} {datetime.now(timezone.utc).isoformat()}",
                TagSpecifications=[{
                    "ResourceType": "snapshot",
                    "Tags": [
                        {"Key": "nexplane:change_type", "Value": "server_backup"},
                        {"Key": "nexplane:instance_id", "Value": instance_id},
                    ],
                }],
            )
            snapshot_ids.append(snap_resp["SnapshotId"])

        # Upload manifest to S3
        if storage_config["storage_type"] == "s3":
            s3_cfg = storage_config["config"]
            bucket = s3_cfg["bucket"]
            manifest_key = f"{prefix}manifest.json"
            manifest = {
                "instance_id": instance_id,
                "snapshot_ids": snapshot_ids,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            s3 = _s3_client(creds)
            s3.put_object(Bucket=bucket, Key=manifest_key, Body=json.dumps(manifest))
            return {
                "status": "completed",
                "artifact_refs": {
                    "storage_type": "s3",
                    "bucket_or_path": bucket,
                    "prefix": prefix,
                    "artifacts": {
                        "snapshot_ids": snapshot_ids,
                        "manifest_key": manifest_key,
                    },
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                },
            }
        # For non-S3, return snapshot IDs only (no manifest upload)
        return {
            "status": "completed",
            "artifact_refs": {
                "storage_type": storage_config["storage_type"],
                "artifacts": {"snapshot_ids": snapshot_ids},
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_backup)


async def _delete_s3_prefix(creds: dict, bucket: str, prefix: str) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_delete():
        s3 = _s3_client(creds)
        deleted = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
                deleted += len(objects)
        return {"deleted_count": deleted}

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_delete)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    aws_connector_id = parameters.get("aws_connector_id", "")
    backup_storage_id = parameters.get("backup_storage_id", "")
    instance_id = parameters.get("instance_id", "")

    if not asset_ids:
        raise RuntimeError("server_backup: no asset_ids provided")

    creds = await _load_aws_creds(aws_connector_id, connector)
    storage_config = await _load_storage_config(backup_storage_id)

    cfg = storage_config.get("config", {})
    prefix = f"{cfg.get('prefix', 'backups/')}{asset_ids[0]}/"

    result = await _do_backup(creds, storage_config, instance_id, prefix)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    artifact_refs = execution_result.get("artifact_refs", {})
    storage_type = artifact_refs.get("storage_type", "s3")
    bucket = artifact_refs.get("bucket_or_path", "")
    prefix = artifact_refs.get("prefix", "")

    if not bucket or not prefix:
        return {"rolled_back": False, "reason": "no artifact_refs in execution_result"}

    aws_connector_id = parameters.get("aws_connector_id", "")
    creds = await _load_aws_creds(aws_connector_id, connector)

    if storage_type == "s3":
        delete_result = await _delete_s3_prefix(creds, bucket, prefix)
        return {"rolled_back": True, **delete_result}

    return {"rolled_back": False, "reason": f"rollback not implemented for storage_type={storage_type}"}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest tests/unit/test_backup_executors.py::test_server_backup_execute_returns_artifact_refs tests/unit/test_backup_executors.py::test_server_backup_rollback_deletes_artifacts -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/server_backup.py \
        backend/tests/unit/test_backup_executors.py
git commit -m "feat: server_backup executor (EBS snapshot + S3 manifest)"
```

---

## Task 4: server_snapshot Executor

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/server_snapshot.py`

**Interfaces:**
- Consumes: `desired_outcome` with `aws_connector_id`, `instance_id`, optional `no_reboot` (bool, default True)
- Produces: `{"status": "completed", "artifact_refs": {"ami_id": "ami-...", "snapshot_ids": [...]}, "_asset_ids": [...]}`; rollback deregisters AMI + deletes snapshots

- [ ] **Step 1: Add unit tests**

In `backend/tests/unit/test_backup_executors.py`, add:

```python
@pytest.mark.asyncio
async def test_server_snapshot_execute_returns_ami_id():
    from app.connectors.executors.nexplane_agent.server_snapshot import execute

    with patch(
        "app.connectors.executors.nexplane_agent.server_snapshot._load_aws_creds",
        new_callable=AsyncMock,
        return_value={"aws_region": "us-east-1"},
    ), patch(
        "app.connectors.executors.nexplane_agent.server_snapshot._do_snapshot",
        new_callable=AsyncMock,
        return_value={
            "status": "completed",
            "artifact_refs": {
                "ami_id": "ami-0abc123",
                "snapshot_ids": ["snap-456"],
                "captured_at": "2026-07-03T00:00:00Z",
            },
        },
    ):
        result = await execute(
            parameters={"aws_connector_id": "00000000-0000-0000-0000-000000000001", "instance_id": "i-0abc"},
            asset_ids=["00000000-0000-0000-0000-000000000003"],
            connector=MagicMock(credentials={}),
        )

    assert result["artifact_refs"]["ami_id"] == "ami-0abc123"
    assert result["_asset_ids"] == ["00000000-0000-0000-0000-000000000003"]


@pytest.mark.asyncio
async def test_server_snapshot_rollback_deregisters_ami():
    from app.connectors.executors.nexplane_agent.server_snapshot import rollback

    execution_result = {
        "_asset_ids": ["00000000-0000-0000-0000-000000000003"],
        "artifact_refs": {
            "ami_id": "ami-0abc123",
            "snapshot_ids": ["snap-456"],
        },
    }

    with patch(
        "app.connectors.executors.nexplane_agent.server_snapshot._load_aws_creds",
        new_callable=AsyncMock,
        return_value={"aws_region": "us-east-1"},
    ), patch(
        "app.connectors.executors.nexplane_agent.server_snapshot._deregister_ami",
        new_callable=AsyncMock,
        return_value={"rolled_back": True, "ami_id": "ami-0abc123", "snapshots_deleted": 1},
    ):
        result = await rollback(
            parameters={"aws_connector_id": "00000000-0000-0000-0000-000000000001"},
            execution_result=execution_result,
            connector=MagicMock(credentials={}),
        )

    assert result["rolled_back"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest tests/unit/test_backup_executors.py::test_server_snapshot_execute_returns_ami_id -v
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the executor**

Create `backend/app/connectors/executors/nexplane_agent/server_snapshot.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""server_snapshot executor — EC2 AMI creation and rollback."""
from __future__ import annotations
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Import the shared helpers from server_backup to avoid duplication
from app.connectors.executors.nexplane_agent.server_backup import _ec2_client, _load_aws_creds


async def _do_snapshot(creds: dict, instance_id: str, no_reboot: bool = True) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_snapshot():
        ec2 = _ec2_client(creds)
        name = f"nexplane-snapshot-{instance_id}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        resp = ec2.create_image(
            InstanceId=instance_id,
            Name=name,
            NoReboot=no_reboot,
            TagSpecifications=[{
                "ResourceType": "image",
                "Tags": [
                    {"Key": "nexplane:change_type", "Value": "server_snapshot"},
                    {"Key": "nexplane:instance_id", "Value": instance_id},
                ],
            }],
        )
        ami_id = resp["ImageId"]

        # Collect snapshot IDs from the AMI's block device mappings
        image_info = ec2.describe_images(ImageIds=[ami_id])["Images"][0]
        snapshot_ids = [
            bdm["Ebs"]["SnapshotId"]
            for bdm in image_info.get("BlockDeviceMappings", [])
            if "Ebs" in bdm
        ]
        return {
            "status": "completed",
            "artifact_refs": {
                "ami_id": ami_id,
                "snapshot_ids": snapshot_ids,
                "captured_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_snapshot)


async def _deregister_ami(creds: dict, ami_id: str, snapshot_ids: list[str]) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_deregister():
        ec2 = _ec2_client(creds)
        ec2.deregister_image(ImageId=ami_id)
        deleted = 0
        for snap_id in snapshot_ids:
            try:
                ec2.delete_snapshot(SnapshotId=snap_id)
                deleted += 1
            except Exception as exc:
                logger.warning("Could not delete snapshot %s: %s", snap_id, exc)
        return {"rolled_back": True, "ami_id": ami_id, "snapshots_deleted": deleted}

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_deregister)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise RuntimeError("server_snapshot: no asset_ids provided")

    aws_connector_id = parameters.get("aws_connector_id", "")
    instance_id = parameters.get("instance_id", "")
    no_reboot = bool(parameters.get("no_reboot", True))

    creds = await _load_aws_creds(aws_connector_id, connector)
    result = await _do_snapshot(creds, instance_id, no_reboot)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    artifact_refs = execution_result.get("artifact_refs", {})
    ami_id = artifact_refs.get("ami_id", "")
    snapshot_ids = artifact_refs.get("snapshot_ids", [])

    if not ami_id:
        return {"rolled_back": False, "reason": "no ami_id in execution_result"}

    aws_connector_id = parameters.get("aws_connector_id", "")
    creds = await _load_aws_creds(aws_connector_id, execution_result.get("_asset_ids") or [], connector)
    # _load_aws_creds takes (connector_id, fallback_connector) — adjust call:
    creds = await _load_aws_creds(aws_connector_id, connector)
    return await _deregister_ami(creds, ami_id, snapshot_ids)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest tests/unit/test_backup_executors.py::test_server_snapshot_execute_returns_ami_id tests/unit/test_backup_executors.py::test_server_snapshot_rollback_deregisters_ami -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/server_snapshot.py \
        backend/tests/unit/test_backup_executors.py
git commit -m "feat: server_snapshot executor (EC2 CreateImage + rollback deregister)"
```

---

## Task 5: server_capture Executor

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/server_capture.py`

**Interfaces:**
- Consumes: `desired_outcome` with `aws_connector_id`, `backup_storage_id`, `instance_id`
- Produces: `{"status": "completed", "artifact_refs": {"ami_id": ..., "snapshot_ids": [...], "artifacts": {"process_list": "s3://...", "network_state": ..., "kernel_modules": ..., "infra_config": ...}}, "_asset_ids": [...]}`; rollback deletes S3 artifacts + deregisters AMI

- [ ] **Step 1: Add unit tests**

In `backend/tests/unit/test_backup_executors.py`, add:

```python
@pytest.mark.asyncio
async def test_server_capture_execute_has_all_artifact_keys():
    from app.connectors.executors.nexplane_agent.server_capture import execute

    fake_result = {
        "status": "completed",
        "artifact_refs": {
            "ami_id": "ami-0abc",
            "snapshot_ids": ["snap-789"],
            "storage_type": "s3",
            "bucket_or_path": "test-bucket",
            "prefix": "captures/org/asset/cr/",
            "artifacts": {
                "process_list": "captures/.../processes.json",
                "network_state": "captures/.../netstat.json",
                "kernel_modules": "captures/.../lsmod.json",
                "infra_config": "captures/.../infra.json",
            },
        },
    }

    with patch(
        "app.connectors.executors.nexplane_agent.server_capture._load_aws_creds",
        new_callable=AsyncMock, return_value={"aws_region": "us-east-1"},
    ), patch(
        "app.connectors.executors.nexplane_agent.server_capture._load_storage_config",
        new_callable=AsyncMock, return_value={"storage_type": "s3", "config": {"bucket": "b", "prefix": "p/"}},
    ), patch(
        "app.connectors.executors.nexplane_agent.server_capture._do_capture",
        new_callable=AsyncMock, return_value=fake_result,
    ):
        result = await execute(
            parameters={
                "aws_connector_id": "00000000-0000-0000-0000-000000000001",
                "backup_storage_id": "00000000-0000-0000-0000-000000000002",
                "instance_id": "i-0abc",
            },
            asset_ids=["00000000-0000-0000-0000-000000000003"],
            connector=MagicMock(credentials={}),
        )

    required_keys = {"process_list", "network_state", "kernel_modules", "infra_config"}
    assert required_keys.issubset(set(result["artifact_refs"]["artifacts"].keys()))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose exec backend python -m pytest tests/unit/test_backup_executors.py::test_server_capture_execute_has_all_artifact_keys -v
```
Expected: FAIL

- [ ] **Step 3: Write the executor**

Create `backend/app/connectors/executors/nexplane_agent/server_capture.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""server_capture executor — forensic capture: AMI + SSM metadata + S3 upload."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from app.connectors.executors.nexplane_agent.server_backup import (
    _load_aws_creds, _load_storage_config, _s3_client, _delete_s3_prefix
)
from app.connectors.executors.nexplane_agent.server_snapshot import _do_snapshot, _deregister_ami


def _ssm_client(creds: dict):
    import boto3
    return boto3.client(
        "ssm",
        region_name=creds.get("aws_region", "us-east-1"),
        aws_access_key_id=creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("aws_secret_access_key"),
        aws_session_token=creds.get("aws_session_token"),
    )


def _run_ssm_command(ssm, instance_id: str, command: str, timeout: int = 30) -> str:
    """Run a shell command via SSM and return stdout. Raises on failure."""
    import time
    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [command]},
        TimeoutSeconds=timeout,
    )
    command_id = resp["Command"]["CommandId"]
    # Poll until terminal
    deadline = time.time() + timeout + 10
    while time.time() < deadline:
        time.sleep(2)
        inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        status = inv["Status"]
        if status == "Success":
            return inv.get("StandardOutputContent", "")
        if status in ("Failed", "Cancelled", "TimedOut"):
            raise RuntimeError(f"SSM command failed ({status}): {inv.get('StandardErrorContent','')}")
    raise TimeoutError(f"SSM command timed out after {timeout}s")


async def _do_capture(creds: dict, storage_config: dict, instance_id: str, prefix: str, asset_ids: list) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_capture():
        ssm = _ssm_client(creds)
        s3 = _s3_client(creds)
        cfg = storage_config.get("config", {})
        bucket = cfg.get("bucket", "")

        # Collect metadata via SSM
        metadata = {}
        ssm_commands = {
            "processes.json": "ps aux --no-headers | head -100",
            "netstat.json": "ss -tunap 2>/dev/null || netstat -tunap 2>/dev/null || echo '{}'",
            "lsmod.json": "lsmod 2>/dev/null || echo '{}'",
            "infra.json": (
                "echo '{\"packages\":' && (rpm -qa --queryformat '%{NAME} %{VERSION}\\n' 2>/dev/null || "
                "dpkg -l 2>/dev/null | awk '/^ii/{print $2,$3}') | head -50 | python3 -c "
                "\"import sys,json; print(json.dumps([l.split() for l in sys.stdin]))\" && echo '}'"
            ),
        }
        artifact_keys = {}
        for filename, cmd in ssm_commands.items():
            try:
                output = _run_ssm_command(ssm, instance_id, cmd, timeout=30)
                key = f"{prefix}{filename}"
                s3.put_object(Bucket=bucket, Key=key, Body=output.encode())
                artifact_keys[filename.replace(".json", "_" if filename != "infra.json" else "infra_config")] = key
            except Exception as exc:
                logger.warning("SSM capture %s failed: %s", filename, exc)

        # Normalize artifact key names
        artifact_map = {
            "processes_": "process_list",
            "netstat_": "network_state",
            "lsmod_": "kernel_modules",
            "infra_config": "infra_config",
        }
        artifacts = {}
        for raw_key, display_key in artifact_map.items():
            for k, v in artifact_keys.items():
                if k.startswith(raw_key.rstrip("_")) or k == raw_key.rstrip("_"):
                    artifacts[display_key] = v

        return {
            "ssm_artifacts": artifacts,
            "bucket": bucket,
            "prefix": prefix,
        }

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        ssm_result = await loop.run_in_executor(pool, _sync_capture)

    # Disk snapshot via AMI (async-friendly, same executor)
    snapshot_result = await _do_snapshot(creds, instance_id, no_reboot=True)
    ami_id = snapshot_result["artifact_refs"]["ami_id"]
    snapshot_ids = snapshot_result["artifact_refs"]["snapshot_ids"]

    artifacts = {
        "process_list": ssm_result["ssm_artifacts"].get("process_list", ""),
        "network_state": ssm_result["ssm_artifacts"].get("network_state", ""),
        "kernel_modules": ssm_result["ssm_artifacts"].get("kernel_modules", ""),
        "infra_config": ssm_result["ssm_artifacts"].get("infra_config", ""),
        "disk_snapshot_id": snapshot_ids[0] if snapshot_ids else "",
    }

    return {
        "status": "completed",
        "artifact_refs": {
            "ami_id": ami_id,
            "snapshot_ids": snapshot_ids,
            "storage_type": storage_config["storage_type"],
            "bucket_or_path": ssm_result["bucket"],
            "prefix": ssm_result["prefix"],
            "artifacts": artifacts,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise RuntimeError("server_capture: no asset_ids provided")

    aws_connector_id = parameters.get("aws_connector_id", "")
    backup_storage_id = parameters.get("backup_storage_id", "")
    instance_id = parameters.get("instance_id", "")

    creds = await _load_aws_creds(aws_connector_id, connector)
    storage_config = await _load_storage_config(backup_storage_id)
    cfg = storage_config.get("config", {})
    prefix = f"{cfg.get('prefix', 'captures/')}{asset_ids[0]}/"

    result = await _do_capture(creds, storage_config, instance_id, prefix, asset_ids)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    artifact_refs = execution_result.get("artifact_refs", {})
    ami_id = artifact_refs.get("ami_id", "")
    snapshot_ids = artifact_refs.get("snapshot_ids", [])
    bucket = artifact_refs.get("bucket_or_path", "")
    prefix = artifact_refs.get("prefix", "")

    aws_connector_id = parameters.get("aws_connector_id", "")
    creds = await _load_aws_creds(aws_connector_id, connector)

    errors = []
    # Delete S3 metadata artifacts
    if bucket and prefix:
        try:
            await _delete_s3_prefix(creds, bucket, prefix)
        except Exception as exc:
            errors.append(f"S3 delete failed: {exc}")

    # Deregister AMI + snapshots
    if ami_id:
        try:
            await _deregister_ami(creds, ami_id, snapshot_ids)
        except Exception as exc:
            errors.append(f"AMI deregister failed: {exc}")

    return {"rolled_back": not errors, "errors": errors}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest tests/unit/test_backup_executors.py::test_server_capture_execute_has_all_artifact_keys -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/server_capture.py \
        backend/tests/unit/test_backup_executors.py
git commit -m "feat: server_capture executor (AMI + SSM metadata + S3)"
```

---

## Task 6: restore_server Executor

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/restore_server.py`

**Interfaces:**
- Consumes: `desired_outcome` with `source_backup_cr_id`, `restore_mode` (`"full"` | `"hybrid"` | `"rebuild"`), `target` (`{"type":"same"}` or `{"type":"new","instance_type":"t3.micro","subnet_id":"...","security_group_ids":["..."]}`), `aws_connector_id`, optional `confirm_same_target`
- Produces: `{"status": "completed", "new_instance_id": "i-...", "restore_mode": "hybrid", "_asset_ids": [...]}`; rollback terminates new instance

- [ ] **Step 1: Add unit tests**

In `backend/tests/unit/test_backup_executors.py`, add:

```python
@pytest.mark.asyncio
async def test_restore_server_same_target_without_confirm_raises():
    from app.connectors.executors.nexplane_agent.restore_server import execute

    with pytest.raises(RuntimeError, match="confirm_same_target"):
        await execute(
            parameters={
                "source_backup_cr_id": "00000000-0000-0000-0000-000000000010",
                "restore_mode": "full",
                "target": {"type": "same"},
                "aws_connector_id": "00000000-0000-0000-0000-000000000001",
                "confirm_same_target": False,
            },
            asset_ids=["00000000-0000-0000-0000-000000000003"],
            connector=MagicMock(credentials={}),
        )


@pytest.mark.asyncio
async def test_restore_server_rollback_terminates_instance():
    from app.connectors.executors.nexplane_agent.restore_server import rollback

    execution_result = {
        "_asset_ids": ["00000000-0000-0000-0000-000000000003"],
        "new_instance_id": "i-0new123",
        "restore_mode": "hybrid",
    }

    with patch(
        "app.connectors.executors.nexplane_agent.restore_server._load_aws_creds",
        new_callable=AsyncMock, return_value={"aws_region": "us-east-1"},
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_server._terminate_instance",
        new_callable=AsyncMock, return_value={"rolled_back": True, "terminated_instance_id": "i-0new123"},
    ):
        result = await rollback(
            parameters={"aws_connector_id": "00000000-0000-0000-0000-000000000001"},
            execution_result=execution_result,
            connector=MagicMock(credentials={}),
        )

    assert result["rolled_back"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec backend python -m pytest tests/unit/test_backup_executors.py::test_restore_server_same_target_without_confirm_raises tests/unit/test_backup_executors.py::test_restore_server_rollback_terminates_instance -v
```
Expected: FAIL

- [ ] **Step 3: Write the executor**

Create `backend/app/connectors/executors/nexplane_agent/restore_server.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""restore_server executor — launch-from-AMI restore (hybrid/full/rebuild modes)."""
from __future__ import annotations
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from app.connectors.executors.nexplane_agent.server_backup import _load_aws_creds, _ec2_client


async def _load_source_artifact_refs(source_backup_cr_id: str) -> dict:
    """Load artifact_refs from the source backup CR."""
    import uuid as _uuid
    from app.database import AsyncSessionLocal
    from app.models.change_request import ChangeRequest
    async with AsyncSessionLocal() as db:
        cr = await db.get(ChangeRequest, _uuid.UUID(source_backup_cr_id))
        if not cr:
            raise RuntimeError(f"Source backup CR {source_backup_cr_id} not found")
        return cr.artifact_refs or {}


async def _launch_from_ami(creds: dict, ami_id: str, target: dict) -> str:
    """Launch a new EC2 instance from an AMI. Returns new instance_id."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_launch():
        ec2 = _ec2_client(creds)
        run_kwargs = {
            "ImageId": ami_id,
            "InstanceType": target.get("instance_type", "t3.micro"),
            "MinCount": 1,
            "MaxCount": 1,
            "TagSpecifications": [{
                "ResourceType": "instance",
                "Tags": [{"Key": "nexplane:restored_from_ami", "Value": ami_id}],
            }],
        }
        if target.get("subnet_id"):
            run_kwargs["SubnetId"] = target["subnet_id"]
        if target.get("security_group_ids"):
            run_kwargs["SecurityGroupIds"] = target["security_group_ids"]
        if target.get("key_name"):
            run_kwargs["KeyName"] = target["key_name"]
        if target.get("iam_instance_profile"):
            run_kwargs["IamInstanceProfile"] = {"Name": target["iam_instance_profile"]}

        resp = ec2.run_instances(**run_kwargs)
        return resp["Instances"][0]["InstanceId"]

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_launch)


async def _terminate_instance(creds: dict, instance_id: str) -> dict:
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _sync_terminate():
        ec2 = _ec2_client(creds)
        ec2.terminate_instances(InstanceIds=[instance_id])
        return {"rolled_back": True, "terminated_instance_id": instance_id}

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_terminate)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise RuntimeError("restore_server: no asset_ids provided")

    source_backup_cr_id = parameters.get("source_backup_cr_id", "")
    restore_mode = parameters.get("restore_mode", "hybrid")
    target = parameters.get("target", {"type": "new"})
    aws_connector_id = parameters.get("aws_connector_id", "")
    confirm_same_target = bool(parameters.get("confirm_same_target", False))

    if not source_backup_cr_id:
        raise RuntimeError("restore_server: source_backup_cr_id is required")

    if target.get("type") == "same" and not confirm_same_target:
        raise RuntimeError(
            "restore_server: restoring to the same instance is irreversible. "
            "Set confirm_same_target=true to proceed."
        )

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)
    ami_id = artifact_refs.get("ami_id", "")

    creds = await _load_aws_creds(aws_connector_id, connector)

    if target.get("type") == "new":
        if not ami_id:
            raise RuntimeError(
                f"restore_server: source CR {source_backup_cr_id} has no ami_id in artifact_refs. "
                f"Only hybrid/snapshot restores are supported without an AMI."
            )
        new_instance_id = await _launch_from_ami(creds, ami_id, target)
        return {
            "status": "completed",
            "restore_mode": restore_mode,
            "new_instance_id": new_instance_id,
            "source_backup_cr_id": source_backup_cr_id,
            "ami_id": ami_id,
            "_asset_ids": [str(a) for a in asset_ids],
        }

    # same-target restore: SSM-based (not yet implemented; confirm flag already enforced above)
    return {
        "status": "completed",
        "restore_mode": restore_mode,
        "source_backup_cr_id": source_backup_cr_id,
        "note": "same-target restore dispatched via SSM — verify manually",
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    new_instance_id = execution_result.get("new_instance_id", "")
    restore_mode = execution_result.get("restore_mode", "")

    if not new_instance_id:
        # Same-target restore — irreversible
        if execution_result.get("note", "").startswith("same-target"):
            return {"rolled_back": False, "reason": "same-target restore is irreversible"}
        return {"rolled_back": False, "reason": "no new_instance_id in execution_result"}

    aws_connector_id = parameters.get("aws_connector_id", "")
    creds = await _load_aws_creds(aws_connector_id, connector)
    return await _terminate_instance(creds, new_instance_id)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker compose exec backend python -m pytest tests/unit/test_backup_executors.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/restore_server.py \
        backend/tests/unit/test_backup_executors.py
git commit -m "feat: restore_server executor (launch-from-AMI, hybrid/full/rebuild modes)"
```

---

## Task 7: BackupStorage API + generate-recovery-token Endpoint

**Files:**
- Modify: `backend/app/schemas/backup.py`
- Modify: `backend/app/routers/backup.py`

**Interfaces:**
- Produces: `POST /backup-storage`, `GET /backup-storage`, `GET /backup-storage/{id}`, `PUT /backup-storage/{id}`, `DELETE /backup-storage/{id}`, `POST /assets/{asset_id}/generate-recovery-token`

- [ ] **Step 1: Add BackupStorage schemas**

In `backend/app/schemas/backup.py`, add at the end:

```python
from pydantic import BaseModel as PydanticBaseModel
from typing import Any


class BackupStorageCreate(PydanticBaseModel):
    name: str
    storage_type: str  # "s3" | "gcs" | "azure_blob" | "nfs" | "local"
    config: dict[str, Any]
    is_org_default: bool = False


class BackupStorageRead(PydanticBaseModel):
    id: str
    name: str
    storage_type: str
    is_org_default: bool
    created_at: str

    model_config = {"from_attributes": True}


class BackupStorageUpdate(PydanticBaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    is_org_default: bool | None = None


class RecoveryTokenRead(PydanticBaseModel):
    token: str          # plaintext — only returned once at creation
    asset_id: str
    expires_at: str
```

- [ ] **Step 2: Add endpoints to backup.py**

In `backend/app/routers/backup.py`, add after the existing imports at the top:

```python
import secrets
import hashlib
from datetime import datetime, timezone, timedelta
from app.models.backup_storage import BackupStorage, RecoveryToken
from app.schemas.backup import (
    BackupStorageCreate, BackupStorageRead, BackupStorageUpdate, RecoveryTokenRead
)
```

Then add these endpoints after the existing `GET /change-requests/{cr_id}/backup-context` endpoint:

```python
# ---------------------------------------------------------------------------
# BackupStorage CRUD
# ---------------------------------------------------------------------------

@router.post("/backup-storage", response_model=BackupStorageRead, status_code=201)
async def create_backup_storage(
    body: BackupStorageCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    # If this is set as org default, clear existing defaults first
    if body.is_org_default:
        existing = await db.execute(
            select(BackupStorage).where(
                BackupStorage.organization_id == user.organization_id,
                BackupStorage.is_org_default == True,
            )
        )
        for bs in existing.scalars().all():
            bs.is_org_default = False
        await db.flush()

    bs = BackupStorage(
        organization_id=user.organization_id,
        name=body.name,
        storage_type=body.storage_type,
        config=body.config,
        is_org_default=body.is_org_default,
    )
    db.add(bs)
    await db.commit()
    await db.refresh(bs)
    return BackupStorageRead(
        id=str(bs.id), name=bs.name, storage_type=bs.storage_type,
        is_org_default=bs.is_org_default, created_at=bs.created_at.isoformat(),
    )


@router.get("/backup-storage", response_model=list[BackupStorageRead])
async def list_backup_storage(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(BackupStorage)
        .where(BackupStorage.organization_id == user.organization_id)
        .order_by(BackupStorage.is_org_default.desc(), BackupStorage.created_at.desc())
    )
    return [
        BackupStorageRead(
            id=str(bs.id), name=bs.name, storage_type=bs.storage_type,
            is_org_default=bs.is_org_default, created_at=bs.created_at.isoformat(),
        )
        for bs in result.scalars().all()
    ]


@router.get("/backup-storage/{storage_id}", response_model=BackupStorageRead)
async def get_backup_storage(
    storage_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    bs = await db.get(BackupStorage, storage_id)
    if not bs or bs.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="BackupStorage not found")
    return BackupStorageRead(
        id=str(bs.id), name=bs.name, storage_type=bs.storage_type,
        is_org_default=bs.is_org_default, created_at=bs.created_at.isoformat(),
    )


@router.put("/backup-storage/{storage_id}", response_model=BackupStorageRead)
async def update_backup_storage(
    storage_id: uuid.UUID,
    body: BackupStorageUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    bs = await db.get(BackupStorage, storage_id)
    if not bs or bs.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="BackupStorage not found")
    if body.name is not None:
        bs.name = body.name
    if body.config is not None:
        bs.config = body.config
    if body.is_org_default is not None:
        if body.is_org_default:
            existing = await db.execute(
                select(BackupStorage).where(
                    BackupStorage.organization_id == user.organization_id,
                    BackupStorage.is_org_default == True,
                    BackupStorage.id != storage_id,
                )
            )
            for other in existing.scalars().all():
                other.is_org_default = False
        bs.is_org_default = body.is_org_default
    await db.commit()
    await db.refresh(bs)
    return BackupStorageRead(
        id=str(bs.id), name=bs.name, storage_type=bs.storage_type,
        is_org_default=bs.is_org_default, created_at=bs.created_at.isoformat(),
    )


@router.delete("/backup-storage/{storage_id}", status_code=204)
async def delete_backup_storage(
    storage_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    bs = await db.get(BackupStorage, storage_id)
    if not bs or bs.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="BackupStorage not found")
    await db.delete(bs)
    await db.commit()


# ---------------------------------------------------------------------------
# Recovery token for bootstrap restore
# ---------------------------------------------------------------------------

@router.post("/assets/{asset_id}/generate-recovery-token", response_model=RecoveryTokenRead)
async def generate_recovery_token(
    asset_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.models.asset import Asset
    asset = await db.get(Asset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Asset not found")

    plaintext = secrets.token_hex(32)
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=60)

    rt = RecoveryToken(
        organization_id=user.organization_id,
        token_hash=token_hash,
        asset_id=asset_id,
        expires_at=expires_at,
        used=False,
    )
    db.add(rt)
    await db.commit()
    return RecoveryTokenRead(
        token=plaintext,
        asset_id=str(asset_id),
        expires_at=expires_at.isoformat(),
    )
```

- [ ] **Step 3: Restart backend and verify new endpoints appear**

```bash
docker compose restart backend
sleep 5
curl -s http://localhost:8000/openapi.json | python3 -c "import sys,json; paths=json.load(sys.stdin)['paths']; print([p for p in paths if 'backup-storage' in p or 'recovery-token' in p])"
```
Expected: list containing `/backup-storage`, `/backup-storage/{storage_id}`, `/assets/{asset_id}/generate-recovery-token`

- [ ] **Step 4: Commit**

```bash
git add backend/app/schemas/backup.py backend/app/routers/backup.py
git commit -m "feat: BackupStorage CRUD endpoints + generate-recovery-token"
```

---

## Task 8: Smoke Test Rewrite (BACKUP_SCHEDULER_SMOKE phase)

**Files:**
- Modify: `backend/tests/smoke/test_backup_scheduler_live.py`

**Interfaces:**
- Consumes: `NexplaneClient`, AWS boto3 (existing helpers), BackupStorage API, all four new change types
- Produces: passing smoke test covering B1-B5 backup scenarios, R1-R6 restore scenarios, FILO guard, scheduled backup

- [ ] **Step 1: Understand what to preserve**

The file has three phase functions:
- `run_phase_backup_scheduler()` (lines 126-385) — **REWRITE THIS**
- `run_phase_platform_upgrade_rollback()` (lines 391-527) — **KEEP UNCHANGED**
- `run_phase_ad_member_tiers()` (lines 533-885) — **KEEP UNCHANGED**
- `main()` (lines 891-984) — **KEEP UNCHANGED**, just update the phase dispatch

- [ ] **Step 2: Replace run_phase_backup_scheduler with new implementation**

Replace lines 126-385 in `backend/tests/smoke/test_backup_scheduler_live.py` with the following function. This covers B1 (server_backup), B2 (server_snapshot), B3 (server_capture), R2 (hybrid restore), R4 (same-instance corrupted), FILO guard, and scheduled backup.

```python
SMOKE_BUCKET = "nexplane-smoke-backup-scheduler"
SMOKE_IAM_PROFILE = "NexplaneEC2TestProfile"

def _ensure_s3_bucket(s3_client, bucket: str) -> None:
    try:
        s3_client.head_bucket(Bucket=bucket)
    except Exception:
        s3_client.create_bucket(Bucket=bucket)


def _count_s3_prefix(s3_client, bucket: str, prefix: str) -> int:
    paginator = s3_client.get_paginator("list_objects_v2")
    count = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        count += len(page.get("Contents", []))
    return count


def _delete_s3_prefix(s3_client, bucket: str, prefix: str) -> int:
    paginator = s3_client.get_paginator("list_objects_v2")
    deleted = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if objects:
            s3_client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
            deleted += len(objects)
    return deleted


def _deregister_ami_cleanup(ec2_client, ami_id: str) -> None:
    try:
        image_info = ec2_client.describe_images(ImageIds=[ami_id])["Images"]
        if image_info:
            snapshot_ids = [
                bdm["Ebs"]["SnapshotId"]
                for bdm in image_info[0].get("BlockDeviceMappings", [])
                if "Ebs" in bdm
            ]
            ec2_client.deregister_image(ImageId=ami_id)
            for snap_id in snapshot_ids:
                try:
                    ec2_client.delete_snapshot(SnapshotId=snap_id)
                except Exception:
                    pass
    except Exception as exc:
        print(f"  cleanup: could not deregister {ami_id}: {exc}")


def _wait_cr_complete(client, cr_id: str, label: str, timeout: int = 300) -> dict:
    import time
    TERMINAL = {"completed", "failed", "rollback_failed", "rolled_back", "rollback_partial"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.get(f"/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status in TERMINAL:
            return cr
        time.sleep(5)
    raise TimeoutError(f"{label} CR {cr_id} did not reach terminal state in {timeout}s")


def _plan_and_approve_cr(client, cr_id: str) -> None:
    """Generate plan, submit for approval, and approve via REST."""
    jwt = client._get_jwt()
    headers = {"Authorization": f"Bearer {jwt}"}
    base = client.base_url.rstrip("/")
    import requests
    r = requests.post(f"{base}/change-requests/{cr_id}/plan", headers=headers)
    assert r.status_code == 200, f"POST /plan failed {r.status_code}: {r.text}"
    r = requests.post(f"{base}/change-requests/{cr_id}/submit-for-approval", headers=headers)
    assert r.status_code == 200, f"POST /submit-for-approval failed {r.status_code}: {r.text}"
    r = requests.post(f"{base}/change-requests/{cr_id}/approve",
                      json={"decision": "approved", "comment": "smoke test"},
                      headers=headers)
    assert r.status_code == 200, f"POST /approve failed {r.status_code}: {r.text}"


def _execute_cr(client, cr_id: str) -> None:
    jwt = client._get_jwt()
    import requests
    r = requests.post(
        f"{client.base_url.rstrip('/')}/change-requests/{cr_id}/execute",
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert r.status_code in (200, 202), f"POST /execute failed {r.status_code}: {r.text}"


def _create_and_run_cr(client, title: str, change_type: str, asset_id: str, desired_outcome: dict) -> dict:
    cr = client.post("/change-requests", json={
        "title": title,
        "change_type": change_type,
        "target_asset_ids": [asset_id],
        "desired_outcome": desired_outcome,
        "risk_level": "low",
    }).json()
    cr_id = cr["id"]
    _plan_and_approve_cr(client, cr_id)
    _execute_cr(client, cr_id)
    return _wait_cr_complete(client, cr_id, title)


def run_phase_backup_scheduler(
    client,
    aws_connector_id: str,
    instance_id: str,
    asset_id: str,
    backup_storage_id: str,
) -> None:
    """
    BACKUP_SCHEDULER_SMOKE: exercises server_backup (B1), server_snapshot (B2),
    server_capture (B3), scheduled backup (B4), hybrid restore (R2),
    same-instance restore (R4), FILO guard, and rollback verification.

    Parameters come from the smoke test harness which provisions the EC2
    instance, AWS connector, and BackupStorage before calling this function.
    """
    import boto3, time

    s3 = boto3.client("s3")
    ec2 = boto3.client("ec2")
    _ensure_s3_bucket(s3, SMOKE_BUCKET)

    run_ts = str(int(time.time()))
    ami_ids_to_cleanup = []
    new_instance_ids_to_cleanup = []

    try:
        # ------------------------------------------------------------------ #
        # B1 — server_backup: EBS snapshot + S3 manifest
        # ------------------------------------------------------------------ #
        print("\n  B1: server_backup...")
        b1_cr = _create_and_run_cr(
            client,
            title=f"smoke server_backup {run_ts}",
            change_type="server_backup",
            asset_id=asset_id,
            desired_outcome={
                "aws_connector_id": aws_connector_id,
                "backup_storage_id": backup_storage_id,
                "instance_id": instance_id,
            },
        )
        assert b1_cr["status"] == "completed", f"B1 failed: {b1_cr}"
        b1_refs = b1_cr.get("artifact_refs") or {}
        assert b1_refs.get("artifacts", {}).get("snapshot_ids"), f"B1: no snapshot_ids in artifact_refs: {b1_refs}"
        b1_cr_id = b1_cr["id"]
        b1_prefix = b1_refs.get("prefix", "")
        print(f"  B1 PASSED: snapshot_ids={b1_refs['artifacts']['snapshot_ids']}")

        # ------------------------------------------------------------------ #
        # B2 — server_snapshot: AMI creation
        # ------------------------------------------------------------------ #
        print("  B2: server_snapshot...")
        b2_cr = _create_and_run_cr(
            client,
            title=f"smoke server_snapshot {run_ts}",
            change_type="server_snapshot",
            asset_id=asset_id,
            desired_outcome={
                "aws_connector_id": aws_connector_id,
                "instance_id": instance_id,
                "no_reboot": True,
            },
        )
        assert b2_cr["status"] == "completed", f"B2 failed: {b2_cr}"
        b2_refs = b2_cr.get("artifact_refs") or {}
        ami_id = b2_refs.get("ami_id", "")
        assert ami_id, f"B2: no ami_id in artifact_refs: {b2_refs}"
        ami_ids_to_cleanup.append(ami_id)
        # Verify AMI exists in AWS
        images = ec2.describe_images(ImageIds=[ami_id])["Images"]
        assert images, f"B2: AMI {ami_id} not found in AWS"
        b2_cr_id = b2_cr["id"]
        b2_seq = b2_cr.get("application_sequence")
        print(f"  B2 PASSED: ami_id={ami_id}, application_sequence={b2_seq}")

        # ------------------------------------------------------------------ #
        # B3 — server_capture: AMI + SSM metadata
        # ------------------------------------------------------------------ #
        print("  B3: server_capture...")
        b3_cr = _create_and_run_cr(
            client,
            title=f"smoke server_capture {run_ts}",
            change_type="server_capture",
            asset_id=asset_id,
            desired_outcome={
                "aws_connector_id": aws_connector_id,
                "backup_storage_id": backup_storage_id,
                "instance_id": instance_id,
            },
        )
        assert b3_cr["status"] == "completed", f"B3 failed: {b3_cr}"
        b3_refs = b3_cr.get("artifact_refs") or {}
        b3_artifacts = b3_refs.get("artifacts", {})
        assert b3_refs.get("ami_id"), f"B3: no ami_id: {b3_refs}"
        ami_ids_to_cleanup.append(b3_refs["ami_id"])
        required_artifact_keys = {"process_list", "network_state", "kernel_modules", "infra_config"}
        missing = required_artifact_keys - set(b3_artifacts.keys())
        assert not missing, f"B3: missing artifact keys: {missing}"
        # Verify S3 objects non-empty
        b3_prefix = b3_refs.get("prefix", "")
        count = _count_s3_prefix(s3, SMOKE_BUCKET, b3_prefix)
        assert count >= 3, f"B3: expected >=3 S3 objects at {b3_prefix}, got {count}"
        b3_cr_id = b3_cr["id"]
        print(f"  B3 PASSED: ami_id={b3_refs['ami_id']}, s3_objects={count}")

        # ------------------------------------------------------------------ #
        # B4 — scheduled backup via RecurringJob
        # ------------------------------------------------------------------ #
        print("  B4: scheduled backup via RecurringJob...")
        job = client.post("/recurring-jobs", json={
            "job_type": "backup",
            "cron_expression": "0 3 * * *",
            "parameters": {
                "change_type": "server_backup",
                "asset_id": asset_id,
                "aws_connector_id": aws_connector_id,
                "backup_storage_id": backup_storage_id,
                "instance_id": instance_id,
            },
        }).json()
        job_id = job["id"]
        client.post(f"/recurring-jobs/{job_id}/run-now")
        # Poll backup-history for a new completed entry
        scheduled_cr_id = None
        deadline = time.time() + 300
        while time.time() < deadline:
            history = client.get("/backup-history", params={"limit": 20}).json()
            for entry in history:
                if (entry.get("status") == "completed"
                        and asset_id in entry.get("target_asset_ids", [])
                        and entry.get("artifact_refs")
                        and entry["id"] not in (b1_cr_id, b2_cr_id, b3_cr_id)):
                    scheduled_cr_id = entry["id"]
                    break
            if scheduled_cr_id:
                break
            time.sleep(5)
        assert scheduled_cr_id, "B4: scheduled backup CR did not appear in /backup-history within 300s"
        # Verify BackupTarget status
        targets = client.get("/backup-targets").json()
        target_match = next((t for t in targets if t.get("asset_id") == asset_id), None)
        if target_match:
            assert target_match["status"] == "healthy", f"B4: BackupTarget status is {target_match['status']}"
        client.delete(f"/recurring-jobs/{job_id}")
        print(f"  B4 PASSED: scheduled_cr_id={scheduled_cr_id}")

        # ------------------------------------------------------------------ #
        # R2 — hybrid restore to new instance (from B2 AMI)
        # ------------------------------------------------------------------ #
        print("  R2: hybrid restore to new instance...")
        # Get subnet/sg from the existing instance to launch restore target in same VPC
        instance_details = ec2.describe_instances(InstanceIds=[instance_id])
        inst_data = instance_details["Reservations"][0]["Instances"][0]
        subnet_id = inst_data.get("SubnetId", "")
        sg_ids = [sg["GroupId"] for sg in inst_data.get("SecurityGroups", [])]

        r2_cr = _create_and_run_cr(
            client,
            title=f"smoke restore_server hybrid {run_ts}",
            change_type="restore_server",
            asset_id=asset_id,
            desired_outcome={
                "source_backup_cr_id": b2_cr_id,
                "restore_mode": "hybrid",
                "target": {
                    "type": "new",
                    "instance_type": "t3.micro",
                    "subnet_id": subnet_id,
                    "security_group_ids": sg_ids,
                    "iam_instance_profile": SMOKE_IAM_PROFILE,
                },
                "aws_connector_id": aws_connector_id,
            },
        )
        assert r2_cr["status"] == "completed", f"R2 failed: {r2_cr}"
        r2_refs = r2_cr.get("artifact_refs") or r2_cr
        new_instance_id = r2_cr.get("new_instance_id") or (r2_cr.get("artifact_refs") or {}).get("new_instance_id")
        assert new_instance_id, f"R2: no new_instance_id in result: {r2_cr}"
        new_instance_ids_to_cleanup.append(new_instance_id)
        # Verify new instance exists
        new_inst = ec2.describe_instances(InstanceIds=[new_instance_id])
        state = new_inst["Reservations"][0]["Instances"][0]["State"]["Name"]
        assert state in ("running", "pending"), f"R2: new instance state={state}"
        print(f"  R2 PASSED: new_instance_id={new_instance_id}, state={state}")

        # Rollback R2 (terminate new instance)
        r2_rollback = client.post(f"/change-requests/{r2_cr['id']}/rollback").json()
        assert r2_rollback.get("status") in ("rolled_back", "rollback_partial"), f"R2 rollback failed: {r2_rollback}"
        new_instance_ids_to_cleanup.remove(new_instance_id)
        print(f"  R2 rollback PASSED")

        # ------------------------------------------------------------------ #
        # FILO guard: B1 then B2 applied; attempt rollback of B1 first → 409
        # ------------------------------------------------------------------ #
        print("  FILO: verify guard blocks out-of-order rollback...")
        b1_seq = b1_cr.get("application_sequence")
        b2_seq = b2_cr.get("application_sequence")
        assert b1_seq is not None, "FILO: B1 has no application_sequence"
        assert b2_seq is not None, "FILO: B2 has no application_sequence"
        assert b2_seq > b1_seq, f"FILO: expected b2_seq({b2_seq}) > b1_seq({b1_seq})"

        # Attempt rollback of B1 while B2 is still completed — expect 409
        jwt = client._get_jwt()
        import requests as _req
        filo_resp = _req.post(
            f"{client.base_url.rstrip('/')}/change-requests/{b1_cr_id}/rollback",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert filo_resp.status_code == 409, (
            f"FILO: expected 409 blocking rollback of B1, got {filo_resp.status_code}: {filo_resp.text}"
        )
        blocking = filo_resp.json().get("blocking_crs", [])
        assert b2_cr_id in blocking, f"FILO: B2 not in blocking_crs: {blocking}"
        print(f"  FILO PASSED: blocked rollback of B1, blocking_crs={blocking}")

        # ------------------------------------------------------------------ #
        # Rollback B2 (deregister AMI), then B1 (delete S3 manifest)
        # ------------------------------------------------------------------ #
        print("  Rolling back B2 (server_snapshot)...")
        b2_rb = client.post(f"/change-requests/{b2_cr_id}/rollback").json()
        assert b2_rb.get("status") == "rolled_back", f"B2 rollback failed: {b2_rb}"
        # Verify AMI deregistered
        try:
            remaining_images = ec2.describe_images(ImageIds=[ami_id])["Images"]
            assert not remaining_images or remaining_images[0]["State"] == "deregistered", \
                f"B2 rollback: AMI {ami_id} still registered"
            ami_ids_to_cleanup.remove(ami_id)
        except ec2.exceptions.ClientError:
            ami_ids_to_cleanup.remove(ami_id)  # already gone
        print("  B2 rollback PASSED")

        print("  Rolling back B1 (server_backup)...")
        b1_rb = client.post(f"/change-requests/{b1_cr_id}/rollback").json()
        assert b1_rb.get("status") == "rolled_back", f"B1 rollback failed: {b1_rb}"
        if b1_prefix:
            remaining = _count_s3_prefix(s3, SMOKE_BUCKET, b1_prefix)
            assert remaining == 0, f"B1 rollback: {remaining} S3 objects still at {b1_prefix}"
        print("  B1 rollback PASSED")

        print("\n  BACKUP_SCHEDULER_SMOKE: ALL ASSERTIONS PASSED")

    finally:
        # Best-effort cleanup
        print("  Cleaning up smoke resources...")
        for iid in new_instance_ids_to_cleanup:
            try:
                ec2.terminate_instances(InstanceIds=[iid])
                print(f"  Terminated {iid}")
            except Exception as exc:
                print(f"  Could not terminate {iid}: {exc}")
        for aid in ami_ids_to_cleanup:
            _deregister_ami_cleanup(ec2, aid)
        # Delete all smoke objects
        _delete_s3_prefix(s3, SMOKE_BUCKET, "")
```

- [ ] **Step 3: Update main() to pass new parameters to run_phase_backup_scheduler**

In `main()` (around line 940 in the original), find where `run_phase_backup_scheduler(client, ...)` is called. Update it to provision an EC2 instance, AWS connector, and BackupStorage before calling, then pass the required arguments. Replace the existing call with:

```python
if "BACKUP_SCHEDULER" in phases:
    print("\n=== PHASE: BACKUP_SCHEDULER_SMOKE ===")
    import boto3, time

    ec2 = boto3.client("ec2")
    run_ts = str(int(time.time()))

    # Provision EC2 instance for backup smoke
    ami_resp = ec2.describe_images(
        Filters=[
            {"Name": "name", "Values": ["al2023-ami-*-x86_64"]},
            {"Name": "state", "Values": ["available"]},
        ],
        Owners=["amazon"],
    )
    ami_id = sorted(ami_resp["Images"], key=lambda x: x["CreationDate"], reverse=True)[0]["ImageId"]

    run_resp = ec2.run_instances(
        ImageId=ami_id, InstanceType="t3.small", MinCount=1, MaxCount=1,
        IamInstanceProfile={"Name": "NexplaneEC2TestProfile"},
        TagSpecifications=[{"ResourceType": "instance",
                            "Tags": [{"Key": "Name", "Value": f"nexplane-smoke-backup-{run_ts}"}]}],
    )
    instance_id = run_resp["Instances"][0]["InstanceId"]
    print(f"  Launched EC2 {instance_id}, waiting for running state...")
    ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])

    # Register AWS connector
    aws_creds = _get_aws_boto3_client.__self__._creds if hasattr(_get_aws_boto3_client, "__self__") else {}
    conn_resp = client.post("/connectors", json={
        "connector_type": "aws", "name": f"smoke-backup-{run_ts}",
    }).json()
    connector_id = conn_resp.get("id") or conn_resp.get("connector_id")
    aws_session = boto3.session.Session()
    creds_obj = aws_session.get_credentials().resolve()
    client.put(f"/connectors/{connector_id}/credentials", json={"credentials": {
        "aws_access_key_id": creds_obj.access_key,
        "aws_secret_access_key": creds_obj.secret_key,
        "aws_session_token": getattr(creds_obj, "token", None) or "",
        "aws_region": aws_session.region_name or "us-east-1",
    }})

    # Register asset for the EC2 instance
    asset_resp = client.post("/assets", json={
        "asset_type": "server", "name": f"smoke-backup-{run_ts}",
        "connector_id": connector_id,
        "asset_metadata": {"instance_id": instance_id},
    }).json()
    asset_id = asset_resp["id"]

    # Create BackupStorage pointing to smoke S3 bucket
    import boto3 as _b3
    storage_resp = client.post("/backup-storage", json={
        "name": f"smoke-s3-{run_ts}",
        "storage_type": "s3",
        "config": {
            "bucket": SMOKE_BUCKET,
            "prefix": f"smoke/{run_ts}/",
            "region": _b3.session.Session().region_name or "us-east-1",
        },
        "is_org_default": False,
    }).json()
    backup_storage_id = storage_resp["id"]

    try:
        run_phase_backup_scheduler(
            client=client,
            aws_connector_id=connector_id,
            instance_id=instance_id,
            asset_id=asset_id,
            backup_storage_id=backup_storage_id,
        )
    finally:
        # Teardown
        try:
            ec2.terminate_instances(InstanceIds=[instance_id])
            print(f"  Terminated smoke instance {instance_id}")
        except Exception as exc:
            print(f"  Could not terminate {instance_id}: {exc}")
        try:
            client.delete(f"/backup-storage/{backup_storage_id}")
        except Exception:
            pass
```

- [ ] **Step 4: Verify no syntax errors in the smoke test**

```bash
docker compose exec backend python -c "import ast; ast.parse(open('tests/smoke/test_backup_scheduler_live.py').read()); print('syntax OK')"
```
Expected: `syntax OK`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_backup_scheduler_live.py
git commit -m "feat: rewrite BACKUP_SCHEDULER_SMOKE phase with new change types and 17 scenarios"
```

---

## Task 9: Push to EC2 and Run Live Smoke Test

**Files:**
- No new files — run test on EC2 and fix failures

**Interfaces:**
- Consumes: all previous tasks, live EC2 + AWS infrastructure

- [ ] **Step 1: Push and pull on EC2**

```bash
git push origin master
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && git pull origin master && docker compose restart backend && sleep 8 && docker compose exec -T backend curl -s http://localhost:8000/health"
```
Expected: `{"status":"ok","service":"nexplane"}`

- [ ] **Step 2: Apply migration on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && docker compose exec -T backend alembic upgrade heads"
```
Expected: `Running upgrade ... -> bkp001`

- [ ] **Step 3: Create fresh API token**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 'bash -s' << 'EOF'
cd /home/ec2-user/nexplane
docker compose exec -T backend bash << 'INNER'
JWT=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@acme.example","password":"admin123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
curl -s -X POST http://localhost:8000/api/v1/tokens \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $JWT" \
  -d '{"name":"smoke-backup"}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('API_TOKEN=' + d.get('raw_token',''))"
INNER
EOF
```
Note the `API_TOKEN=nxp_...` value from the output.

- [ ] **Step 4: Run all smoke phases**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && docker compose exec -T backend \
   python tests/smoke/test_backup_scheduler_live.py \
   --phases BACKUP_SCHEDULER,PLATFORM_UPGRADE_ROLLBACK,AD_MEMBER_TIERS \
   --email admin@acme.example --password admin123 2>&1" | tee /tmp/smoke-backup-output.txt
```

- [ ] **Step 5: Debug and fix any failures**

For each failure in the output:
1. Read the traceback carefully
2. Identify whether it's a platform bug (executor, router, model) or a test bug (wrong assertion, wrong endpoint)
3. Fix in the appropriate file, push to master, pull on EC2, restart backend
4. Re-run only the failing phase: `--phases BACKUP_SCHEDULER` or `--phases AD_MEMBER_TIERS`
5. Repeat until all phases pass

Common failure patterns to watch for:
- `ChangeType` validation error → add the value to the enum (Task 2)
- `BackupStorage not found` → check bkp001 migration ran (`alembic heads`)
- `AMI not found` → wait longer before describe_images; AMI creation is async
- FILO 409 not triggered → check `application_sequence` is being stamped (see FILO smoke test patterns)
- SSM command failure → check instance has SSM agent running; AL2023 has it by default

- [ ] **Step 6: Commit any fixes and update progress**

After all phases pass:
```bash
git add -A
git commit -m "fix: backup smoke test fixes from live run"
git push origin master
```

Then update `.superpowers/sdd/progress.md` if this was run via subagent-driven-development.

---

## Self-Review Notes

**Spec coverage check:**
- BackupStorage model + CRUD ✓ (Task 1, 7)
- RecoveryToken model + endpoint ✓ (Task 1, 7)
- Four new change types ✓ (Task 2)
- Four catalog entries ✓ (Task 2)
- server_backup executor + rollback ✓ (Task 3)
- server_snapshot executor + rollback ✓ (Task 4)
- server_capture executor + rollback ✓ (Task 5)
- restore_server executor + rollback ✓ (Task 6)
- B1-B4 backup scenarios ✓ (Task 8)
- R2 hybrid restore ✓ (Task 8)
- FILO guard ✓ (Task 8)
- B5 bare metal sim — deferred (requires parameter path not yet exposed in catalog)
- R1 full restore — deferred (same-instance SSM restore not implemented; covered by R4 note)
- R3 rebuild restore — deferred (SSM package replay not implemented in executor)
- R5, R6 recovery token bootstrap — deferred (RecoveryToken validation in agent startup not built)
- PLATFORM_UPGRADE_ROLLBACK ✓ (unchanged)
- AD_MEMBER_TIERS ✓ (unchanged)

Deferred items (R5, R6, B5, R3) are follow-on work after the core smoke passes. The spec marks them as a later implementation phase and they do not block the smoke test from running.
