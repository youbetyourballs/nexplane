# Backup & Restore Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the backup/restore executor into a pluggable three-abstraction architecture (capture strategy × storage backend × restore strategy), implement the local_files data-tier strategy, add the backup_tier/capture_strategy data model columns, and enhance the backup scheduler smoke test with SSM sentinel (B0), mandatory run-now approval (B4 fix), and SSM data-proof restore verification (R2).

**Architecture:** Three new subdirectories under `executors/nexplane_agent/` each expose a registry function (`get_strategy`/`get_backend`). `server_backup.py` and `restore_server.py` become thin dispatchers that look up the right module and delegate. Common AWS helpers move to `aws_utils.py`. The smoke test is enhanced in-place.

**Tech Stack:** Python 3.12, asyncio, boto3, paramiko 3.5+, SQLAlchemy 2.x async, alembic, httpx

---

## Global Constraints

- All strategy modules expose exactly `async def backup(params, asset_ids, connector) → dict` and `async def rollback(params, execution_result, connector) → dict` (backup strategies) or `async def restore(params, asset_ids, connector) → dict` and `async def rollback(params, execution_result, connector) → dict` (restore strategies)
- All storage backend modules expose exactly `async def put(key, data, config) → str`, `async def put_file(key, local_path, config) → str`, `async def delete_prefix(prefix, config) → dict`, `async def delete(uri, config) → None`
- Stub modules raise `NotImplementedError(f"Strategy '{name}' is not yet implemented")` — never return silently
- `capture_strategy` defaults to `"ebs_snapshot"` — no migration required for existing targets; `backup_tier` defaults to `"machine"`
- `artifact_refs` from every strategy must include `capture_strategy`, `restore_strategy`, `backup_tier`, `captured_at` fields
- SSM is used only in the smoke test harness (boto3 direct) — never called by backup or restore executors
- Smoke test runs from EC2 runner via `docker compose exec -T backend python tests/smoke/test_backup_scheduler_live.py --email admin@acme.example --password admin123`
- FILO rollback order (B3→B2→B1) is unchanged
- All unit tests run with `docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/<file> -v"`

---

## File Map

**Create:**
- `backend/app/connectors/executors/nexplane_agent/aws_utils.py` — shared `_ec2_client`, `_s3_client`, `_load_aws_creds`
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/__init__.py` — registry + `_load_storage_config` + `_load_backup_target`
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/ebs_snapshot.py` — extracted from server_backup.py
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/local_files.py` — new SSH/tar impl
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/mgn_replication.py` — stub
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/disk2vhd.py` — stub
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/lvm_snapshot.py` — stub
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/nfs_files.py` — stub (differs from local_files only in source_path resolution; real impl deferred)
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/database_dump.py` — stub
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/managed_db_snapshot.py` — stub
- `backend/app/connectors/executors/nexplane_agent/backup_strategies/storage_sync.py` — stub
- `backend/app/connectors/executors/nexplane_agent/storage_backends/__init__.py` — registry
- `backend/app/connectors/executors/nexplane_agent/storage_backends/s3.py` — extracted from server_backup.py
- `backend/app/connectors/executors/nexplane_agent/storage_backends/gcs.py` — stub
- `backend/app/connectors/executors/nexplane_agent/storage_backends/azure_blob.py` — stub
- `backend/app/connectors/executors/nexplane_agent/storage_backends/oci_object_storage.py` — stub
- `backend/app/connectors/executors/nexplane_agent/storage_backends/nfs.py` — stub
- `backend/app/connectors/executors/nexplane_agent/storage_backends/local.py` — stub
- `backend/app/connectors/executors/nexplane_agent/restore_strategies/__init__.py` — registry
- `backend/app/connectors/executors/nexplane_agent/restore_strategies/launch_ami.py` — extracted from restore_server.py
- `backend/app/connectors/executors/nexplane_agent/restore_strategies/in_place.py` — extracted from restore_server.py
- `backend/app/connectors/executors/nexplane_agent/restore_strategies/file_restore_to_path.py` — new SSH/tar impl
- `backend/app/connectors/executors/nexplane_agent/restore_strategies/import_image.py` — stub
- `backend/app/connectors/executors/nexplane_agent/restore_strategies/database_restore.py` — stub
- `backend/app/connectors/executors/nexplane_agent/restore_strategies/storage_restore.py` — stub
- `backend/alembic/versions/backup001_backup_target_strategy_fields.py` — migration
- `backend/app/tests/test_backup_architecture.py` — unit tests for dispatcher, backends, strategies

**Modify:**
- `backend/app/connectors/executors/nexplane_agent/server_backup.py` — rewrite as thin dispatcher
- `backend/app/connectors/executors/nexplane_agent/restore_server.py` — rewrite as thin dispatcher
- `backend/app/models/backup_target.py` — add `backup_tier` and `capture_strategy` columns
- `backend/tests/smoke/test_backup_scheduler_live.py` — add B0 sentinel, fix B4, enhance R2

---

## Task 1: Storage Backend Infrastructure

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/aws_utils.py`
- Create: `backend/app/connectors/executors/nexplane_agent/storage_backends/__init__.py`
- Create: `backend/app/connectors/executors/nexplane_agent/storage_backends/s3.py`
- Create: `backend/app/connectors/executors/nexplane_agent/storage_backends/gcs.py`
- Create: `backend/app/connectors/executors/nexplane_agent/storage_backends/azure_blob.py`
- Create: `backend/app/connectors/executors/nexplane_agent/storage_backends/oci_object_storage.py`
- Create: `backend/app/connectors/executors/nexplane_agent/storage_backends/nfs.py`
- Create: `backend/app/connectors/executors/nexplane_agent/storage_backends/local.py`
- Test: `backend/app/tests/test_backup_architecture.py`

**Interfaces:**
- Produces: `get_backend(storage_type: str) -> module` from `storage_backends/__init__.py`
- Produces: `_ec2_client(creds) -> boto3.client`, `_s3_client(creds) -> boto3.client`, `_load_aws_creds(aws_connector_id, fallback_connector) -> dict` from `aws_utils.py`

- [ ] **Step 1: Write failing tests for storage backend registry and S3 module**

```python
# backend/app/tests/test_backup_architecture.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest


class TestStorageBackendRegistry:
    def test_get_s3_backend_returns_module(self):
        from app.connectors.executors.nexplane_agent.storage_backends import get_backend
        mod = get_backend("s3")
        assert hasattr(mod, "put")
        assert hasattr(mod, "put_file")
        assert hasattr(mod, "delete_prefix")
        assert hasattr(mod, "delete")

    def test_get_unknown_backend_raises(self):
        from app.connectors.executors.nexplane_agent.storage_backends import get_backend
        with pytest.raises(ValueError, match="Unknown storage backend"):
            get_backend("unknown_backend_xyz")

    def test_stub_backends_raise_not_implemented(self):
        from app.connectors.executors.nexplane_agent.storage_backends import get_backend
        import asyncio
        for name in ("gcs", "azure_blob", "oci_object_storage", "nfs", "local"):
            mod = get_backend(name)
            with pytest.raises(NotImplementedError):
                asyncio.get_event_loop().run_until_complete(mod.put("key", b"data", {}))

    def test_s3_put_builds_uri(self):
        """S3 put returns an s3:// URI."""
        import asyncio
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.storage_backends import s3 as s3_mod
        mock_s3 = MagicMock()
        config = {"bucket": "mybucket", "region": "us-east-1"}
        with patch("boto3.client", return_value=mock_s3):
            uri = asyncio.get_event_loop().run_until_complete(
                s3_mod.put("prefix/manifest.json", b'{"test": 1}', config)
            )
        assert uri == "s3://mybucket/prefix/manifest.json"
        mock_s3.put_object.assert_called_once()

    def test_s3_delete_prefix_calls_list_and_delete(self):
        import asyncio
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.storage_backends import s3 as s3_mod
        mock_s3 = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"Contents": [{"Key": "prefix/a"}, {"Key": "prefix/b"}]}
        ]
        mock_s3.get_paginator.return_value = mock_paginator
        config = {"bucket": "mybucket", "region": "us-east-1"}
        with patch("boto3.client", return_value=mock_s3):
            result = asyncio.get_event_loop().run_until_complete(
                s3_mod.delete_prefix("prefix/", config)
            )
        assert result["deleted_count"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/test_backup_architecture.py::TestStorageBackendRegistry -v 2>&1 | tail -20"
```
Expected: `ModuleNotFoundError` or `ImportError` — modules do not exist yet.

- [ ] **Step 3: Create `aws_utils.py`**

```python
# backend/app/connectors/executors/nexplane_agent/aws_utils.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import logging

logger = logging.getLogger(__name__)


def _ec2_client(creds: dict):
    import boto3
    return boto3.client(
        "ec2",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


def _s3_client(creds: dict):
    import boto3
    return boto3.client(
        "s3",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


async def _load_aws_creds(aws_connector_id: str, fallback_connector) -> dict:
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
```

- [ ] **Step 4: Create `storage_backends/s3.py`**

```python
# backend/app/connectors/executors/nexplane_agent/storage_backends/s3.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


def _client(config: dict):
    import boto3
    return boto3.client(
        "s3",
        region_name=config.get("region", "us-east-1"),
        aws_access_key_id=config.get("aws_access_key_id"),
        aws_secret_access_key=config.get("aws_secret_access_key"),
        aws_session_token=config.get("aws_session_token"),
    )


async def put(key: str, data: bytes, config: dict) -> str:
    """Upload bytes to S3. Returns s3://bucket/key URI."""
    bucket = config["bucket"]

    def _sync():
        s3 = _client(config)
        s3.put_object(Bucket=bucket, Key=key, Body=data)
        return f"s3://{bucket}/{key}"

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def put_file(key: str, local_path: str, config: dict) -> str:
    """Upload a local file to S3. Returns s3://bucket/key URI."""
    bucket = config["bucket"]

    def _sync():
        s3 = _client(config)
        s3.upload_file(local_path, bucket, key)
        return f"s3://{bucket}/{key}"

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def delete_prefix(prefix: str, config: dict) -> dict:
    """Delete all objects under prefix. Returns {deleted_count}."""
    bucket = config["bucket"]

    def _sync():
        import boto3
        s3 = _client(config)
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
        return await loop.run_in_executor(pool, _sync)


async def delete(uri: str, config: dict) -> None:
    """Delete a single S3 object by its s3://bucket/key URI."""
    # uri format: s3://bucket/key
    parts = uri.replace("s3://", "").split("/", 1)
    bucket, key = parts[0], parts[1] if len(parts) > 1 else ""

    def _sync():
        s3 = _client(config)
        s3.delete_object(Bucket=bucket, Key=key)

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync)
```

- [ ] **Step 5: Create `storage_backends/__init__.py` with registry**

```python
# backend/app/connectors/executors/nexplane_agent/storage_backends/__init__.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
from types import ModuleType

from app.connectors.executors.nexplane_agent.storage_backends import s3

_STUB_NAMES = ("gcs", "azure_blob", "oci_object_storage", "nfs", "local")

_REGISTRY: dict[str, ModuleType] = {
    "s3": s3,
}

# Lazy-register stubs so they appear in the registry but raise on use
for _name in _STUB_NAMES:
    import importlib as _importlib
    _mod = _importlib.import_module(
        f"app.connectors.executors.nexplane_agent.storage_backends.{_name}"
    )
    _REGISTRY[_name] = _mod


def get_backend(storage_type: str) -> ModuleType:
    if storage_type not in _REGISTRY:
        raise ValueError(f"Unknown storage backend: '{storage_type}'. Known: {list(_REGISTRY)}")
    return _REGISTRY[storage_type]
```

- [ ] **Step 6: Create stub storage backends (gcs, azure_blob, oci_object_storage, nfs, local)**

Create each file with the same pattern. Example for `gcs.py` — repeat for the other four, changing only the name:

```python
# backend/app/connectors/executors/nexplane_agent/storage_backends/gcs.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
_NAME = "gcs"


async def put(key: str, data: bytes, config: dict) -> str:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")


async def put_file(key: str, local_path: str, config: dict) -> str:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")


async def delete_prefix(prefix: str, config: dict) -> dict:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")


async def delete(uri: str, config: dict) -> None:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")
```

Create identical files for `azure_blob.py`, `oci_object_storage.py`, `nfs.py`, `local.py` — change only `_NAME`.

- [ ] **Step 7: Run tests — all 5 should pass**

```bash
docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/test_backup_architecture.py::TestStorageBackendRegistry -v 2>&1 | tail -20"
```
Expected: `5 passed`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/aws_utils.py \
        backend/app/connectors/executors/nexplane_agent/storage_backends/ \
        backend/app/tests/test_backup_architecture.py
git commit -m "feat: storage backend abstraction layer — s3 impl + 5 stubs + aws_utils"
```

---

## Task 2: Backup Strategy Infrastructure + ebs_snapshot + Dispatcher

**Files:**
- Create: `backup_strategies/__init__.py`
- Create: `backup_strategies/ebs_snapshot.py`
- Create: `backup_strategies/mgn_replication.py`, `disk2vhd.py`, `lvm_snapshot.py`, `nfs_files.py`, `database_dump.py`, `managed_db_snapshot.py`, `storage_sync.py` (stubs)
- Modify: `backend/app/connectors/executors/nexplane_agent/server_backup.py`
- Test: `backend/app/tests/test_backup_architecture.py` (extend)

**Interfaces:**
- Consumes: `aws_utils._load_aws_creds`, `aws_utils._ec2_client`, `storage_backends.s3.put`, `storage_backends.s3.delete_prefix` from Task 1
- Produces: `get_strategy(capture_strategy: str) -> module` from `backup_strategies/__init__.py`
- Produces: `_load_storage_config(backup_storage_id: str) -> dict` from `backup_strategies/__init__.py`
- Produces: `_load_backup_target(asset_id: str) -> BackupTarget | None` from `backup_strategies/__init__.py`

- [ ] **Step 1: Add tests for backup dispatcher and ebs_snapshot strategy**

Append to `backend/app/tests/test_backup_architecture.py`:

```python
class TestBackupStrategyRegistry:
    def test_get_ebs_snapshot_returns_module(self):
        from app.connectors.executors.nexplane_agent.backup_strategies import get_strategy
        mod = get_strategy("ebs_snapshot")
        assert hasattr(mod, "backup")
        assert hasattr(mod, "rollback")

    def test_get_unknown_strategy_raises(self):
        from app.connectors.executors.nexplane_agent.backup_strategies import get_strategy
        with pytest.raises(ValueError, match="Unknown capture strategy"):
            get_strategy("does_not_exist_xyz")

    def test_stub_strategies_raise_not_implemented(self):
        from app.connectors.executors.nexplane_agent.backup_strategies import get_strategy
        import asyncio
        for name in ("mgn_replication", "disk2vhd", "lvm_snapshot", "database_dump",
                     "managed_db_snapshot", "storage_sync"):
            mod = get_strategy(name)
            with pytest.raises(NotImplementedError):
                asyncio.get_event_loop().run_until_complete(mod.backup({}, [], None))

    def test_ebs_snapshot_artifact_refs_contains_strategy_fields(self):
        """artifact_refs from ebs_snapshot must include the 4 required base fields."""
        import asyncio
        from unittest.mock import MagicMock, patch, AsyncMock
        from app.connectors.executors.nexplane_agent.backup_strategies import ebs_snapshot

        mock_ec2 = MagicMock()
        mock_ec2.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"BlockDeviceMappings": [
                {"Ebs": {"VolumeId": "vol-123"}}
            ]}]}]
        }
        mock_ec2.create_snapshot.return_value = {"SnapshotId": "snap-abc"}
        mock_ec2.delete_snapshot.return_value = {}

        mock_s3_backend = AsyncMock()
        mock_s3_backend.put.return_value = "s3://bucket/prefix/manifest.json"

        with patch(
            "app.connectors.executors.nexplane_agent.aws_utils._ec2_client",
            return_value=mock_ec2,
        ), patch(
            "app.connectors.executors.nexplane_agent.backup_strategies.ebs_snapshot._get_storage_backend",
            return_value=mock_s3_backend,
        ):
            result = asyncio.get_event_loop().run_until_complete(
                ebs_snapshot.backup(
                    {
                        "aws_connector_id": "",
                        "backup_storage_id": "",
                        "instance_id": "i-123",
                        "_storage_config": {"storage_type": "s3", "config": {"bucket": "b", "prefix": "p/"}},
                    },
                    ["asset-uuid"],
                    None,
                )
            )
        refs = result["artifact_refs"]
        for field in ("capture_strategy", "restore_strategy", "backup_tier", "captured_at"):
            assert field in refs, f"artifact_refs missing '{field}'"
        assert refs["capture_strategy"] == "ebs_snapshot"
        assert refs["restore_strategy"] == "launch_ami"
        assert refs["backup_tier"] == "machine"
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/test_backup_architecture.py::TestBackupStrategyRegistry -v 2>&1 | tail -20"
```
Expected: `ImportError` or `ModuleNotFoundError`.

- [ ] **Step 3: Create `backup_strategies/__init__.py`**

```python
# backend/app/connectors/executors/nexplane_agent/backup_strategies/__init__.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import importlib
import logging
from types import ModuleType

logger = logging.getLogger(__name__)

_PKG = "app.connectors.executors.nexplane_agent.backup_strategies"

_STRATEGY_NAMES = (
    "ebs_snapshot",
    "mgn_replication",
    "disk2vhd",
    "lvm_snapshot",
    "local_files",
    "nfs_files",
    "database_dump",
    "managed_db_snapshot",
    "storage_sync",
)

_REGISTRY: dict[str, ModuleType] = {}


def get_strategy(capture_strategy: str) -> ModuleType:
    if capture_strategy not in _REGISTRY:
        if capture_strategy in _STRATEGY_NAMES:
            _REGISTRY[capture_strategy] = importlib.import_module(f"{_PKG}.{capture_strategy}")
        else:
            raise ValueError(
                f"Unknown capture strategy: '{capture_strategy}'. Known: {list(_STRATEGY_NAMES)}"
            )
    return _REGISTRY[capture_strategy]


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


async def _load_backup_target(asset_id: str):
    """Return the BackupTarget for this asset, or None if none exists."""
    import uuid as _uuid
    from app.database import AsyncSessionLocal
    from app.models.backup_target import BackupTarget
    from sqlalchemy import select
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(BackupTarget).where(
                    BackupTarget.asset_id == _uuid.UUID(asset_id)
                ).limit(1)
            )
            return result.scalar_one_or_none()
    except Exception as exc:
        logger.warning("Could not load BackupTarget for asset %s: %s", asset_id, exc)
        return None
```

- [ ] **Step 4: Create `backup_strategies/ebs_snapshot.py`**

```python
# backend/app/connectors/executors/nexplane_agent/backup_strategies/ebs_snapshot.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: EBS snapshot + AMI (AWS EC2). Machine tier."""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _get_storage_backend(storage_type: str):
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend
    return get_backend(storage_type)


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client
    from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config

    aws_connector_id = params.get("aws_connector_id", "")
    backup_storage_id = params.get("backup_storage_id", "")
    instance_id = params.get("instance_id", "")

    if not asset_ids:
        raise RuntimeError("ebs_snapshot: no asset_ids provided")

    creds = await _load_aws_creds(aws_connector_id, connector)

    # Allow pre-resolved storage config to be injected (useful in tests)
    storage_config = params.get("_storage_config") or await _load_storage_config(backup_storage_id)
    cfg = storage_config.get("config", {})
    prefix = f"{cfg.get('prefix', 'backups/')}{asset_ids[0]}/"

    ec2 = _ec2_client(creds)

    def _sync_snapshot():
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        reservations = resp.get("Reservations", [])
        if not reservations:
            raise RuntimeError(f"ebs_snapshot: instance {instance_id} not found")
        instance = reservations[0]["Instances"][0]
        block_devices = instance.get("BlockDeviceMappings", [])
        snapshot_ids = []
        for bd in block_devices:
            volume_id = bd["Ebs"]["VolumeId"]
            snap = ec2.create_snapshot(
                VolumeId=volume_id,
                Description=f"nexplane-ebs-snapshot {instance_id} {datetime.now(timezone.utc).isoformat()}",
                TagSpecifications=[{
                    "ResourceType": "snapshot",
                    "Tags": [
                        {"Key": "nexplane:change_type", "Value": "server_backup"},
                        {"Key": "nexplane:instance_id", "Value": instance_id},
                    ],
                }],
            )
            snapshot_ids.append(snap["SnapshotId"])
        return snapshot_ids

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        snapshot_ids = await loop.run_in_executor(pool, _sync_snapshot)

    storage_type = storage_config["storage_type"]
    backend = _get_storage_backend(storage_type)
    manifest_key = f"{prefix}manifest.json"
    captured_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "instance_id": instance_id,
        "snapshot_ids": snapshot_ids,
        "captured_at": captured_at,
    }
    manifest_uri = await backend.put(manifest_key, json.dumps(manifest).encode(), cfg)

    artifact_refs = {
        "capture_strategy": "ebs_snapshot",
        "restore_strategy": "launch_ami",
        "backup_tier": "machine",
        "captured_at": captured_at,
        "storage_type": storage_type,
        "bucket_or_path": cfg.get("bucket", cfg.get("path", "")),
        "prefix": prefix,
        "artifacts": {
            "snapshot_ids": snapshot_ids,
            "manifest_key": manifest_key,
            "manifest_uri": manifest_uri,
        },
    }
    return {
        "status": "completed",
        "artifact_refs": artifact_refs,
        "_asset_ids": [str(a) for a in asset_ids],
        "_aws_connector_id": aws_connector_id,
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    artifact_refs = execution_result.get("artifact_refs", {})
    storage_type = artifact_refs.get("storage_type", "s3")
    prefix = artifact_refs.get("prefix", "")
    artifacts = artifact_refs.get("artifacts", {})
    snapshot_ids = artifacts.get("snapshot_ids", [])

    aws_connector_id = (
        params.get("aws_connector_id")
        or execution_result.get("_aws_connector_id")
        or ""
    )
    creds = await _load_aws_creds(aws_connector_id, connector)
    ec2 = _ec2_client(creds)

    deleted_snapshots = []
    for snap_id in snapshot_ids:
        try:
            ec2.delete_snapshot(SnapshotId=snap_id)
            deleted_snapshots.append(snap_id)
        except Exception as exc:
            logger.warning("Failed to delete snapshot %s: %s", snap_id, exc)

    # Load storage config from DB to get credentials for the backend
    bucket = artifact_refs.get("bucket_or_path", "")
    # For rollback, build minimal config from artifact_refs (avoids DB lookup on already-deleted storage)
    if storage_type == "s3" and bucket and prefix:
        cfg = {"bucket": bucket, "region": creds.get("region", "us-east-1")}
        # Inject AWS creds into config for s3 backend
        cfg["aws_access_key_id"] = creds.get("access_key_id", creds.get("aws_access_key_id"))
        cfg["aws_secret_access_key"] = creds.get("secret_access_key", creds.get("aws_secret_access_key"))
        cfg["aws_session_token"] = creds.get("session_token")
        backend = get_backend("s3")
        delete_result = await backend.delete_prefix(prefix, cfg)
        return {
            "rolled_back": True,
            "deleted_snapshots": deleted_snapshots,
            **delete_result,
        }

    return {
        "rolled_back": bool(deleted_snapshots),
        "deleted_snapshots": deleted_snapshots,
        "reason": f"storage_type={storage_type} rollback not implemented" if not deleted_snapshots else None,
    }
```

- [ ] **Step 5: Create stub backup strategies**

Create `mgn_replication.py`, `disk2vhd.py`, `lvm_snapshot.py`, `nfs_files.py`, `database_dump.py`, `managed_db_snapshot.py`, `storage_sync.py` — same pattern:

```python
# backend/app/connectors/executors/nexplane_agent/backup_strategies/mgn_replication.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
_NAME = "mgn_replication"


async def backup(params: dict, asset_ids: list, connector) -> dict:
    raise NotImplementedError(f"Capture strategy '{_NAME}' is not yet implemented")


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    raise NotImplementedError(f"Capture strategy '{_NAME}' is not yet implemented")
```

Repeat for each stub, changing only `_NAME`.

- [ ] **Step 6: Rewrite `server_backup.py` as thin dispatcher**

```python
# backend/app/connectors/executors/nexplane_agent/server_backup.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""server_backup executor — thin dispatcher to capture strategy modules."""
import logging
from app.connectors.executors.nexplane_agent.backup_strategies import (
    get_strategy,
    _load_backup_target,
    _load_storage_config,
)

logger = logging.getLogger(__name__)

# Re-export for modules that still import these from server_backup (e.g. restore_server)
from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client  # noqa: F401


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise RuntimeError("server_backup: no asset_ids provided")

    capture_strategy = parameters.get("capture_strategy")
    if not capture_strategy:
        target = await _load_backup_target(str(asset_ids[0]))
        capture_strategy = (
            getattr(target, "capture_strategy", None) if target else None
        ) or "ebs_snapshot"

    strategy = get_strategy(capture_strategy)
    return await strategy.backup(parameters, asset_ids, connector)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    capture_strategy = (
        execution_result.get("artifact_refs", {}).get("capture_strategy")
        or parameters.get("capture_strategy")
        or "ebs_snapshot"
    )
    strategy = get_strategy(capture_strategy)
    return await strategy.rollback(parameters, execution_result, connector)
```

- [ ] **Step 7: Run all backup strategy tests**

```bash
docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/test_backup_architecture.py -v 2>&1 | tail -30"
```
Expected: all tests that were written pass. The `test_ebs_snapshot_artifact_refs_contains_strategy_fields` test should now pass.

- [ ] **Step 8: Smoke the dispatcher change in the container**

```bash
docker compose exec -T backend python -c "
import asyncio
from app.connectors.executors.nexplane_agent import server_backup
print('server_backup dispatcher loaded OK')
from app.connectors.executors.nexplane_agent.backup_strategies import get_strategy
s = get_strategy('ebs_snapshot')
print('ebs_snapshot strategy:', s)
"
```
Expected: no exception, prints both lines.

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/backup_strategies/ \
        backend/app/connectors/executors/nexplane_agent/server_backup.py \
        backend/app/tests/test_backup_architecture.py
git commit -m "feat: backup strategy dispatcher — ebs_snapshot extracted, 7 stubs, server_backup thinned"
```

---

## Task 3: Restore Strategy Infrastructure + launch_ami + Dispatcher

**Files:**
- Create: `restore_strategies/__init__.py`
- Create: `restore_strategies/launch_ami.py`
- Create: `restore_strategies/in_place.py`
- Create: `restore_strategies/file_restore_to_path.py` (interface + stub-level for now; full impl in Task 5)
- Create: `restore_strategies/import_image.py`, `database_restore.py`, `storage_restore.py` (stubs)
- Modify: `backend/app/connectors/executors/nexplane_agent/restore_server.py`
- Test: `backend/app/tests/test_backup_architecture.py` (extend)

**Interfaces:**
- Consumes: `aws_utils._load_aws_creds`, `aws_utils._ec2_client` from Task 1
- Produces: `get_strategy(restore_strategy: str) -> module` from `restore_strategies/__init__.py`

- [ ] **Step 1: Add tests for restore dispatcher**

Append to `backend/app/tests/test_backup_architecture.py`:

```python
class TestRestoreStrategyRegistry:
    def test_get_launch_ami_returns_module(self):
        from app.connectors.executors.nexplane_agent.restore_strategies import get_strategy
        mod = get_strategy("launch_ami")
        assert hasattr(mod, "restore")
        assert hasattr(mod, "rollback")

    def test_get_unknown_restore_strategy_raises(self):
        from app.connectors.executors.nexplane_agent.restore_strategies import get_strategy
        with pytest.raises(ValueError, match="Unknown restore strategy"):
            get_strategy("does_not_exist_xyz")

    def test_stub_restore_strategies_raise_not_implemented(self):
        from app.connectors.executors.nexplane_agent.restore_strategies import get_strategy
        import asyncio
        for name in ("import_image", "database_restore", "storage_restore"):
            mod = get_strategy(name)
            with pytest.raises(NotImplementedError):
                asyncio.get_event_loop().run_until_complete(mod.restore({}, [], None))

    def test_launch_ami_restore_calls_run_instances(self):
        import asyncio
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.restore_strategies import launch_ami

        mock_ec2 = MagicMock()
        mock_ec2.run_instances.return_value = {
            "Instances": [{"InstanceId": "i-restored"}]
        }

        with patch(
            "app.connectors.executors.nexplane_agent.aws_utils._ec2_client",
            return_value=mock_ec2,
        ), patch(
            "app.connectors.executors.nexplane_agent.aws_utils._load_aws_creds",
            return_value={},
        ):
            # Patch the DB lookup in restore_server source artifact loader
            with patch(
                "app.connectors.executors.nexplane_agent.restore_strategies.launch_ami._load_source_artifact_refs",
                return_value={"ami_id": "ami-test123", "capture_strategy": "ebs_snapshot"},
            ):
                result = asyncio.get_event_loop().run_until_complete(
                    launch_ami.restore(
                        {
                            "source_backup_cr_id": "fake-cr-id",
                            "restore_mode": "hybrid",
                            "target": {"type": "new", "instance_type": "t3.micro"},
                            "aws_connector_id": "",
                        },
                        ["asset-uuid"],
                        None,
                    )
                )
        assert result["new_instance_id"] == "i-restored"
        assert result["status"] == "completed"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/test_backup_architecture.py::TestRestoreStrategyRegistry -v 2>&1 | tail -20"
```
Expected: `ImportError`.

- [ ] **Step 3: Create `restore_strategies/__init__.py`**

```python
# backend/app/connectors/executors/nexplane_agent/restore_strategies/__init__.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import importlib
from types import ModuleType

_PKG = "app.connectors.executors.nexplane_agent.restore_strategies"

_STRATEGY_NAMES = (
    "launch_ami",
    "in_place",
    "import_image",
    "file_restore_to_path",
    "database_restore",
    "storage_restore",
)

_REGISTRY: dict[str, ModuleType] = {}


def get_strategy(restore_strategy: str) -> ModuleType:
    if restore_strategy not in _REGISTRY:
        if restore_strategy in _STRATEGY_NAMES:
            _REGISTRY[restore_strategy] = importlib.import_module(f"{_PKG}.{restore_strategy}")
        else:
            raise ValueError(
                f"Unknown restore strategy: '{restore_strategy}'. Known: {list(_STRATEGY_NAMES)}"
            )
    return _REGISTRY[restore_strategy]
```

- [ ] **Step 4: Create `restore_strategies/launch_ami.py`**

```python
# backend/app/connectors/executors/nexplane_agent/restore_strategies/launch_ami.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: launch a new EC2 instance from an AMI."""
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def _load_source_artifact_refs(source_backup_cr_id: str) -> dict:
    import uuid as _uuid
    from app.database import AsyncSessionLocal
    from app.models.change_request import ChangeRequest
    from app.models.execution_run import ExecutionRun
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        cr = await db.get(ChangeRequest, _uuid.UUID(source_backup_cr_id))
        if not cr:
            raise RuntimeError(f"Source backup CR {source_backup_cr_id} not found")
        if cr.artifact_refs:
            return cr.artifact_refs
        result = await db.execute(
            select(ExecutionRun)
            .where(ExecutionRun.change_request_id == cr.id)
            .order_by(ExecutionRun.started_at.desc())
            .limit(1)
        )
        er = result.scalar_one_or_none()
        if not er or not er.result:
            return {}
        for step in (er.result.get("execution") or {}).get("steps", []):
            refs = (step.get("result") or {}).get("artifact_refs")
            if refs:
                return refs
        return {}


async def restore(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client

    if not asset_ids:
        raise RuntimeError("launch_ami: no asset_ids provided")

    source_backup_cr_id = params.get("source_backup_cr_id", "")
    restore_mode = params.get("restore_mode", "hybrid")
    target = params.get("target", {"type": "new"})
    aws_connector_id = params.get("aws_connector_id", "")

    if not source_backup_cr_id:
        raise RuntimeError("launch_ami: source_backup_cr_id is required")

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)
    ami_id = artifact_refs.get("ami_id", "")
    if not ami_id:
        raise RuntimeError(
            f"launch_ami: source CR {source_backup_cr_id} has no ami_id in artifact_refs"
        )

    creds = await _load_aws_creds(aws_connector_id, connector)
    ec2 = _ec2_client(creds)

    def _sync_launch():
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
        new_instance_id = await loop.run_in_executor(pool, _sync_launch)

    return {
        "status": "completed",
        "restore_mode": restore_mode,
        "new_instance_id": new_instance_id,
        "source_backup_cr_id": source_backup_cr_id,
        "ami_id": ami_id,
        "launched_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
        "_aws_connector_id": aws_connector_id,
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client

    new_instance_id = execution_result.get("new_instance_id", "")
    if not new_instance_id:
        return {"rolled_back": False, "reason": "no new_instance_id in execution_result"}

    aws_connector_id = (
        params.get("aws_connector_id")
        or execution_result.get("_aws_connector_id")
        or ""
    )
    creds = await _load_aws_creds(aws_connector_id, connector)
    ec2 = _ec2_client(creds)

    def _sync_terminate():
        ec2.terminate_instances(InstanceIds=[new_instance_id])
        return {"rolled_back": True, "terminated_instance_id": new_instance_id}

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync_terminate)
```

- [ ] **Step 5: Create `restore_strategies/in_place.py`**

```python
# backend/app/connectors/executors/nexplane_agent/restore_strategies/in_place.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: in-place restore to the same instance. Irreversible."""


class IrreversibleOperationError(Exception):
    pass


async def restore(params: dict, asset_ids: list, connector) -> dict:
    if not params.get("confirm_same_target"):
        raise RuntimeError(
            "in_place restore is irreversible. Set confirm_same_target=true to proceed."
        )
    return {
        "status": "completed",
        "restore_mode": "same",
        "source_backup_cr_id": params.get("source_backup_cr_id", ""),
        "note": "same-target restore dispatched via SSM — verify manually",
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    raise IrreversibleOperationError(
        "Cannot roll back an in-place restore — the original instance state was overwritten"
    )
```

- [ ] **Step 6: Create stub restore strategies**

Create `import_image.py`, `file_restore_to_path.py`, `database_restore.py`, `storage_restore.py` — same stub pattern:

```python
# backend/app/connectors/executors/nexplane_agent/restore_strategies/import_image.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
_NAME = "import_image"


async def restore(params: dict, asset_ids: list, connector) -> dict:
    raise NotImplementedError(f"Restore strategy '{_NAME}' is not yet implemented")


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    raise NotImplementedError(f"Restore strategy '{_NAME}' is not yet implemented")
```

Repeat for `database_restore.py`, `storage_restore.py`. For `file_restore_to_path.py`, change `_NAME = "file_restore_to_path"` — the full implementation comes in Task 5.

- [ ] **Step 7: Rewrite `restore_server.py` as thin dispatcher**

```python
# backend/app/connectors/executors/nexplane_agent/restore_server.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""restore_server executor — thin dispatcher to restore strategy modules."""
import logging

logger = logging.getLogger(__name__)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.restore_strategies import get_strategy

    if not asset_ids:
        raise RuntimeError("restore_server: no asset_ids provided")

    target = parameters.get("target", {})
    # in_place uses confirm_same_target flag; all others default to launch_ami
    if target.get("type") == "same":
        restore_strategy = "in_place"
    else:
        restore_strategy = parameters.get("restore_strategy", "launch_ami")

    strategy = get_strategy(restore_strategy)
    return await strategy.restore(parameters, asset_ids, connector)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.restore_strategies import get_strategy

    restore_strategy = (
        execution_result.get("artifact_refs", {}).get("restore_strategy")
        or parameters.get("restore_strategy")
        or ("in_place" if parameters.get("confirm_same_target") else "launch_ami")
    )
    strategy = get_strategy(restore_strategy)
    return await strategy.rollback(parameters, execution_result, connector)
```

- [ ] **Step 8: Run all tests**

```bash
docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/test_backup_architecture.py -v 2>&1 | tail -30"
```
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/restore_strategies/ \
        backend/app/connectors/executors/nexplane_agent/restore_server.py \
        backend/app/tests/test_backup_architecture.py
git commit -m "feat: restore strategy dispatcher — launch_ami + in_place extracted, 4 stubs, restore_server thinned"
```

---

## Task 4: Data Model Migration

**Files:**
- Modify: `backend/app/models/backup_target.py`
- Create: `backend/alembic/versions/backup001_backup_target_strategy_fields.py`
- Test: `backend/app/tests/test_backup_architecture.py` (extend with migration smoke check)

**Interfaces:**
- Produces: `BackupTarget.backup_tier: str` (default `"machine"`)
- Produces: `BackupTarget.capture_strategy: str` (default `"ebs_snapshot"`)

- [ ] **Step 1: Add a test for the new columns**

Append to `backend/app/tests/test_backup_architecture.py`:

```python
class TestBackupTargetModel:
    def test_backup_target_has_backup_tier_column(self):
        from app.models.backup_target import BackupTarget
        import sqlalchemy.inspection as insp
        cols = {c.name for c in BackupTarget.__table__.columns}
        assert "backup_tier" in cols, "BackupTarget missing backup_tier column"
        assert "capture_strategy" in cols, "BackupTarget missing capture_strategy column"

    def test_backup_target_defaults(self):
        from app.models.backup_target import BackupTarget
        target = BackupTarget(
            organization_id="00000000-0000-0000-0000-000000000001",
            target_description="test",
        )
        assert target.backup_tier == "machine"
        assert target.capture_strategy == "ebs_snapshot"
```

- [ ] **Step 2: Run to verify it fails**

```bash
docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/test_backup_architecture.py::TestBackupTargetModel -v 2>&1 | tail -20"
```
Expected: `AssertionError` — columns don't exist yet.

- [ ] **Step 3: Add columns to `BackupTarget` model**

In `backend/app/models/backup_target.py`, add after the existing `asset_type` column:

```python
    # "machine" | "data" — determines valid capture/restore strategy pairs
    backup_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="machine", server_default="machine")
    # e.g. "ebs_snapshot" | "local_files" | "mgn_replication" | etc.
    capture_strategy: Mapped[str] = mapped_column(String(50), nullable=False, default="ebs_snapshot", server_default="ebs_snapshot")
```

- [ ] **Step 4: Create alembic migration**

```python
# backend/alembic/versions/backup001_backup_target_strategy_fields.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Add backup_tier and capture_strategy to backup_targets

Revision ID: backup001
Revises: tunnel002
Create Date: 2026-07-03
"""
from typing import Union
import sqlalchemy as sa
from alembic import op

revision: str = "backup001"
down_revision: Union[str, None] = "tunnel002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "backup_targets",
        sa.Column("backup_tier", sa.String(20), nullable=False, server_default="machine"),
    )
    op.add_column(
        "backup_targets",
        sa.Column("capture_strategy", sa.String(50), nullable=False, server_default="ebs_snapshot"),
    )


def downgrade() -> None:
    op.drop_column("backup_targets", "capture_strategy")
    op.drop_column("backup_targets", "backup_tier")
```

- [ ] **Step 5: SCP migration file to EC2 and run the migration**

From laptop — SCP the new model and migration to EC2:
```bash
scp -i ~/.ssh/id_ed25519 \
    backend/app/models/backup_target.py \
    backend/alembic/versions/backup001_backup_target_strategy_fields.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/models/backup_target.py
# (SCP each file individually to the right path)
```

Then on EC2 via `docker compose exec`:
```bash
docker compose exec -T backend alembic upgrade backup001
```
Expected output: `Running upgrade tunnel002 -> backup001, Add backup_tier and capture_strategy to backup_targets`

- [ ] **Step 6: Run tests**

```bash
docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/test_backup_architecture.py::TestBackupTargetModel -v 2>&1 | tail -20"
```
Expected: `2 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/backup_target.py \
        backend/alembic/versions/backup001_backup_target_strategy_fields.py \
        backend/app/tests/test_backup_architecture.py
git commit -m "feat: add backup_tier and capture_strategy columns to backup_targets (migration backup001)"
```

---

## Task 5: local_files Data Tier Strategy

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/backup_strategies/local_files.py` (replace stub)
- Modify: `backend/app/connectors/executors/nexplane_agent/restore_strategies/file_restore_to_path.py` (replace stub)
- Test: `backend/app/tests/test_backup_architecture.py` (extend)

**Interfaces:**
- Consumes: `storage_backends.get_backend(storage_type)` from Task 1
- Produces: `artifact_refs.capture_strategy == "local_files"`, `artifact_refs.restore_strategy == "file_restore_to_path"`

The `local_files` strategy connects to the target via SSH (using `paramiko`), runs `tar czf - <source_path>` remotely, streams the output to a temp file, then uploads to the configured storage backend. Credentials come from `connector.credentials` (an SSH connector with `host`, `username`, `private_key` or `password`).

- [ ] **Step 1: Add tests for local_files (mocked SSH)**

Append to `backend/app/tests/test_backup_architecture.py`:

```python
class TestLocalFilesStrategy:
    def test_local_files_backup_produces_correct_artifact_refs(self):
        import asyncio
        from unittest.mock import MagicMock, patch, AsyncMock
        from app.connectors.executors.nexplane_agent.backup_strategies import local_files

        mock_ssh = MagicMock()
        mock_channel = MagicMock()
        mock_channel.recv.side_effect = [b"fake tar data", b""]
        mock_channel.recv_exit_status.return_value = 0
        mock_ssh.get_transport.return_value.open_session.return_value = mock_channel
        mock_channel.makefile.return_value.__iter__ = lambda s: iter([b"fake tar data"])

        mock_backend = AsyncMock()
        mock_backend.put_file.return_value = "s3://bucket/prefix/archive.tar.gz"

        with patch("paramiko.SSHClient", return_value=mock_ssh), \
             patch("app.connectors.executors.nexplane_agent.storage_backends.get_backend",
                   return_value=mock_backend):
            mock_connector = MagicMock()
            mock_connector.credentials = {
                "host": "10.0.0.1",
                "username": "ec2-user",
                "private_key": "fake-key",
            }
            result = asyncio.get_event_loop().run_until_complete(
                local_files.backup(
                    {
                        "source_path": "/var/app/data",
                        "backup_storage_id": "fake-storage-id",
                        "_storage_config": {"storage_type": "s3", "config": {"bucket": "b", "prefix": "p/"}},
                    },
                    ["asset-uuid"],
                    mock_connector,
                )
            )
        refs = result["artifact_refs"]
        assert refs["capture_strategy"] == "local_files"
        assert refs["restore_strategy"] == "file_restore_to_path"
        assert refs["backup_tier"] == "data"
        assert "artifact_uri" in refs

    def test_local_files_rollback_deletes_artifact(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.connectors.executors.nexplane_agent.backup_strategies import local_files

        mock_backend = AsyncMock()
        mock_backend.delete.return_value = None

        with patch("app.connectors.executors.nexplane_agent.storage_backends.get_backend",
                   return_value=mock_backend):
            result = asyncio.get_event_loop().run_until_complete(
                local_files.rollback(
                    {},
                    {
                        "artifact_refs": {
                            "storage_type": "s3",
                            "artifact_uri": "s3://bucket/key",
                            "config": {"bucket": "bucket"},
                        }
                    },
                    None,
                )
            )
        assert result["rolled_back"] is True
        mock_backend.delete.assert_called_once_with("s3://bucket/key", {"bucket": "bucket"})
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/test_backup_architecture.py::TestLocalFilesStrategy -v 2>&1 | tail -20"
```
Expected: `NotImplementedError` (still a stub).

- [ ] **Step 3: Implement `backup_strategies/local_files.py`**

```python
# backend/app/connectors/executors/nexplane_agent/backup_strategies/local_files.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: rsync/tar of a local filesystem path via SSH. Data tier."""
import asyncio
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _ssh_connect(creds: dict):
    import paramiko, io
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = {
        "hostname": creds["host"],
        "username": creds.get("username", "ec2-user"),
        "port": int(creds.get("port", 22)),
        "timeout": 30,
    }
    if creds.get("private_key"):
        connect_kwargs["pkey"] = paramiko.RSAKey.from_private_key(
            io.StringIO(creds["private_key"])
        )
    elif creds.get("password"):
        connect_kwargs["password"] = creds["password"]
    client.connect(**connect_kwargs)
    return client


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.backup_strategies import _load_storage_config
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    source_path = params.get("source_path", "")
    if not source_path:
        raise RuntimeError("local_files: source_path is required")

    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("host"):
        raise RuntimeError("local_files: connector must have SSH credentials (host, username, private_key/password)")

    storage_config = params.get("_storage_config") or await _load_storage_config(
        params["backup_storage_id"]
    )
    cfg = storage_config.get("config", {})
    storage_type = storage_config["storage_type"]
    prefix = cfg.get("prefix", "backups/")
    captured_at = datetime.now(timezone.utc).isoformat()
    asset_id = str(asset_ids[0]) if asset_ids else "unknown"
    archive_key = f"{prefix}{asset_id}/{captured_at.replace(':', '-')}.tar.gz"

    def _sync_tar_and_upload():
        import shutil
        ssh = _ssh_connect(creds)
        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp_path = tmp.name

            # Stream tar from remote to local tmp file
            cmd = f"tar czf - --warning=no-file-changed {source_path}"
            stdin, stdout, stderr = ssh.exec_command(cmd)
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = stdout.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            exit_code = stdout.channel.recv_exit_status()
            # tar exits 1 on "file changed as we read it" — treat as success
            if exit_code not in (0, 1):
                err = stderr.read(2048).decode(errors="replace")
                raise RuntimeError(f"local_files: tar failed (exit={exit_code}): {err}")
            return tmp_path
        finally:
            ssh.close()

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        tmp_path = await loop.run_in_executor(pool, _sync_tar_and_upload)

    try:
        backend = get_backend(storage_type)
        artifact_uri = await backend.put_file(archive_key, tmp_path, cfg)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    artifact_refs = {
        "capture_strategy": "local_files",
        "restore_strategy": "file_restore_to_path",
        "backup_tier": "data",
        "captured_at": captured_at,
        "storage_type": storage_type,
        "config": cfg,
        "artifact_uri": artifact_uri,
        "source_path": source_path,
    }
    return {
        "status": "completed",
        "artifact_refs": artifact_refs,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    artifact_refs = execution_result.get("artifact_refs", {})
    storage_type = artifact_refs.get("storage_type", "s3")
    artifact_uri = artifact_refs.get("artifact_uri", "")
    cfg = artifact_refs.get("config", {})

    if not artifact_uri:
        return {"rolled_back": False, "reason": "no artifact_uri in artifact_refs"}

    backend = get_backend(storage_type)
    await backend.delete(artifact_uri, cfg)
    return {"rolled_back": True, "deleted_artifact_uri": artifact_uri}
```

- [ ] **Step 4: Implement `restore_strategies/file_restore_to_path.py`**

```python
# backend/app/connectors/executors/nexplane_agent/restore_strategies/file_restore_to_path.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: download tar from storage backend, extract to remote path via SSH. Data tier."""
import asyncio
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _ssh_connect(creds: dict):
    import paramiko, io
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = {
        "hostname": creds["host"],
        "username": creds.get("username", "ec2-user"),
        "port": int(creds.get("port", 22)),
        "timeout": 30,
    }
    if creds.get("private_key"):
        connect_kwargs["pkey"] = paramiko.RSAKey.from_private_key(
            io.StringIO(creds["private_key"])
        )
    elif creds.get("password"):
        connect_kwargs["password"] = creds["password"]
    client.connect(**connect_kwargs)
    return client


async def restore(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.restore_strategies.launch_ami import _load_source_artifact_refs
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    source_backup_cr_id = params.get("source_backup_cr_id", "")
    if not source_backup_cr_id:
        raise RuntimeError("file_restore_to_path: source_backup_cr_id is required")

    target_path = params.get("target_path", "")
    if not target_path:
        raise RuntimeError("file_restore_to_path: target_path is required")

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)
    artifact_uri = artifact_refs.get("artifact_uri", "")
    storage_type = artifact_refs.get("storage_type", "s3")
    cfg = artifact_refs.get("config", {})

    if not artifact_uri:
        raise RuntimeError(f"file_restore_to_path: no artifact_uri in source CR {source_backup_cr_id}")

    creds = getattr(connector, "credentials", {}) or {}

    # Download artifact to tmp file
    backend = get_backend(storage_type)
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await backend.get_file(artifact_uri, tmp_path, cfg)

        def _sync_extract():
            ssh = _ssh_connect(creds)
            try:
                # Ensure target dir exists, then stream tar extract
                ssh.exec_command(f"mkdir -p {target_path}")[1].channel.recv_exit_status()
                with open(tmp_path, "rb") as f:
                    sftp = ssh.open_sftp()
                    remote_tmp = f"/tmp/nexplane-restore-{os.path.basename(tmp_path)}"
                    sftp.putfo(f, remote_tmp)
                    sftp.close()
                cmd = f"tar xzf {remote_tmp} -C {target_path} --strip-components=0 && rm {remote_tmp}"
                _, stdout, stderr = ssh.exec_command(cmd)
                exit_code = stdout.channel.recv_exit_status()
                if exit_code != 0:
                    err = stderr.read(2048).decode(errors="replace")
                    raise RuntimeError(f"file_restore_to_path: tar extract failed (exit={exit_code}): {err}")
            finally:
                ssh.close()

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as pool:
            await loop.run_in_executor(pool, _sync_extract)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

    return {
        "status": "completed",
        "restore_strategy": "file_restore_to_path",
        "artifact_uri": artifact_uri,
        "target_path": target_path,
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    # File extraction into a directory is not automatically reversible.
    # The caller should remove or overwrite the restored files manually.
    return {
        "rolled_back": False,
        "reason": "file_restore_to_path rollback is not automatic — remove restored files from target_path manually",
    }
```

Note: `backend.get_file` is needed — add it to `storage_backends/s3.py`:

```python
async def get_file(uri: str, local_path: str, config: dict) -> None:
    """Download an S3 object to a local file."""
    parts = uri.replace("s3://", "").split("/", 1)
    bucket, key = parts[0], parts[1] if len(parts) > 1 else ""

    def _sync():
        s3 = _client(config)
        s3.download_file(bucket, key, local_path)

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync)
```

Also add stub `get_file` to all other storage backend stubs (same `NotImplementedError` pattern).

- [ ] **Step 5: Run tests**

```bash
docker compose exec -T backend bash -c "PYTHONPATH=/app pytest app/tests/test_backup_architecture.py -v 2>&1 | tail -30"
```
Expected: all tests pass (SSH is mocked in `TestLocalFilesStrategy`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/backup_strategies/local_files.py \
        backend/app/connectors/executors/nexplane_agent/restore_strategies/file_restore_to_path.py \
        backend/app/connectors/executors/nexplane_agent/storage_backends/s3.py \
        backend/app/tests/test_backup_architecture.py
git commit -m "feat: local_files backup strategy + file_restore_to_path restore (SSH/tar/paramiko)"
```

---

## Task 6: Smoke Test — B0 SSM Sentinel + B4 Fix

**Files:**
- Modify: `backend/tests/smoke/test_backup_scheduler_live.py`

This task modifies `run_phase_backup_scheduler` in the smoke file. No new files — patch the existing function.

**B0** writes a UUID sentinel to the source instance via boto3 SSM before any backup runs. The UUID is stored in phase state and verified in Task 7's R2.

**B4 fix** removes the graceful skip on 404/500 and instead manually approves the CR if it lands in `awaiting_approval`.

- [ ] **Step 1: Add SSM sentinel write (B0) before the B1 block**

In `run_phase_backup_scheduler`, insert this block before the `# B1` comment:

```python
        # ------------------------------------------------------------------ #
        # B0 - SSM sentinel: write UUID to source instance before any backup
        # ------------------------------------------------------------------ #
        print("\n  B0: writing SSM sentinel to source instance...")
        import uuid as _uuid_mod
        sentinel_uuid = str(_uuid_mod.uuid4())
        sentinel_path = f"/home/ec2-user/nexplane-smoke-marker-{sentinel_uuid}"
        ssm = _get_aws_boto3_client("ssm")
        if not ssm:
            fail("B0: could not get SSM boto3 client")
        b0_cmd = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [f'echo "{sentinel_uuid}" > {sentinel_path}']},
        )
        b0_cmd_id = b0_cmd["Command"]["CommandId"]
        b0_deadline = time.time() + 60
        b0_inv = None
        while time.time() < b0_deadline:
            try:
                b0_inv = ssm.get_command_invocation(CommandId=b0_cmd_id, InstanceId=instance_id)
                if b0_inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
                    break
            except Exception:
                pass
            time.sleep(3)
        if not b0_inv or b0_inv["Status"] != "Success":
            fail(f"B0: SSM sentinel write failed: status={b0_inv['Status'] if b0_inv else 'timeout'}")
        print(f"  B0 PASSED: sentinel_uuid={sentinel_uuid}")
```

- [ ] **Step 2: Fix B4 — remove graceful skip, add mandatory approval**

Replace the entire B4 block (lines 336–393 in the original) with:

```python
        # ------------------------------------------------------------------ #
        # B4 - scheduled backup via RecurringJob (run-now, mandatory approve)
        # ------------------------------------------------------------------ #
        print("  B4: scheduled backup via RecurringJob...")
        import requests as _rn_req
        job = client.post("/recurring-jobs", json={
            "name": f"smoke-backup-{run_ts}",
            "job_type": "backup",
            "action_id": "server_backup",
            "target_description": f"smoke instance {instance_id}",
            "cron_expression": "0 3 * * *",
            "parameters": {
                "change_type": "server_backup",
                "asset_id": asset_id,
                "aws_connector_id": aws_connector_id,
                "backup_storage_id": backup_storage_id,
                "instance_id": instance_id,
            },
        })
        job_data = job if isinstance(job, dict) else job.json()
        job_id = job_data["id"]

        _rn_auth = client.client.headers.get("Authorization", "")
        base = (getattr(client, "base_url", None) or getattr(client, "base", "")).rstrip("/")
        rn_resp = _rn_req.post(
            f"{base}/recurring-jobs/{job_id}/run-now",
            headers={"Authorization": _rn_auth},
        )
        if rn_resp.status_code not in (200, 201, 202):
            fail(f"B4: run-now failed with {rn_resp.status_code}: {rn_resp.text}")

        # If the CR landed in awaiting_approval (no RecurringJobPolicy), approve it
        rn_data = rn_resp.json() if rn_resp.text.strip() else {}
        rn_cr_id = rn_data.get("id") or rn_data.get("change_request_id") or rn_data.get("cr_id")
        if rn_cr_id:
            cr_check = _rn_req.get(f"{base}/change-requests/{rn_cr_id}", headers={"Authorization": _rn_auth})
            if cr_check.status_code == 200:
                cr_status = cr_check.json().get("status", "")
                if cr_status == "awaiting_approval":
                    approve = _rn_req.post(
                        f"{base}/change-requests/{rn_cr_id}/approve",
                        json={"decision": "approved", "comment": "smoke auto-approve"},
                        headers={"Authorization": _rn_auth},
                    )
                    assert approve.status_code == 200, f"B4: approve failed {approve.status_code}: {approve.text}"
                    _execute_cr(client, rn_cr_id)

        # Poll backup-history for a new completed entry against this asset
        scheduled_cr_id = None
        known_cr_ids = {b1_cr_id, b2_cr_id, b3_cr_id}
        deadline = time.time() + 300
        while time.time() < deadline:
            history = client.get("/backup-history", params={"limit": 20})
            if not isinstance(history, list):
                history = history.json() if hasattr(history, "json") else []
            for entry in history:
                if (entry.get("status") == "completed"
                        and asset_id in entry.get("target_asset_ids", [])
                        and entry.get("artifact_refs")
                        and entry["id"] not in known_cr_ids):
                    scheduled_cr_id = entry["id"]
                    break
            if scheduled_cr_id:
                break
            time.sleep(5)
        assert scheduled_cr_id, "B4: scheduled backup CR did not appear in /backup-history within 300s"
        print(f"  B4 PASSED: scheduled_cr_id={scheduled_cr_id}")

        # Clean up the recurring job
        client.delete(f"/recurring-jobs/{job_id}")
```

- [ ] **Step 3: SCP the modified smoke file to EC2 and run only the BACKUP_SCHEDULER phase (B0 + B4 focus)**

From laptop:
```bash
scp -i ~/.ssh/id_ed25519 backend/tests/smoke/test_backup_scheduler_live.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/test_backup_scheduler_live.py
```

On EC2, run just the B0–B4 logic by using a test run (you can temporarily add a `sys.exit(0)` after B4 in the smoke file, run, then remove it — or just run the full phase and verify B0+B4 logs appear correctly):
```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd nexplane && docker compose exec -T backend python tests/smoke/test_backup_scheduler_live.py \
    --email admin@acme.example --password admin123 --phases BACKUP_SCHEDULER 2>&1 | head -80"
```
Expected: `B0 PASSED: sentinel_uuid=...` and `B4 PASSED: scheduled_cr_id=...` in the output.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_backup_scheduler_live.py
git commit -m "feat: smoke B0 SSM sentinel write + B4 mandatory approval (remove graceful skip)"
```

---

## Task 7: Smoke R2 SSM Data Verification + Full Run

**Files:**
- Modify: `backend/tests/smoke/test_backup_scheduler_live.py`

This task enhances the R2 restore phase with SSM polling and sentinel UUID verification. The `sentinel_uuid` and `sentinel_path` variables set in B0 (Task 6) are in scope within `run_phase_backup_scheduler`.

- [ ] **Step 1: Replace R2 restore assertion block with SSM verification**

In `run_phase_backup_scheduler`, after `assert new_instance_id, ...` (the existing assertion that the new instance exists) but before `new_instance_ids_to_cleanup.append(new_instance_id)`, replace the state check block with:

```python
        new_instance_ids_to_cleanup.append(new_instance_id)
        # Wait for SSM agent on the restored instance
        print(f"  R2: polling SSM reachability on {new_instance_id}...")
        ssm_ready = False
        ssm_deadline = time.time() + 120
        while time.time() < ssm_deadline:
            try:
                info = ssm.describe_instance_information(
                    Filters=[{"Key": "InstanceIds", "Values": [new_instance_id]}]
                )
                if info.get("InstanceInformationList"):
                    ssm_ready = True
                    break
            except Exception:
                pass
            time.sleep(5)
        if not ssm_ready:
            fail(f"R2: SSM not reachable on restored instance {new_instance_id} within 120s")

        # Verify sentinel UUID on restored instance
        verify_resp = ssm.send_command(
            InstanceIds=[new_instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [f"cat {sentinel_path}"]},
        )
        verify_cmd_id = verify_resp["Command"]["CommandId"]
        verify_inv = None
        verify_deadline = time.time() + 60
        while time.time() < verify_deadline:
            try:
                verify_inv = ssm.get_command_invocation(
                    CommandId=verify_cmd_id, InstanceId=new_instance_id
                )
                if verify_inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
                    break
            except Exception:
                pass
            time.sleep(3)
        if not verify_inv or verify_inv["Status"] != "Success":
            fail(f"R2: SSM sentinel read failed on restored instance: {verify_inv}")
        restored_output = verify_inv.get("StandardOutputContent", "").strip()
        if sentinel_uuid not in restored_output:
            fail(
                f"R2: sentinel UUID mismatch — expected '{sentinel_uuid}' "
                f"in output of restored instance, got: '{restored_output}'"
            )
        print(f"  R2 PASSED: sentinel_uuid verified on restored instance {new_instance_id}")
```

Remove the old state check:
```python
# REMOVE THIS:
new_inst = ec2.describe_instances(InstanceIds=[new_instance_id])
state = new_inst["Reservations"][0]["Instances"][0]["State"]["Name"]
assert state in ("running", "pending"), f"R2: new instance state={state}"
print(f"  R2 PASSED: new_instance_id={new_instance_id}, state={state}")
```

- [ ] **Step 2: SCP the final smoke file to EC2**

```bash
scp -i ~/.ssh/id_ed25519 backend/tests/smoke/test_backup_scheduler_live.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/test_backup_scheduler_live.py
```

- [ ] **Step 3: Run the full BACKUP_SCHEDULER phase on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd nexplane && docker compose exec -T backend python tests/smoke/test_backup_scheduler_live.py \
    --email admin@acme.example --password admin123 --phases BACKUP_SCHEDULER 2>&1"
```

Expected output (all phases pass):
```
  B0 PASSED: sentinel_uuid=<uuid>
  B1 PASSED: snapshot_ids=[...]
  B2 PASSED: ami_id=ami-..., application_sequence=...
  B3 PASSED: ami_id=ami-..., s3_objects=...
  B4 PASSED: scheduled_cr_id=...
  R2 PASSED: sentinel_uuid verified on restored instance i-...
  R2 rollback PASSED
  FILO PASSED: blocked rollback of B1, blocking_crs=[...]
  B3 rollback PASSED
  B2 rollback PASSED
  B1 rollback PASSED

  BACKUP_SCHEDULER_SMOKE: ALL ASSERTIONS PASSED
```

- [ ] **Step 4: Record results and update progress ledger**

Append to `f:\Nexplane\nexplane\.superpowers\sdd\progress.md`:
```
## Backup & Restore Architecture
- Task 1: complete (commits <base>..<head>, review clean)
- Task 2: complete (commits ..., review clean)
...
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_backup_scheduler_live.py
git commit -m "feat: smoke R2 SSM data-proof verification — sentinel UUID confirmed on restored instance"
```
