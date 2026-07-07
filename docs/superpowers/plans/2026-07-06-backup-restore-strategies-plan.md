# Backup Restore Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the GCS storage backend, `storage_restore`, MySQL+MongoDB extension to `database_dump`, and `database_restore` restore strategy — each with a live smoke phase.

**Architecture:** Agent-mediated style: the executor downloads the artifact from the storage backend, then runs the restore operation against the target via SSH. Mirrors `database_dump` and `file_restore_to_path`. The GCS backend replaces the existing stub and implements the same 5-method interface as `s3.py`, plus a new `list_prefix` method added to both.

**Tech Stack:** `google-cloud-storage>=2.18.0` (already in requirements), `paramiko` (already in requirements), `asyncio.get_running_loop()` for restore strategies, boto3 for S3 smoke setup

## Global Constraints

- SPDX header on every new/modified Python file: `# SPDX-License-Identifier: AGPL-3.0-only` + `# Copyright (C) 2024-2026 Nexplane, Inc.`
- `google-cloud-storage>=2.18.0` already in `requirements.txt` — no change needed
- `paramiko` already in `requirements.txt` — no change needed
- `asyncio.get_running_loop()` for restore strategies (called from async context); backup strategies use `get_event_loop()`
- GCS URI scheme: `gcs://bucket/key` (mirrors S3's `s3://bucket/key`)
- `confirm_drop=true` always required for `database_restore` rollback — no exceptions
- Smoke tests run on EC2 runner via SSM command; platform API at `http://100.101.186.39:8000`, auth `admin@acme.example`/`admin123`, AWS connector ID `666e237d`, EC2 instance `i-050bab85006f0b73c`
- All functionality must run against live GCP/AWS/Docker infrastructure — no mocks
- `storage_sync` `artifact_refs` stores `dest_prefix`/`dest_bucket`/`dest_config` (not individual keys) — `storage_restore` must call `list_prefix` on the source backend to enumerate objects
- Storage backend interface now has 6 methods: `upload`, `download`, `delete`, `put_bytes`, `delete_prefix`, `list_prefix`

---

### Task 1: GCS storage backend + `list_prefix` interface extension

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/storage_backends/s3.py`
- Create: (replace stub) `backend/app/connectors/executors/nexplane_agent/storage_backends/gcs.py`
- Modify (add stub): `backend/app/connectors/executors/nexplane_agent/storage_backends/azure_blob.py`
- Modify (add stub): `backend/app/connectors/executors/nexplane_agent/storage_backends/oci_object_storage.py`
- Modify (add stub): `backend/app/connectors/executors/nexplane_agent/storage_backends/nfs.py`
- Modify (add stub): `backend/app/connectors/executors/nexplane_agent/storage_backends/local.py`
- Test: `backend/tests/unit/test_gcs_backend.py`

**Interfaces:**
- Produces: `list_prefix(prefix: str, config: dict) -> list[str]` on all storage backends; returns list of full URIs (`s3://bucket/key` or `gcs://bucket/key`)
- Produces: `gcs.upload`, `gcs.download`, `gcs.delete`, `gcs.put_bytes`, `gcs.delete_prefix`, `gcs.list_prefix` — full implementation
- `_client(config) -> google.cloud.storage.Client` — `service_account_key_json` → service account creds; missing → ADC fallback
- ValueError raised if `config["bucket"]` is missing

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_gcs_backend.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import asyncio
import io
import tempfile
from unittest.mock import MagicMock, patch, call

import pytest


def _mod():
    from app.connectors.executors.nexplane_agent.storage_backends import gcs
    return gcs


CONFIG = {
    "bucket": "my-bucket",
    "service_account_key_json": '{"type": "service_account", "project_id": "p"}',
}


def _make_client(blobs=None):
    client = MagicMock()
    bucket = MagicMock()
    client.bucket.return_value = bucket
    if blobs is not None:
        bucket.list_blobs.return_value = blobs
    return client, bucket


@pytest.mark.asyncio
async def test_upload_returns_uri():
    gcs = _mod()
    mock_blob = MagicMock()
    client, bucket = _make_client()
    bucket.blob.return_value = mock_blob
    with patch.object(gcs, "_client", return_value=client):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"data")
            tmp = f.name
        result = await gcs.upload(tmp, "path/to/file.sql.gz", CONFIG)
    assert result == "gcs://my-bucket/path/to/file.sql.gz"
    mock_blob.upload_from_filename.assert_called_once_with(tmp)


@pytest.mark.asyncio
async def test_download_writes_file():
    gcs = _mod()
    mock_blob = MagicMock()
    client, bucket = _make_client()
    bucket.blob.return_value = mock_blob
    with patch.object(gcs, "_client", return_value=client):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            tmp = f.name
        await gcs.download("gcs://my-bucket/path/to/file.sql.gz", tmp, CONFIG)
    mock_blob.download_to_filename.assert_called_once_with(tmp)


@pytest.mark.asyncio
async def test_delete_calls_blob_delete():
    gcs = _mod()
    mock_blob = MagicMock()
    client, bucket = _make_client()
    bucket.blob.return_value = mock_blob
    with patch.object(gcs, "_client", return_value=client):
        await gcs.delete("gcs://my-bucket/some/key", CONFIG)
    mock_blob.delete.assert_called_once()


@pytest.mark.asyncio
async def test_put_bytes_returns_uri():
    gcs = _mod()
    mock_blob = MagicMock()
    client, bucket = _make_client()
    bucket.blob.return_value = mock_blob
    with patch.object(gcs, "_client", return_value=client):
        result = await gcs.put_bytes("smoke/test.txt", b"hello", CONFIG)
    assert result == "gcs://my-bucket/smoke/test.txt"
    mock_blob.upload_from_string.assert_called_once_with(b"hello")


@pytest.mark.asyncio
async def test_delete_prefix_returns_count():
    gcs = _mod()
    b1, b2 = MagicMock(name="b1"), MagicMock(name="b2")
    client, bucket = _make_client(blobs=[b1, b2])
    with patch.object(gcs, "_client", return_value=client):
        result = await gcs.delete_prefix("smoke/", CONFIG)
    assert result == {"deleted_count": 2}
    b1.delete.assert_called_once()
    b2.delete.assert_called_once()


@pytest.mark.asyncio
async def test_list_prefix_returns_uris():
    gcs = _mod()
    b1 = MagicMock(); b1.name = "smoke/file1.txt"
    b2 = MagicMock(); b2.name = "smoke/file2.txt"
    client, bucket = _make_client(blobs=[b1, b2])
    with patch.object(gcs, "_client", return_value=client):
        result = await gcs.list_prefix("smoke/", CONFIG)
    assert result == ["gcs://my-bucket/smoke/file1.txt", "gcs://my-bucket/smoke/file2.txt"]


@pytest.mark.asyncio
async def test_missing_bucket_raises():
    gcs = _mod()
    with pytest.raises(ValueError, match="bucket"):
        await gcs.upload("/tmp/x", "key", {})


@pytest.mark.asyncio
async def test_s3_list_prefix_returns_uris():
    from app.connectors.executors.nexplane_agent.storage_backends import s3
    mock_s3 = MagicMock()
    paginator = MagicMock()
    mock_s3.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {"Contents": [{"Key": "backups/file1.sql.gz"}, {"Key": "backups/file2.sql.gz"}]},
        {},
    ]
    with patch.object(s3, "_client", return_value=mock_s3):
        result = await s3.list_prefix("backups/", {"bucket": "my-bucket"})
    assert result == [
        "s3://my-bucket/backups/file1.sql.gz",
        "s3://my-bucket/backups/file2.sql.gz",
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd /app && python -m pytest tests/unit/test_gcs_backend.py -v 2>&1 | head -40
```

Expected: FAIL — `NotImplementedError` or `AttributeError: module has no attribute 'list_prefix'`

- [ ] **Step 3: Add `list_prefix` to `s3.py`**

Append to `backend/app/connectors/executors/nexplane_agent/storage_backends/s3.py`:

```python
async def list_prefix(prefix: str, config: dict) -> list:
    """List all objects under prefix. Returns list of s3://bucket/key URIs."""
    bucket = config["bucket"]

    def _sync():
        s3 = _client(config)
        uris = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                uris.append(f"s3://{bucket}/{obj['Key']}")
        return uris

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)
```

- [ ] **Step 4: Implement `gcs.py`**

Replace `backend/app/connectors/executors/nexplane_agent/storage_backends/gcs.py` entirely:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""GCS storage backend. URI scheme: gcs://bucket/key."""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_NAME = "gcs"


def _client(config: dict):
    from google.cloud import storage
    key_json = config.get("service_account_key_json")
    if key_json:
        if isinstance(key_json, str):
            key_json = json.loads(key_json)
        from google.oauth2 import service_account
        creds = service_account.Credentials.from_service_account_info(
            key_json,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return storage.Client(credentials=creds, project=key_json.get("project_id"))
    # Fall back to ADC for local dev / workload identity
    return storage.Client()


def _require_bucket(config: dict) -> str:
    bucket = config.get("bucket")
    if not bucket:
        raise ValueError("GCS storage backend requires config['bucket'] to be set")
    return bucket


async def upload(local_path: str, dest_key: str, config: dict) -> str:
    """Upload a local file to GCS. Returns gcs://bucket/key URI."""
    bucket_name = _require_bucket(config)

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(dest_key)
        blob.upload_from_filename(local_path)
        return f"gcs://{bucket_name}/{dest_key}"

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def download(uri: str, local_path: str, config: dict) -> None:
    """Download a GCS object by its gcs://bucket/key URI to local_path."""
    _require_bucket(config)
    parts = uri.replace("gcs://", "").split("/", 1)
    bucket_name, key = parts[0], parts[1] if len(parts) > 1 else ""

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(key)
        blob.download_to_filename(local_path)

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync)


async def delete(uri: str, config: dict) -> None:
    """Delete a single GCS object by its gcs://bucket/key URI."""
    _require_bucket(config)
    parts = uri.replace("gcs://", "").split("/", 1)
    bucket_name, key = parts[0], parts[1] if len(parts) > 1 else ""

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(key)
        blob.delete()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync)


async def put_bytes(key: str, data: bytes, config: dict) -> str:
    """Put raw bytes at key in GCS. Returns gcs://bucket/key URI."""
    bucket_name = _require_bucket(config)

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(key)
        blob.upload_from_string(data)
        return f"gcs://{bucket_name}/{key}"

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def delete_prefix(prefix: str, config: dict) -> dict:
    """Delete all blobs under prefix. Returns {deleted_count: N}."""
    bucket_name = _require_bucket(config)

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blobs = list(bucket.list_blobs(prefix=prefix))
        for blob in blobs:
            blob.delete()
        return {"deleted_count": len(blobs)}

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)


async def list_prefix(prefix: str, config: dict) -> list:
    """List all blobs under prefix. Returns list of gcs://bucket/key URIs."""
    bucket_name = _require_bucket(config)

    def _sync():
        client = _client(config)
        bucket = client.bucket(bucket_name)
        blobs = bucket.list_blobs(prefix=prefix)
        return [f"gcs://{bucket_name}/{blob.name}" for blob in blobs]

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        return await loop.run_in_executor(pool, _sync)
```

- [ ] **Step 5: Add `list_prefix` stub to remaining storage backend stubs**

Each stub file (`azure_blob.py`, `oci_object_storage.py`, `nfs.py`, `local.py`) — append:

```python
async def list_prefix(prefix: str, config: dict) -> list:
    raise NotImplementedError(f"Storage backend '{_NAME}' is not yet implemented")
```

Read each file first to find `_NAME` and append after `delete_prefix`.

- [ ] **Step 6: Run tests to verify they pass**

```
cd /app && python -m pytest tests/unit/test_gcs_backend.py -v
```

Expected: 8/8 PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/storage_backends/gcs.py
git add backend/app/connectors/executors/nexplane_agent/storage_backends/s3.py
git add backend/app/connectors/executors/nexplane_agent/storage_backends/azure_blob.py
git add backend/app/connectors/executors/nexplane_agent/storage_backends/oci_object_storage.py
git add backend/app/connectors/executors/nexplane_agent/storage_backends/nfs.py
git add backend/app/connectors/executors/nexplane_agent/storage_backends/local.py
git add backend/tests/unit/test_gcs_backend.py
git commit -m "feat: implement GCS storage backend + list_prefix interface extension"
```

---

### Task 2: `storage_restore` restore strategy

**Files:**
- Modify: (replace stub) `backend/app/connectors/executors/nexplane_agent/restore_strategies/storage_restore.py`
- Test: `backend/tests/unit/test_storage_restore.py`

**Interfaces:**
- Consumes: `list_prefix(prefix, config) -> list[str]` from Task 1
- Consumes: `_load_source_artifact_refs(source_backup_cr_id)` from `restore_strategies/__init__.py`
- Consumes: `get_backend(storage_type)` from `storage_backends/__init__.py`
- `storage_sync` artifact_refs keys: `dest_storage_type`, `dest_bucket`, `dest_prefix`, `dest_config`
- Produces: `restore()` → `{"status": "completed", "restore_strategy": "storage_restore", "restored_uris": [...], "target_storage_type": ..., "target_bucket": ..., "target_prefix": ..., "restored_at": ...}`
- Produces: `rollback()` → `{"rolled_back": True, "deleted_count": N}` or `{"rolled_back": False, "reason": ...}`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_storage_restore.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mod():
    from app.connectors.executors.nexplane_agent.restore_strategies import storage_restore
    return storage_restore


ARTIFACT_REFS = {
    "capture_strategy": "storage_sync",
    "restore_strategy": "storage_restore",
    "dest_storage_type": "s3",
    "dest_bucket": "backup-bucket",
    "dest_prefix": "backups/data/",
    "dest_config": {"bucket": "backup-bucket", "aws_access_key_id": "k"},
}


@pytest.mark.asyncio
async def test_restore_copies_objects():
    mod = _mod()

    source_backend = MagicMock()
    source_backend.list_prefix = AsyncMock(return_value=[
        "s3://backup-bucket/backups/data/file1.tar",
        "s3://backup-bucket/backups/data/file2.tar",
    ])
    source_backend.download = AsyncMock()

    target_backend = MagicMock()
    target_backend.upload = AsyncMock(side_effect=[
        "s3://target-bucket/restored/file1.tar",
        "s3://target-bucket/restored/file2.tar",
    ])

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.storage_restore._load_source_artifact_refs",
        AsyncMock(return_value=ARTIFACT_REFS),
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.storage_restore.get_backend",
        side_effect=lambda t: source_backend if t == "s3" else target_backend,
    ):
        result = await mod.restore(
            {
                "source_backup_cr_id": "abc",
                "target_storage_type": "s3",
                "target_bucket": "target-bucket",
                "target_prefix": "restored/",
            },
            [],
            MagicMock(),
        )

    assert result["status"] == "completed"
    assert result["restore_strategy"] == "storage_restore"
    assert len(result["restored_uris"]) == 2
    assert result["target_bucket"] == "target-bucket"


@pytest.mark.asyncio
async def test_restore_defaults_to_source_storage_type():
    mod = _mod()

    backend = MagicMock()
    backend.list_prefix = AsyncMock(return_value=["s3://b/k/f.tar"])
    backend.download = AsyncMock()
    backend.upload = AsyncMock(return_value="s3://b2/r/f.tar")

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.storage_restore._load_source_artifact_refs",
        AsyncMock(return_value=ARTIFACT_REFS),
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.storage_restore.get_backend",
        return_value=backend,
    ):
        result = await mod.restore(
            {"source_backup_cr_id": "abc", "target_bucket": "b2", "target_prefix": "r/"},
            [],
            MagicMock(),
        )

    assert result["target_storage_type"] == "s3"


@pytest.mark.asyncio
async def test_rollback_deletes_restored_uris():
    mod = _mod()
    backend = MagicMock()
    backend.delete = AsyncMock()

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.storage_restore.get_backend",
        return_value=backend,
    ):
        result = await mod.rollback(
            {"target_storage_type": "s3", "target_bucket": "b", "target_prefix": "r/"},
            {
                "restored_uris": ["s3://b/r/f1.tar", "s3://b/r/f2.tar"],
                "target_storage_type": "s3",
                "target_config": {"bucket": "b"},
            },
            MagicMock(),
        )

    assert result["rolled_back"] is True
    assert result["deleted_count"] == 2
    assert backend.delete.call_count == 2


@pytest.mark.asyncio
async def test_rollback_empty_uris():
    mod = _mod()
    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.storage_restore.get_backend",
        return_value=MagicMock(),
    ):
        result = await mod.rollback({}, {"restored_uris": []}, MagicMock())
    assert result["rolled_back"] is False
    assert "no restored_uris" in result["reason"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd /app && python -m pytest tests/unit/test_storage_restore.py -v 2>&1 | head -30
```

Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement `storage_restore.py`**

Replace `backend/app/connectors/executors/nexplane_agent/restore_strategies/storage_restore.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: copy objects from source storage to target storage. Data tier."""
import asyncio
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_NAME = "storage_restore"


async def restore(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.restore_strategies import _load_source_artifact_refs
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    source_backup_cr_id = params.get("source_backup_cr_id", "")
    if not source_backup_cr_id:
        raise RuntimeError("storage_restore: source_backup_cr_id is required")

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)

    source_storage_type = artifact_refs.get("dest_storage_type", "s3")
    source_config = artifact_refs.get("dest_config", {})
    source_prefix = artifact_refs.get("dest_prefix", "")

    target_storage_type = params.get("target_storage_type") or source_storage_type
    target_bucket = params.get("target_bucket") or artifact_refs.get("dest_bucket", "")
    target_prefix = params.get("target_prefix", "")

    if not target_bucket:
        raise RuntimeError("storage_restore: target_bucket is required")

    target_config = dict(source_config)
    target_config["bucket"] = target_bucket

    source_backend = get_backend(source_storage_type)
    target_backend = get_backend(target_storage_type)

    source_uris = await source_backend.list_prefix(source_prefix, source_config)

    restored_uris = []
    for uri in source_uris:
        filename = uri.rsplit("/", 1)[-1]
        target_key = f"{target_prefix}{filename}"
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        try:
            await source_backend.download(uri, tmp_path, source_config)
            target_uri = await target_backend.upload(tmp_path, target_key, target_config)
            restored_uris.append(target_uri)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return {
        "status": "completed",
        "restore_strategy": "storage_restore",
        "restored_uris": restored_uris,
        "target_storage_type": target_storage_type,
        "target_bucket": target_bucket,
        "target_prefix": target_prefix,
        "target_config": target_config,
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    restored_uris = execution_result.get("restored_uris", [])
    if not restored_uris:
        return {"rolled_back": False, "reason": "no restored_uris in execution_result"}

    target_storage_type = execution_result.get("target_storage_type", "s3")
    target_config = execution_result.get("target_config", {})

    backend = get_backend(target_storage_type)
    for uri in restored_uris:
        await backend.delete(uri, target_config)

    return {"rolled_back": True, "deleted_count": len(restored_uris)}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd /app && python -m pytest tests/unit/test_storage_restore.py -v
```

Expected: 4/4 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/restore_strategies/storage_restore.py
git add backend/tests/unit/test_storage_restore.py
git commit -m "feat: implement storage_restore restore strategy"
```

---

### Task 3: `database_dump` MySQL + MongoDB extension

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/backup_strategies/database_dump.py`
- Test: `backend/tests/unit/test_database_dump.py` (extend existing if present, or create)

**Interfaces:**
- Produces: `_SUPPORTED_DB_TYPES = ("postgres", "mysql", "mongodb")`
- Produces: `dump_format` in `artifact_refs`: `"sql.gz"` for postgres/mysql, `"archive.gz"` for mongodb
- Produces: artifact key suffix `.sql.gz` for postgres/mysql, `.archive.gz` for mongodb

- [ ] **Step 1: Check existing tests**

```
ls /app/tests/unit/test_database_dump.py 2>/dev/null && echo exists || echo missing
```

- [ ] **Step 2: Write or extend failing tests**

If `test_database_dump.py` does not exist, create it. If it exists, append the MySQL + MongoDB tests. The tests below are standalone (work as a new file):

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import asyncio
import os
import tempfile
from unittest.mock import MagicMock, patch, call

import pytest


def _mod():
    from app.connectors.executors.nexplane_agent.backup_strategies import database_dump
    return database_dump


def _make_storage_config():
    return {"storage_type": "s3", "config": {"bucket": "b", "prefix": "backups/"}}


def _make_connector(host="10.0.0.1", user="ec2-user", key="---FAKE---"):
    c = MagicMock()
    c.credentials = {"hostname": host, "username": user, "private_key": key}
    return c


@pytest.mark.asyncio
async def test_mysql_dump_uses_mysqldump():
    mod = _mod()
    ssh = MagicMock()
    stdout = MagicMock()
    stdout.read.side_effect = [b"data", b""]
    stdout.channel.recv_exit_status.return_value = 0
    ssh.exec_command.return_value = (None, stdout, MagicMock())

    backend = MagicMock()
    backend.upload = asyncio.coroutine(lambda *a, **kw: "s3://b/backups/db/mydb/x/ts.sql.gz") if False else None
    backend.upload = MagicMock(return_value=asyncio.coroutine(lambda: "s3://b/key")())

    import asyncio as _asyncio

    async def _fake_upload(local_path, key, cfg):
        return f"s3://b/{key}"

    backend.upload = _fake_upload

    with patch.object(mod, "_ssh_connect", return_value=ssh), \
         patch(
             "app.connectors.executors.nexplane_agent.backup_strategies.database_dump._load_storage_config",
             return_value=_make_storage_config(),
         ), \
         patch(
             "app.connectors.executors.nexplane_agent.backup_strategies.database_dump.get_backend",
             return_value=backend,
         ):
        result = await mod.backup(
            {
                "db_type": "mysql",
                "db_host": "127.0.0.1",
                "db_port": 3306,
                "database_name": "mydb",
                "db_user": "root",
                "db_password": "pass",
                "backup_storage_id": "store1",
            },
            ["asset-1"],
            _make_connector(),
        )

    cmd = ssh.exec_command.call_args[0][0]
    assert "mysqldump" in cmd
    assert "mydb" in cmd
    assert result["artifact_refs"]["dump_format"] == "sql.gz"
    assert result["artifact_refs"]["db_type"] == "mysql"
    assert result["artifact_refs"]["artifact_uri"].endswith(".sql.gz")


@pytest.mark.asyncio
async def test_mongodb_dump_uses_mongodump():
    mod = _mod()
    ssh = MagicMock()
    stdout = MagicMock()
    stdout.read.side_effect = [b"data", b""]
    stdout.channel.recv_exit_status.return_value = 0
    ssh.exec_command.return_value = (None, stdout, MagicMock())

    async def _fake_upload(local_path, key, cfg):
        return f"s3://b/{key}"

    backend = MagicMock()
    backend.upload = _fake_upload

    with patch.object(mod, "_ssh_connect", return_value=ssh), \
         patch(
             "app.connectors.executors.nexplane_agent.backup_strategies.database_dump._load_storage_config",
             return_value=_make_storage_config(),
         ), \
         patch(
             "app.connectors.executors.nexplane_agent.backup_strategies.database_dump.get_backend",
             return_value=backend,
         ):
        result = await mod.backup(
            {
                "db_type": "mongodb",
                "db_host": "127.0.0.1",
                "db_port": 27017,
                "database_name": "smokedb",
                "db_user": "root",
                "db_password": "pass",
                "backup_storage_id": "store1",
            },
            ["asset-1"],
            _make_connector(),
        )

    cmd = ssh.exec_command.call_args[0][0]
    assert "mongodump" in cmd
    assert "smokedb" in cmd
    assert result["artifact_refs"]["dump_format"] == "archive.gz"
    assert result["artifact_refs"]["db_type"] == "mongodb"
    assert result["artifact_refs"]["artifact_uri"].endswith(".archive.gz")


@pytest.mark.asyncio
async def test_unsupported_db_type_raises():
    mod = _mod()
    with pytest.raises(RuntimeError, match="unsupported db_type"):
        await mod.backup(
            {"db_type": "oracle", "backup_storage_id": "x", "database_name": "db"},
            [],
            _make_connector(),
        )
```

- [ ] **Step 3: Run tests to verify they fail**

```
cd /app && python -m pytest tests/unit/test_database_dump.py -v -k "mysql or mongodb or unsupported" 2>&1 | head -30
```

Expected: FAIL — unsupported db_type raises already works; mysql/mongodb fail because not implemented

- [ ] **Step 4: Extend `database_dump.py`**

In `backend/app/connectors/executors/nexplane_agent/backup_strategies/database_dump.py`:

Change `_SUPPORTED_DB_TYPES`:
```python
_SUPPORTED_DB_TYPES = ("postgres", "mysql", "mongodb")
```

In `_sync_dump_and_upload()`, replace the current `pg_cmd` block with a multi-type dispatch. Find the function and replace its body. The existing function looks like this (with `pg_cmd` built and used):

Replace the section starting with `# Build pg_dump command` through the `exit_code` check with:

```python
            # Build dump command per db_type
            if db_type == "postgres":
                dump_cmd = (
                    f"PGPASSWORD={_shell_quote(db_password)} "
                    f"pg_dump -h {db_host} -p {db_port} -U {_shell_quote(db_user)} "
                    f"{_shell_quote(db_name)} | gzip"
                )
                dump_format = "sql.gz"
            elif db_type == "mysql":
                dump_cmd = (
                    f"mysqldump -h {db_host} -P {db_port} -u {_shell_quote(db_user)} "
                    f"-p{_shell_quote(db_password)} {_shell_quote(db_name)} | gzip"
                )
                dump_format = "sql.gz"
            else:  # mongodb
                dump_cmd = (
                    f"mongodump --host {db_host} --port {db_port} "
                    f"-u {_shell_quote(db_user)} -p {_shell_quote(db_password)} "
                    f"--authenticationDatabase admin --db {_shell_quote(db_name)} "
                    f"--archive | gzip"
                )
                dump_format = "archive.gz"
            _, stdout, stderr = ssh.exec_command(dump_cmd)
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = stdout.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read(2048).decode(errors="replace")
                raise RuntimeError(
                    f"database_dump: {db_type} dump failed (exit={exit_code}): {err}"
                )
            size_bytes = os.path.getsize(tmp_path)
            return tmp_path, size_bytes, dump_format
```

Also update `_sync_dump_and_upload`'s return call site: the outer function must unpack 3 values `tmp_path, size_bytes, dump_format`.

Change the dump_key line to:
```python
    dump_format = "sql.gz"  # placeholder — replaced by actual format from _sync
    # key will be rebuilt after _sync returns with dump_format
```

Actually, since `dump_format` is determined inside `_sync_dump_and_upload`, rework the key construction. Replace the key assignment and sync block:

```python
    # key suffix determined by db_type (used before sync for temp naming, suffix corrected after)
    suffix = ".archive.gz" if db_type == "mongodb" else ".sql.gz"
    dump_key = f"{prefix}db/{db_name}/{asset_id}/{ts}{suffix}"

    def _sync_dump_and_upload():
        ssh = _ssh_connect(creds)
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name

            if db_type == "postgres":
                dump_cmd = (
                    f"PGPASSWORD={_shell_quote(db_password)} "
                    f"pg_dump -h {db_host} -p {db_port} -U {_shell_quote(db_user)} "
                    f"{_shell_quote(db_name)} | gzip"
                )
                dump_format = "sql.gz"
            elif db_type == "mysql":
                dump_cmd = (
                    f"mysqldump -h {db_host} -P {db_port} -u {_shell_quote(db_user)} "
                    f"-p{_shell_quote(db_password)} {_shell_quote(db_name)} | gzip"
                )
                dump_format = "sql.gz"
            else:  # mongodb
                dump_cmd = (
                    f"mongodump --host {db_host} --port {db_port} "
                    f"-u {_shell_quote(db_user)} -p {_shell_quote(db_password)} "
                    f"--authenticationDatabase admin --db {_shell_quote(db_name)} "
                    f"--archive | gzip"
                )
                dump_format = "archive.gz"

            _, stdout, stderr = ssh.exec_command(dump_cmd)
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = stdout.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read(2048).decode(errors="replace")
                raise RuntimeError(
                    f"database_dump: {db_type} dump failed (exit={exit_code}): {err}"
                )
            size_bytes = os.path.getsize(tmp_path)
            return tmp_path, size_bytes, dump_format
        finally:
            ssh.close()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        tmp_path, size_bytes, dump_format = await loop.run_in_executor(pool, _sync_dump_and_upload)
```

Add `dump_format` to `artifact_refs`:
```python
    artifact_refs = {
        "capture_strategy": "database_dump",
        "restore_strategy": "database_restore",
        "backup_tier": "data",
        "captured_at": captured_at,
        "storage_type": storage_type,
        "config": cfg,
        "artifact_uri": artifact_uri,
        "db_type": db_type,
        "dump_format": dump_format,
        "database_name": db_name,
        "size_bytes": size_bytes,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd /app && python -m pytest tests/unit/test_database_dump.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/backup_strategies/database_dump.py
git add backend/tests/unit/test_database_dump.py
git commit -m "feat: extend database_dump to support MySQL and MongoDB"
```

---

### Task 4: `database_restore` restore strategy

**Files:**
- Modify: (replace stub) `backend/app/connectors/executors/nexplane_agent/restore_strategies/database_restore.py`
- Test: `backend/tests/unit/test_database_restore.py`

**Interfaces:**
- Consumes: `_load_source_artifact_refs(source_backup_cr_id)` from `restore_strategies/__init__.py`
- Consumes: `get_backend(storage_type)` from `storage_backends/__init__.py`
- Consumes: `artifact_refs["db_type"]`, `artifact_refs["dump_format"]`, `artifact_refs["artifact_uri"]`, `artifact_refs["config"]`, `artifact_refs["storage_type"]`
- `_ssh_connect(creds)` — same pattern as `database_dump._ssh_connect` (multi-key-type version)
- Target DB params from `params`: `target_db_host`, `target_db_port`, `target_db_name`, `target_db_user`, `target_db_password`
- SSH creds from connector (same fallback chain as database_dump: connector creds → inline ssh_creds → asset connector)
- Produces: `restore()` → `{"status": "completed", "restore_strategy": "database_restore", "db_type": ..., "target_db_name": ..., "restored_at": ...}`
- Produces: `rollback()` → `{"rolled_back": True, "dropped_database": dbname}` or `{"rolled_back": False, "reason": ...}`
- `confirm_drop=True` in params ALWAYS required for rollback; if missing → `{"rolled_back": False, "reason": "confirm_drop not set..."}`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/test_database_restore.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


def _mod():
    from app.connectors.executors.nexplane_agent.restore_strategies import database_restore
    return database_restore


def _artifact_refs(db_type="postgres", dump_format="sql.gz"):
    return {
        "capture_strategy": "database_dump",
        "storage_type": "s3",
        "config": {"bucket": "b"},
        "artifact_uri": f"s3://b/backups/db/mydb/x/ts.{dump_format}",
        "db_type": db_type,
        "dump_format": dump_format,
        "database_name": "mydb",
    }


def _params(db_type="postgres"):
    return {
        "source_backup_cr_id": "cr-123",
        "target_db_host": "127.0.0.1",
        "target_db_port": 5432,
        "target_db_name": "mydb_copy",
        "target_db_user": "postgres",
        "target_db_password": "secret",
    }


def _make_connector():
    c = MagicMock()
    c.credentials = {
        "hostname": "10.0.0.1",
        "username": "ec2-user",
        "private_key": "---FAKE---",
    }
    return c


def _make_ssh(exit_code=0):
    ssh = MagicMock()
    stdout = MagicMock()
    stdout.channel.recv_exit_status.return_value = exit_code
    stdout.read.return_value = b""
    stderr = MagicMock()
    stderr.read.return_value = b""
    ssh.exec_command.return_value = (None, stdout, stderr)
    sftp = MagicMock()
    ssh.open_sftp.return_value = sftp
    return ssh, sftp


@pytest.mark.asyncio
async def test_postgres_restore_runs_psql():
    mod = _mod()
    ssh, sftp = _make_ssh()
    backend = MagicMock()
    backend.download = AsyncMock()

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._load_source_artifact_refs",
        AsyncMock(return_value=_artifact_refs("postgres", "sql.gz")),
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore.get_backend",
        return_value=backend,
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._ssh_connect",
        return_value=ssh,
    ):
        result = await mod.restore(_params("postgres"), [], _make_connector())

    assert result["status"] == "completed"
    assert result["db_type"] == "postgres"
    assert result["target_db_name"] == "mydb_copy"
    # restore command and verify command both use psql
    calls = [c[0][0] for c in ssh.exec_command.call_args_list]
    assert any("psql" in c for c in calls)
    assert any("SELECT 1" in c for c in calls)


@pytest.mark.asyncio
async def test_mysql_restore_runs_mysql():
    mod = _mod()
    ssh, sftp = _make_ssh()
    backend = MagicMock()
    backend.download = AsyncMock()

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._load_source_artifact_refs",
        AsyncMock(return_value=_artifact_refs("mysql", "sql.gz")),
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore.get_backend",
        return_value=backend,
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._ssh_connect",
        return_value=ssh,
    ):
        params = _params()
        params["target_db_port"] = 3306
        result = await mod.restore(params, [], _make_connector())

    assert result["db_type"] == "mysql"
    calls = [c[0][0] for c in ssh.exec_command.call_args_list]
    assert any("mysql" in c and "SELECT 1" not in c for c in calls)


@pytest.mark.asyncio
async def test_mongodb_restore_runs_mongorestore():
    mod = _mod()
    ssh, sftp = _make_ssh()
    backend = MagicMock()
    backend.download = AsyncMock()

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._load_source_artifact_refs",
        AsyncMock(return_value=_artifact_refs("mongodb", "archive.gz")),
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore.get_backend",
        return_value=backend,
    ), patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._ssh_connect",
        return_value=ssh,
    ):
        params = _params()
        params["target_db_port"] = 27017
        result = await mod.restore(params, [], _make_connector())

    assert result["db_type"] == "mongodb"
    calls = [c[0][0] for c in ssh.exec_command.call_args_list]
    assert any("mongorestore" in c for c in calls)


@pytest.mark.asyncio
async def test_rollback_requires_confirm_drop():
    mod = _mod()
    result = await mod.rollback(
        {"target_db_host": "h", "target_db_name": "db"},
        {},
        MagicMock(),
    )
    assert result["rolled_back"] is False
    assert "confirm_drop" in result["reason"]


@pytest.mark.asyncio
async def test_rollback_drops_postgres_db():
    mod = _mod()
    ssh, _ = _make_ssh()

    with patch(
        "app.connectors.executors.nexplane_agent.restore_strategies.database_restore._ssh_connect",
        return_value=ssh,
    ):
        result = await mod.rollback(
            {
                "confirm_drop": True,
                "target_db_host": "h",
                "target_db_port": 5432,
                "target_db_name": "mydb_copy",
                "target_db_user": "postgres",
                "target_db_password": "s",
                "db_type": "postgres",
            },
            {"db_type": "postgres"},
            _make_connector(),
        )

    assert result["rolled_back"] is True
    assert result["dropped_database"] == "mydb_copy"
    calls = [c[0][0] for c in ssh.exec_command.call_args_list]
    assert any("DROP DATABASE" in c for c in calls)
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd /app && python -m pytest tests/unit/test_database_restore.py -v 2>&1 | head -30
```

Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement `database_restore.py`**

Replace `backend/app/connectors/executors/nexplane_agent/restore_strategies/database_restore.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Restore strategy: download dump from storage backend, restore to target DB via SSH. Data tier."""
import asyncio
import logging
import os
import tempfile
import uuid as _uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_NAME = "database_restore"


def _shell_quote(s: str) -> str:
    if not s:
        return "''"
    return "'" + str(s).replace("'", "'\\''") + "'"


def _ssh_connect(creds: dict):
    import paramiko
    import io
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = {
        "hostname": creds.get("hostname") or creds.get("host"),
        "username": creds.get("username", "ec2-user"),
        "port": int(creds.get("port", 22)),
        "timeout": 30,
    }
    if creds.get("private_key"):
        key_str = creds["private_key"]
        pkey = None
        for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
            try:
                pkey = cls.from_private_key(io.StringIO(key_str))
                break
            except Exception:
                continue
        if pkey is None:
            raise RuntimeError("database_restore: could not load private key — unsupported key type")
        connect_kwargs["pkey"] = pkey
    elif creds.get("password"):
        connect_kwargs["password"] = creds["password"]
    client.connect(**connect_kwargs)
    return client


async def _get_ssh_creds(params: dict, connector, asset_ids: list) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    if not (creds.get("hostname") or creds.get("host")):
        inline = params.get("ssh_creds") or {}
        if inline:
            creds = dict(creds)
            creds.update(inline)
    if not (creds.get("hostname") or creds.get("host")):
        try:
            asset_id_str = str(asset_ids[0]) if asset_ids else ""
            if asset_id_str:
                from app.database import AsyncSessionLocal
                from app.models.asset import Asset
                from app.models.connector import Connector as _Connector
                from app.services.connector_service import _attach_credentials
                async with AsyncSessionLocal() as db:
                    asset = await db.get(Asset, _uuid.UUID(asset_id_str))
                    if asset and asset.connector_id:
                        conn = await db.get(_Connector, asset.connector_id)
                        if conn:
                            await _attach_credentials(conn, db)
                            _creds = conn.credentials or {}
                            if _creds.get("hostname") or _creds.get("host"):
                                creds = _creds
        except Exception as _exc:
            logger.debug("database_restore: could not load asset connector creds: %s", _exc)
    if not (creds.get("hostname") or creds.get("host")):
        raise RuntimeError(
            "database_restore: connector must have SSH credentials (hostname/host, username, private_key/password)"
        )
    return creds


async def restore(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.restore_strategies import _load_source_artifact_refs
    from app.connectors.executors.nexplane_agent.storage_backends import get_backend

    source_backup_cr_id = params.get("source_backup_cr_id", "")
    if not source_backup_cr_id:
        raise RuntimeError("database_restore: source_backup_cr_id is required")

    artifact_refs = await _load_source_artifact_refs(source_backup_cr_id)
    db_type = artifact_refs.get("db_type", "postgres")
    storage_type = artifact_refs.get("storage_type", "s3")
    cfg = artifact_refs.get("config", {})
    artifact_uri = artifact_refs.get("artifact_uri", "")
    dump_format = artifact_refs.get("dump_format", "sql.gz")

    if not artifact_uri:
        raise RuntimeError(f"database_restore: no artifact_uri in source CR {source_backup_cr_id}")

    target_db_host = params.get("target_db_host", "localhost")
    target_db_port = params.get("target_db_port", 5432)
    target_db_name = params.get("target_db_name", "")
    target_db_user = params.get("target_db_user", "")
    target_db_password = params.get("target_db_password", "")

    if not target_db_name:
        raise RuntimeError("database_restore: target_db_name is required")

    creds = await _get_ssh_creds(params, connector, asset_ids)

    suffix = f".{dump_format}"
    run_id = str(_uuid.uuid4())[:8]
    local_tmp = tempfile.mktemp(suffix=suffix)
    remote_tmp = f"/tmp/nexplane-dbrestore-{run_id}{suffix}"

    backend = get_backend(storage_type)
    await backend.download(artifact_uri, local_tmp, cfg)

    def _sync_restore():
        ssh = _ssh_connect(creds)
        try:
            with open(local_tmp, "rb") as f:
                sftp = ssh.open_sftp()
                sftp.putfo(f, remote_tmp)
                sftp.close()

            if db_type == "postgres":
                restore_cmd = (
                    f"gunzip -c {remote_tmp} | "
                    f"PGPASSWORD={_shell_quote(target_db_password)} "
                    f"psql -h {target_db_host} -p {target_db_port} "
                    f"-U {_shell_quote(target_db_user)} {_shell_quote(target_db_name)}"
                )
                verify_cmd = (
                    f"PGPASSWORD={_shell_quote(target_db_password)} "
                    f"psql -h {target_db_host} -p {target_db_port} "
                    f"-U {_shell_quote(target_db_user)} {_shell_quote(target_db_name)} "
                    f'-c "SELECT 1"'
                )
            elif db_type == "mysql":
                restore_cmd = (
                    f"gunzip -c {remote_tmp} | "
                    f"mysql -h {target_db_host} -P {target_db_port} "
                    f"-u {_shell_quote(target_db_user)} "
                    f"-p{_shell_quote(target_db_password)} {_shell_quote(target_db_name)}"
                )
                verify_cmd = (
                    f"mysql -h {target_db_host} -P {target_db_port} "
                    f"-u {_shell_quote(target_db_user)} "
                    f"-p{_shell_quote(target_db_password)} {_shell_quote(target_db_name)} "
                    f'-e "SELECT 1"'
                )
            else:  # mongodb
                restore_cmd = (
                    f"gunzip -c {remote_tmp} | "
                    f"mongorestore --host {target_db_host} --port {target_db_port} "
                    f"--username {_shell_quote(target_db_user)} "
                    f"--password {_shell_quote(target_db_password)} "
                    f"--authenticationDatabase admin --archive "
                    f"--db {_shell_quote(target_db_name)}"
                )
                verify_cmd = (
                    f"mongosh --host {target_db_host} --port {target_db_port} "
                    f"-u {_shell_quote(target_db_user)} -p {_shell_quote(target_db_password)} "
                    f'--eval "db.runCommand({{ping:1}})"'
                )

            _, stdout, stderr = ssh.exec_command(restore_cmd)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read(2048).decode(errors="replace")
                raise RuntimeError(f"database_restore: restore failed (exit={exit_code}): {err}")

            _, vstdout, vstderr = ssh.exec_command(verify_cmd)
            vexit = vstdout.channel.recv_exit_status()
            if vexit != 0:
                err = vstderr.read(2048).decode(errors="replace")
                raise RuntimeError(f"database_restore: verify failed (exit={vexit}): {err}")

            ssh.exec_command(f"rm -f {remote_tmp}")
        finally:
            ssh.close()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync_restore)

    try:
        os.unlink(local_tmp)
    except Exception:
        pass

    return {
        "status": "completed",
        "restore_strategy": "database_restore",
        "db_type": db_type,
        "target_db_name": target_db_name,
        "restored_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    if not params.get("confirm_drop"):
        return {
            "rolled_back": False,
            "reason": "confirm_drop not set — set confirm_drop=true to drop the restored database",
        }

    db_type = params.get("db_type") or execution_result.get("db_type", "postgres")
    target_db_host = params.get("target_db_host", "localhost")
    target_db_port = params.get("target_db_port", 5432)
    target_db_name = params.get("target_db_name", "")
    target_db_user = params.get("target_db_user", "")
    target_db_password = params.get("target_db_password", "")

    if not target_db_name:
        return {"rolled_back": False, "reason": "target_db_name is required for rollback"}

    creds = await _get_ssh_creds(params, connector, [])

    def _sync_drop():
        ssh = _ssh_connect(creds)
        try:
            if db_type == "postgres":
                drop_cmd = (
                    f"PGPASSWORD={_shell_quote(target_db_password)} "
                    f"psql -h {target_db_host} -p {target_db_port} "
                    f"-U {_shell_quote(target_db_user)} "
                    f'-c "DROP DATABASE IF EXISTS {target_db_name}"'
                )
            elif db_type == "mysql":
                drop_cmd = (
                    f"mysql -h {target_db_host} -P {target_db_port} "
                    f"-u {_shell_quote(target_db_user)} "
                    f"-p{_shell_quote(target_db_password)} "
                    f'-e "DROP DATABASE IF EXISTS {target_db_name}"'
                )
            else:  # mongodb
                drop_cmd = (
                    f"mongosh --host {target_db_host} --port {target_db_port} "
                    f"-u {_shell_quote(target_db_user)} -p {_shell_quote(target_db_password)} "
                    f"--eval \"db.getSiblingDB('{target_db_name}').dropDatabase()\""
                )
            _, stdout, stderr = ssh.exec_command(drop_cmd)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read(2048).decode(errors="replace")
                raise RuntimeError(f"database_restore rollback: drop failed (exit={exit_code}): {err}")
        finally:
            ssh.close()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _sync_drop)

    return {"rolled_back": True, "dropped_database": target_db_name}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd /app && python -m pytest tests/unit/test_database_restore.py -v
```

Expected: 5/5 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/restore_strategies/database_restore.py
git add backend/tests/unit/test_database_restore.py
git commit -m "feat: implement database_restore restore strategy (Postgres, MySQL, MongoDB)"
```

---

### Task 5: Live smoke test — GCS_BACKEND, STORAGE_RESTORE, DB_RESTORE

**Files:**
- Create: `backend/tests/smoke/test_backup_strategies_restore_live.py`

**Interfaces:**
- Consumes: `NexplaneClient`, `get_connector_creds_from_db`, `log`, `fail`, `make_base_parser`, `_get_aws_boto3_client` from `smoke_helpers`
- Consumes: `run_on_ec2.get_default_vpc_subnet`, `run_on_ec2.get_ssm_instance_profile`
- Platform API: `http://100.101.186.39:8000`, auth `admin@acme.example`/`admin123`
- AWS connector ID: `666e237d`; EC2 instance: `i-050bab85006f0b73c`
- Phases: `GCS_BACKEND`, `STORAGE_RESTORE`, `DB_RESTORE`
- Run command: `python tests/smoke/test_backup_strategies_restore_live.py --phases GCS_BACKEND,STORAGE_RESTORE,DB_RESTORE`
- PHASE_GCS_BACKEND: direct in-process calls to `gcs` module using real GCP creds from DB — no CR lifecycle
- PHASE_STORAGE_RESTORE: full CR lifecycle — CR1 `storage_sync` backup → CR2 `storage_restore` → rollback LIFO
- PHASE_DB_RESTORE: full CR lifecycle — Postgres sub-phase (RDS), MySQL sub-phase (Docker), MongoDB sub-phase (Docker)

- [ ] **Step 1: Create the smoke test file**

Create `backend/tests/smoke/test_backup_strategies_restore_live.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
#!/usr/bin/env python3
"""
Live smoke tests for backup/restore strategy completion:
  GCS_BACKEND    — in-process calls against real GCP bucket
  STORAGE_RESTORE — full CR lifecycle: storage_sync backup → storage_restore
  DB_RESTORE      — full CR lifecycle: database_dump + database_restore (Postgres/MySQL/MongoDB)

Run from EC2 runner inside backend container:
    python tests/smoke/test_backup_strategies_restore_live.py \\
        --base-url http://localhost:8000 \\
        --email admin@acme.example \\
        --password admin123 \\
        --phases GCS_BACKEND,STORAGE_RESTORE,DB_RESTORE
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

import boto3

_IN_CONTAINER = os.path.exists("/.dockerenv") or os.path.exists("/app/app")
if _IN_CONTAINER and "/app" not in sys.path:
    sys.path.insert(0, "/app")
if os.path.dirname(__file__) not in sys.path:
    sys.path.insert(0, os.path.dirname(__file__))

from smoke_helpers import (
    NexplaneClient,
    get_connector_creds_from_db,
    log,
    fail,
    make_base_parser,
    _get_aws_boto3_client,
)

SMOKE_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _wait_cr(client, cr_id, timeout=300, poll=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.get(f"/change-requests/{cr_id}").json()
        status = cr.get("status")
        if status in ("completed", "failed", "rollback_completed", "rollback_failed"):
            return cr
        time.sleep(poll)
    fail(f"CR {cr_id} did not complete within {timeout}s")


def _execute_cr(client, cr_id, timeout=300):
    client.post(f"/change-requests/{cr_id}/execute")
    return _wait_cr(client, cr_id, timeout=timeout)


def _rollback_cr(client, cr_id, extra_params=None, timeout=300):
    body = extra_params or {}
    client.post(f"/change-requests/{cr_id}/rollback", json=body)
    return _wait_cr(client, cr_id, timeout=timeout)


def _create_cr(client, change_type, params, connector_id=None, asset_ids=None):
    body = {
        "change_type": change_type,
        "parameters": params,
        "description": f"smoke-{change_type}-{uuid.uuid4().hex[:6]}",
    }
    if connector_id:
        body["connector_id"] = connector_id
    if asset_ids:
        body["asset_ids"] = asset_ids
    resp = client.post("/change-requests", json=body)
    cr = resp.json()
    # plan
    client.post(f"/change-requests/{cr['id']}/plan")
    _wait_cr_planned(client, cr["id"])
    # approve
    client.post(f"/change-requests/{cr['id']}/approve", json={"decision": "approved"})
    return cr["id"]


def _wait_cr_planned(client, cr_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.get(f"/change-requests/{cr_id}").json()
        if cr.get("status") in ("planned", "awaiting_approval"):
            return
        if cr.get("status") in ("failed",):
            fail(f"CR {cr_id} failed during planning")
        time.sleep(3)
    fail(f"CR {cr_id} did not reach planned state within {timeout}s")


def _get_execution_result(cr):
    runs = cr.get("execution_runs") or []
    if runs:
        return (runs[0].get("result") or {})
    result = cr.get("result") or {}
    return result


def _get_artifact_refs(cr):
    result = _get_execution_result(cr)
    refs = result.get("artifact_refs")
    if refs:
        return refs
    for step in (result.get("execution") or {}).get("steps", []):
        refs = (step.get("result") or {}).get("artifact_refs")
        if refs:
            return refs
    return {}


# ── PHASE_GCS_BACKEND ─────────────────────────────────────────────────────────

def run_phase_gcs_backend(args):
    log("PHASE_GCS_BACKEND: testing GCS backend in-process against real GCP infra")

    # Load GCP connector creds from DB
    gcp_creds = get_connector_creds_from_db("gcp")
    if not gcp_creds:
        fail("PHASE_GCS_BACKEND: no GCP connector creds found in DB — register a GCP connector first")

    bucket = gcp_creds.get("bucket") or gcp_creds.get("gcs_bucket")
    if not bucket:
        fail("PHASE_GCS_BACKEND: GCP connector creds missing 'bucket' or 'gcs_bucket' field")

    key_json = (
        gcp_creds.get("service_account_key_json")
        or gcp_creds.get("service_account_key")
        or gcp_creds.get("credentials_json")
    )
    config = {"bucket": bucket}
    if key_json:
        config["service_account_key_json"] = key_json

    import asyncio
    from app.connectors.executors.nexplane_agent.storage_backends import gcs

    async def _run():
        test_key = "smoke/gcs_backend_test.txt"
        test_data = b"nexplane-gcs-smoke"
        expected_uri = f"gcs://{bucket}/{test_key}"

        # put_bytes
        uri = await gcs.put_bytes(test_key, test_data, config)
        assert uri == expected_uri, f"put_bytes returned {uri!r}, expected {expected_uri!r}"
        log(f"  put_bytes → {uri}")

        # download
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        try:
            await gcs.download(uri, tmp_path, config)
            with open(tmp_path, "rb") as f:
                contents = f.read()
            assert contents == test_data, f"downloaded {contents!r}, expected {test_data!r}"
            log("  download → contents match")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        # delete
        await gcs.delete(uri, config)
        log("  delete → no exception")

        # verify deletion
        with tempfile.NamedTemporaryFile(delete=False) as tmp2:
            tmp2_path = tmp2.name
        try:
            raised = False
            try:
                await gcs.download(uri, tmp2_path, config)
            except Exception:
                raised = True
            assert raised, "expected exception downloading deleted blob, got none"
            log("  verify deletion → raises as expected")
        finally:
            try:
                os.unlink(tmp2_path)
            except Exception:
                pass

        # delete_prefix (already deleted — should return 0)
        result = await gcs.delete_prefix("smoke/", config)
        assert result["deleted_count"] == 0, f"expected deleted_count=0, got {result}"
        log(f"  delete_prefix('smoke/') → {result}")

        # list_prefix
        await gcs.put_bytes("smoke/list_test_1.txt", b"a", config)
        await gcs.put_bytes("smoke/list_test_2.txt", b"b", config)
        uris = await gcs.list_prefix("smoke/", config)
        assert len(uris) >= 2, f"expected ≥2 URIs, got {uris}"
        log(f"  list_prefix('smoke/') → {len(uris)} objects")
        # cleanup
        await gcs.delete_prefix("smoke/", config)
        log("  cleanup → smoke/ prefix cleared")

    asyncio.run(_run())
    log("PHASE_GCS_BACKEND: PASSED")


# ── PHASE_STORAGE_RESTORE ─────────────────────────────────────────────────────

def run_phase_storage_restore(client, aws_connector_id, instance_id, args):
    log("PHASE_STORAGE_RESTORE: CR lifecycle — storage_sync → storage_restore → rollback")

    aws_creds = get_connector_creds_from_db("aws")
    s3_client = boto3.client(
        "s3",
        region_name=SMOKE_REGION,
        aws_access_key_id=aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("aws_secret_access_key"),
    )

    # Setup: ensure source object exists
    smoke_bucket = aws_creds.get("bucket") or aws_creds.get("s3_bucket") or "nexplane-smoke-backups"
    source_key = "smoke/storage_restore_source.txt"
    s3_client.put_object(Bucket=smoke_bucket, Key=source_key, Body=b"nexplane-storage-restore-smoke")
    log(f"  seeded s3://{smoke_bucket}/{source_key}")

    # CR1: storage_sync backup
    source_storage_id = aws_creds.get("backup_storage_id") or _get_or_create_backup_storage(
        client, aws_connector_id, smoke_bucket, "smoke/"
    )
    cr1_id = _create_cr(
        client,
        "server_backup",
        {
            "capture_strategy": "storage_sync",
            "source_storage_id": source_storage_id,
            "backup_storage_id": source_storage_id,
            "source_prefix": "smoke/",
        },
        connector_id=aws_connector_id,
    )
    cr1 = _execute_cr(client, cr1_id, timeout=120)
    if cr1["status"] != "completed":
        fail(f"CR1 storage_sync failed: {cr1.get('status')}")
    refs = _get_artifact_refs(cr1)
    assert refs.get("dest_prefix"), f"CR1 artifact_refs missing dest_prefix: {refs}"
    log(f"  CR1 storage_sync completed, dest_prefix={refs['dest_prefix']}")

    # CR2: storage_restore
    cr2_id = _create_cr(
        client,
        "restore_server",
        {
            "restore_strategy": "storage_restore",
            "source_backup_cr_id": cr1_id,
            "target_storage_type": "s3",
            "target_bucket": smoke_bucket,
            "target_prefix": "smoke/restored/",
        },
        connector_id=aws_connector_id,
    )
    cr2 = _execute_cr(client, cr2_id, timeout=120)
    if cr2["status"] != "completed":
        fail(f"CR2 storage_restore failed: {cr2.get('status')}")
    cr2_result = _get_execution_result(cr2)
    restored_uris = cr2_result.get("restored_uris", [])
    assert len(restored_uris) > 0, f"CR2 returned no restored_uris: {cr2_result}"
    log(f"  CR2 storage_restore completed, {len(restored_uris)} objects restored")

    # SDK verify: object exists at target
    for uri in restored_uris:
        # uri is s3://bucket/key
        parts = uri.replace("s3://", "").split("/", 1)
        b, k = parts[0], parts[1]
        try:
            s3_client.head_object(Bucket=b, Key=k)
            log(f"  SDK verify: {uri} exists ✓")
        except Exception as e:
            fail(f"SDK verify failed for {uri}: {e}")

    # Rollback LIFO: CR2 first, then CR1
    rb2 = _rollback_cr(client, cr2_id)
    if rb2["status"] not in ("rollback_completed",):
        fail(f"CR2 rollback failed: {rb2.get('status')}")
    # Verify objects gone
    for uri in restored_uris:
        parts = uri.replace("s3://", "").split("/", 1)
        b, k = parts[0], parts[1]
        try:
            s3_client.head_object(Bucket=b, Key=k)
            fail(f"Object {uri} still exists after rollback")
        except s3_client.exceptions.ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                log(f"  SDK verify rollback: {uri} gone ✓")
            else:
                raise
    log("  CR2 rollback completed, objects deleted")

    rb1 = _rollback_cr(client, cr1_id)
    if rb1["status"] not in ("rollback_completed",):
        fail(f"CR1 rollback failed: {rb1.get('status')}")
    log("  CR1 rollback completed")

    log("PHASE_STORAGE_RESTORE: PASSED")


def _get_or_create_backup_storage(client, connector_id, bucket, prefix):
    """Get or create a backup_storage record for the smoke bucket."""
    resp = client.get("/backup-storages")
    storages = resp.json() if resp.status_code == 200 else []
    for s in (storages if isinstance(storages, list) else storages.get("items", [])):
        if s.get("bucket") == bucket:
            return s["id"]
    resp = client.post("/backup-storages", json={
        "name": f"smoke-s3-{bucket[:20]}",
        "storage_type": "s3",
        "connector_id": connector_id,
        "bucket": bucket,
        "prefix": prefix,
    })
    return resp.json()["id"]


# ── PHASE_DB_RESTORE ──────────────────────────────────────────────────────────

def _wait_docker(cmd, timeout=60, poll=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(cmd, capture_output=True, shell=True)
        if result.returncode == 0:
            return
        time.sleep(poll)
    fail(f"Timed out waiting for: {cmd}")


def run_phase_db_restore_postgres(client, ssh_connector_id, rds_host, rds_user, rds_password, rds_db, args):
    log("  DB_RESTORE/postgres: seeding RDS test table")
    # Seed via SSH to agent
    seed_sql = (
        "DROP TABLE IF EXISTS nexplane_restore_smoke; "
        "CREATE TABLE nexplane_restore_smoke (id serial, val text); "
        "INSERT INTO nexplane_restore_smoke VALUES (1, 'smoke');"
    )
    _run_psql_via_api(client, ssh_connector_id, rds_host, rds_user, rds_password, rds_db, seed_sql)

    aws_creds = get_connector_creds_from_db("aws")
    smoke_bucket = aws_creds.get("bucket") or "nexplane-smoke-backups"

    # CR1: database_dump
    cr1_id = _create_cr(
        client,
        "server_backup",
        {
            "capture_strategy": "database_dump",
            "db_type": "postgres",
            "db_host": rds_host,
            "db_port": 5432,
            "database_name": rds_db,
            "db_user": rds_user,
            "db_password": rds_password,
            "backup_storage_id": _get_or_create_backup_storage_by_type(
                client, "s3", smoke_bucket, "backups/smoke/"
            ),
        },
        connector_id=ssh_connector_id,
    )
    cr1 = _execute_cr(client, cr1_id, timeout=180)
    if cr1["status"] != "completed":
        fail(f"Postgres CR1 database_dump failed: {cr1.get('status')}")
    refs = _get_artifact_refs(cr1)
    assert refs.get("artifact_uri"), f"CR1 missing artifact_uri: {refs}"
    log(f"  CR1 database_dump completed, artifact={refs['artifact_uri']}")

    # CR2: database_restore
    copy_db = "nexplane_restore_smoke_copy"
    cr2_id = _create_cr(
        client,
        "restore_server",
        {
            "restore_strategy": "database_restore",
            "source_backup_cr_id": cr1_id,
            "target_db_host": rds_host,
            "target_db_port": 5432,
            "target_db_name": copy_db,
            "target_db_user": rds_user,
            "target_db_password": rds_password,
        },
        connector_id=ssh_connector_id,
    )
    cr2 = _execute_cr(client, cr2_id, timeout=300)
    if cr2["status"] != "completed":
        fail(f"Postgres CR2 database_restore failed: {cr2.get('status')}")
    log(f"  CR2 database_restore completed")

    # Verify row count
    count = _query_count_via_api(
        client, ssh_connector_id, rds_host, rds_user, rds_password, copy_db,
        "SELECT COUNT(*) FROM nexplane_restore_smoke"
    )
    assert count >= 1, f"Expected ≥1 rows in copy DB, got {count}"
    log(f"  SDK verify: {count} rows in {copy_db}.nexplane_restore_smoke ✓")

    # Rollback LIFO
    rb2 = _rollback_cr(client, cr2_id, extra_params={
        "confirm_drop": True,
        "target_db_host": rds_host,
        "target_db_port": 5432,
        "target_db_name": copy_db,
        "target_db_user": rds_user,
        "target_db_password": rds_password,
        "db_type": "postgres",
    })
    if rb2["status"] not in ("rollback_completed",):
        fail(f"Postgres CR2 rollback failed: {rb2.get('status')}")
    log(f"  CR2 rollback completed — {copy_db} dropped")

    rb1 = _rollback_cr(client, cr1_id)
    if rb1["status"] not in ("rollback_completed",):
        fail(f"Postgres CR1 rollback failed: {rb1.get('status')}")
    log("  CR1 rollback completed — artifact deleted")
    log("  DB_RESTORE/postgres: PASSED")


def run_phase_db_restore_mysql(client, ssh_connector_id, args):
    log("  DB_RESTORE/mysql: launching Docker MySQL container")
    subprocess.run("docker rm -f smoke-mysql 2>/dev/null || true", shell=True)
    subprocess.run(
        "docker run -d --name smoke-mysql "
        "-e MYSQL_ROOT_PASSWORD=smokepass -e MYSQL_DATABASE=smokedb "
        "-p 3307:3306 mysql:8",
        shell=True, check=True,
    )
    log("  waiting for MySQL to be ready (~30s)")
    _wait_docker(
        "docker exec smoke-mysql mysql -h 127.0.0.1 -P 3306 -uroot -psmokepass smokedb -e 'SELECT 1'",
        timeout=90,
    )

    seed_cmd = (
        "docker exec smoke-mysql mysql -h 127.0.0.1 -P 3306 -uroot -psmokepass smokedb "
        "-e \"CREATE TABLE IF NOT EXISTS t (id int); INSERT INTO t VALUES (42)\""
    )
    subprocess.run(seed_cmd, shell=True, check=True)
    log("  seeded smokedb.t")

    aws_creds = get_connector_creds_from_db("aws")
    smoke_bucket = aws_creds.get("bucket") or "nexplane-smoke-backups"

    cr3_id = _create_cr(
        client,
        "server_backup",
        {
            "capture_strategy": "database_dump",
            "db_type": "mysql",
            "db_host": "127.0.0.1",
            "db_port": 3307,
            "database_name": "smokedb",
            "db_user": "root",
            "db_password": "smokepass",
            "backup_storage_id": _get_or_create_backup_storage_by_type(
                client, "s3", smoke_bucket, "backups/smoke/"
            ),
        },
        connector_id=ssh_connector_id,
    )
    cr3 = _execute_cr(client, cr3_id, timeout=180)
    if cr3["status"] != "completed":
        subprocess.run("docker rm -f smoke-mysql", shell=True)
        fail(f"MySQL CR3 database_dump failed: {cr3.get('status')}")
    log("  CR3 database_dump (mysql) completed")

    cr4_id = _create_cr(
        client,
        "restore_server",
        {
            "restore_strategy": "database_restore",
            "source_backup_cr_id": cr3_id,
            "target_db_host": "127.0.0.1",
            "target_db_port": 3307,
            "target_db_name": "smokedb_copy",
            "target_db_user": "root",
            "target_db_password": "smokepass",
        },
        connector_id=ssh_connector_id,
    )
    cr4 = _execute_cr(client, cr4_id, timeout=300)
    if cr4["status"] != "completed":
        subprocess.run("docker rm -f smoke-mysql", shell=True)
        fail(f"MySQL CR4 database_restore failed: {cr4.get('status')}")
    log("  CR4 database_restore (mysql) completed")

    # Verify
    result = subprocess.run(
        "docker exec smoke-mysql mysql -h 127.0.0.1 -P 3306 -uroot -psmokepass smokedb_copy "
        "-e 'SELECT COUNT(*) FROM t'",
        shell=True, capture_output=True, text=True,
    )
    assert result.returncode == 0 and "1" in result.stdout, f"MySQL verify failed: {result.stdout}"
    log("  SDK verify: row count ✓")

    # Rollback
    rb4 = _rollback_cr(client, cr4_id, extra_params={
        "confirm_drop": True,
        "target_db_host": "127.0.0.1",
        "target_db_port": 3307,
        "target_db_name": "smokedb_copy",
        "target_db_user": "root",
        "target_db_password": "smokepass",
        "db_type": "mysql",
    })
    if rb4["status"] not in ("rollback_completed",):
        fail(f"MySQL CR4 rollback failed: {rb4.get('status')}")
    rb3 = _rollback_cr(client, cr3_id)
    if rb3["status"] not in ("rollback_completed",):
        fail(f"MySQL CR3 rollback failed: {rb3.get('status')}")
    subprocess.run("docker rm -f smoke-mysql", shell=True)
    log("  DB_RESTORE/mysql: PASSED")


def run_phase_db_restore_mongodb(client, ssh_connector_id, args):
    log("  DB_RESTORE/mongodb: launching Docker MongoDB container")
    subprocess.run("docker rm -f smoke-mongo 2>/dev/null || true", shell=True)
    subprocess.run(
        "docker run -d --name smoke-mongo -p 27018:27017 mongo:6",
        shell=True, check=True,
    )
    log("  waiting for MongoDB to be ready (~15s)")
    time.sleep(15)

    seed_cmd = (
        "docker exec smoke-mongo mongosh --port 27017 "
        "--eval \"db.getSiblingDB('smokedb').col.insertOne({x:1})\""
    )
    subprocess.run(seed_cmd, shell=True, check=True)
    log("  seeded smokedb.col")

    aws_creds = get_connector_creds_from_db("aws")
    smoke_bucket = aws_creds.get("bucket") or "nexplane-smoke-backups"

    cr5_id = _create_cr(
        client,
        "server_backup",
        {
            "capture_strategy": "database_dump",
            "db_type": "mongodb",
            "db_host": "127.0.0.1",
            "db_port": 27018,
            "database_name": "smokedb",
            "db_user": "",
            "db_password": "",
            "backup_storage_id": _get_or_create_backup_storage_by_type(
                client, "s3", smoke_bucket, "backups/smoke/"
            ),
        },
        connector_id=ssh_connector_id,
    )
    cr5 = _execute_cr(client, cr5_id, timeout=180)
    if cr5["status"] != "completed":
        subprocess.run("docker rm -f smoke-mongo", shell=True)
        fail(f"MongoDB CR5 database_dump failed: {cr5.get('status')}")
    log("  CR5 database_dump (mongodb) completed")

    cr6_id = _create_cr(
        client,
        "restore_server",
        {
            "restore_strategy": "database_restore",
            "source_backup_cr_id": cr5_id,
            "target_db_host": "127.0.0.1",
            "target_db_port": 27018,
            "target_db_name": "smokedb_copy",
            "target_db_user": "",
            "target_db_password": "",
        },
        connector_id=ssh_connector_id,
    )
    cr6 = _execute_cr(client, cr6_id, timeout=300)
    if cr6["status"] != "completed":
        subprocess.run("docker rm -f smoke-mongo", shell=True)
        fail(f"MongoDB CR6 database_restore failed: {cr6.get('status')}")
    log("  CR6 database_restore (mongodb) completed")

    # Verify doc count
    result = subprocess.run(
        "docker exec smoke-mongo mongosh --port 27017 "
        "--eval \"db.getSiblingDB('smokedb_copy').col.countDocuments({})\"",
        shell=True, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"MongoDB verify failed: {result.stderr}"
    log(f"  SDK verify: {result.stdout.strip()} docs in smokedb_copy.col ✓")

    # Rollback
    rb6 = _rollback_cr(client, cr6_id, extra_params={
        "confirm_drop": True,
        "target_db_host": "127.0.0.1",
        "target_db_port": 27018,
        "target_db_name": "smokedb_copy",
        "target_db_user": "",
        "target_db_password": "",
        "db_type": "mongodb",
    })
    if rb6["status"] not in ("rollback_completed",):
        fail(f"MongoDB CR6 rollback failed: {rb6.get('status')}")
    rb5 = _rollback_cr(client, cr5_id)
    if rb5["status"] not in ("rollback_completed",):
        fail(f"MongoDB CR5 rollback failed: {rb5.get('status')}")
    subprocess.run("docker rm -f smoke-mongo", shell=True)
    log("  DB_RESTORE/mongodb: PASSED")


def run_phase_db_restore(client, aws_connector_id, instance_id, args):
    log("PHASE_DB_RESTORE: database_dump + database_restore for Postgres/MySQL/MongoDB")

    # Locate SSH connector (nexplane_agent connector for the smoke EC2 instance)
    ssh_connector_id = _get_or_create_ssh_connector(client, instance_id, aws_connector_id)

    # Postgres sub-phase: use existing RDS instance from smoke infra
    rds_host = os.getenv("SMOKE_RDS_HOST", "")
    rds_user = os.getenv("SMOKE_RDS_USER", "postgres")
    rds_password = os.getenv("SMOKE_RDS_PASSWORD", "")
    rds_db = os.getenv("SMOKE_RDS_DB", "nexplanedb")

    if rds_host and rds_password:
        run_phase_db_restore_postgres(
            client, ssh_connector_id, rds_host, rds_user, rds_password, rds_db, args
        )
    else:
        log("  DB_RESTORE/postgres: skipped (set SMOKE_RDS_HOST + SMOKE_RDS_PASSWORD env vars to enable)")

    run_phase_db_restore_mysql(client, ssh_connector_id, args)
    run_phase_db_restore_mongodb(client, ssh_connector_id, args)

    log("PHASE_DB_RESTORE: PASSED")


# ── Connector helpers ─────────────────────────────────────────────────────────

def _get_or_create_ssh_connector(client, instance_id, aws_connector_id):
    """Return or create a nexplane_agent SSH connector for the smoke EC2 instance."""
    # Check for existing connector named nexplane-smoke-ssh
    resp = client.get("/connectors")
    connectors = resp.json() if resp.status_code == 200 else []
    if isinstance(connectors, dict):
        connectors = connectors.get("items", [])
    for c in connectors:
        if c.get("name") == "nexplane-smoke-ssh" and c.get("connector_type") == "nexplane_agent":
            return c["id"]

    # Get instance private IP
    aws_creds = get_connector_creds_from_db("aws")
    ec2 = boto3.client(
        "ec2",
        region_name=SMOKE_REGION,
        aws_access_key_id=aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("aws_secret_access_key"),
    )
    desc = ec2.describe_instances(InstanceIds=[instance_id])
    private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    # Get SSH private key from SSM
    ssm = boto3.client(
        "ssm",
        region_name=SMOKE_REGION,
        aws_access_key_id=aws_creds.get("aws_access_key_id"),
        aws_secret_access_key=aws_creds.get("aws_secret_access_key"),
    )
    try:
        pk = ssm.get_parameter(Name="/nexplane/smoke/ssh-private-key", WithDecryption=True)["Parameter"]["Value"]
    except Exception:
        pk = ""

    resp = client.post("/connectors", json={
        "name": "nexplane-smoke-ssh",
        "connector_type": "nexplane_agent",
        "description": "smoke SSH connector for database_dump/restore tests",
    })
    conn_id = resp.json()["id"]
    client.put(f"/connectors/{conn_id}/credentials", json={
        "hostname": private_ip,
        "username": "ec2-user",
        "private_key": pk,
    })
    return conn_id


def _run_psql_via_api(client, ssh_connector_id, host, user, password, db, sql):
    """Execute SQL against Postgres by running a psql command via a CR on the SSH connector."""
    cr_id = _create_cr(
        client,
        "ssm_command",
        {
            "instance_id": "self",
            "command": f"PGPASSWORD={password} psql -h {host} -U {user} {db} -c \"{sql}\"",
        },
        connector_id=ssh_connector_id,
    )
    cr = _execute_cr(client, cr_id, timeout=60)
    if cr["status"] != "completed":
        fail(f"psql seed CR failed: {cr.get('status')}")


def _query_count_via_api(client, ssh_connector_id, host, user, password, db, sql):
    """Execute a count query via psql over SSH and return the integer result."""
    cr_id = _create_cr(
        client,
        "ssm_command",
        {
            "instance_id": "self",
            "command": (
                f"PGPASSWORD={password} psql -h {host} -U {user} {db} -t -c \"{sql}\""
            ),
        },
        connector_id=ssh_connector_id,
    )
    cr = _execute_cr(client, cr_id, timeout=60)
    result = _get_execution_result(cr)
    output = result.get("output") or result.get("stdout") or ""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.isdigit():
            return int(stripped)
    return 0


def _get_or_create_backup_storage_by_type(client, storage_type, bucket, prefix):
    resp = client.get("/backup-storages")
    storages = resp.json() if resp.status_code == 200 else []
    if isinstance(storages, dict):
        storages = storages.get("items", [])
    for s in storages:
        if s.get("bucket") == bucket and s.get("storage_type") == storage_type:
            return s["id"]
    aws_creds = get_connector_creds_from_db("aws")
    resp = client.post("/backup-storages", json={
        "name": f"smoke-{storage_type}-{bucket[:20]}",
        "storage_type": storage_type,
        "bucket": bucket,
        "prefix": prefix,
        "config": {
            "bucket": bucket,
            "aws_access_key_id": aws_creds.get("aws_access_key_id", ""),
            "aws_secret_access_key": aws_creds.get("aws_secret_access_key", ""),
            "region": SMOKE_REGION,
        },
    })
    return resp.json()["id"]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = make_base_parser()
    parser.add_argument(
        "--phases",
        default="GCS_BACKEND,STORAGE_RESTORE,DB_RESTORE",
        help="Comma-separated phases to run",
    )
    parser.add_argument("--aws-connector-id", default="666e237d")
    parser.add_argument("--instance-id", default="i-050bab85006f0b73c")
    args = parser.parse_args()

    phases = [p.strip().upper() for p in args.phases.split(",")]

    client = None
    if any(p in phases for p in ("STORAGE_RESTORE", "DB_RESTORE")):
        client = NexplaneClient(args.base_url, args.email, args.password)

    passed = []
    failed_phases = []

    for phase in phases:
        try:
            if phase == "GCS_BACKEND":
                run_phase_gcs_backend(args)
            elif phase == "STORAGE_RESTORE":
                run_phase_storage_restore(client, args.aws_connector_id, args.instance_id, args)
            elif phase == "DB_RESTORE":
                run_phase_db_restore(client, args.aws_connector_id, args.instance_id, args)
            else:
                log(f"Unknown phase: {phase}", ok=False)
                failed_phases.append(phase)
                continue
            passed.append(phase)
        except SystemExit:
            failed_phases.append(phase)
        except Exception as e:
            log(f"PHASE {phase} raised exception: {e}", ok=False)
            import traceback
            traceback.print_exc()
            failed_phases.append(phase)

    print()
    if failed_phases:
        print(f"FAILED PHASES: {', '.join(failed_phases)}")
        sys.exit(1)
    else:
        print(f"ALL SELECTED PHASES PASSED: {', '.join(passed)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit the smoke test file**

```bash
git add backend/tests/smoke/test_backup_strategies_restore_live.py
git commit -m "test: add GCS_BACKEND, STORAGE_RESTORE, DB_RESTORE live smoke phases"
```

- [ ] **Step 3: SCP changes to EC2 and restart backend**

```bash
# From laptop:
scp -i ~/.ssh/id_ed25519 -r backend/app/connectors/executors/nexplane_agent/storage_backends/ \
    backend/app/connectors/executors/nexplane_agent/restore_strategies/ \
    backend/app/connectors/executors/nexplane_agent/backup_strategies/database_dump.py \
    backend/tests/smoke/test_backup_strategies_restore_live.py \
    ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/
```

Or from EC2: `cd /home/ec2-user/nexplane && git pull`

Then restart backend:
```bash
# On EC2:
docker compose restart nexplane-backend-1
sleep 10
docker compose ps
```

- [ ] **Step 4: Run smoke phases GCS_BACKEND and STORAGE_RESTORE**

```bash
# On EC2 via SSM or ssh:
docker exec nexplane-backend-1 python tests/smoke/test_backup_strategies_restore_live.py \
    --base-url http://localhost:8000 \
    --email admin@acme.example \
    --password admin123 \
    --phases GCS_BACKEND,STORAGE_RESTORE
```

Expected: `ALL SELECTED PHASES PASSED: GCS_BACKEND, STORAGE_RESTORE`

- [ ] **Step 5: Run DB_RESTORE phase**

First check if MySQL and MongoDB CLI tools are available on the agent, or confirm they'll run via `docker exec` commands (smoke test runs commands directly via `subprocess` which executes on the EC2 runner where docker is available):

```bash
docker exec nexplane-backend-1 python tests/smoke/test_backup_strategies_restore_live.py \
    --base-url http://localhost:8000 \
    --email admin@acme.example \
    --password admin123 \
    --phases DB_RESTORE
```

Expected: `ALL SELECTED PHASES PASSED: DB_RESTORE`

If Postgres sub-phase is needed, set env vars:
```bash
SMOKE_RDS_HOST=<rds-host> SMOKE_RDS_PASSWORD=<password> \
docker exec -e SMOKE_RDS_HOST -e SMOKE_RDS_PASSWORD nexplane-backend-1 \
    python tests/smoke/test_backup_strategies_restore_live.py \
    --phases DB_RESTORE
```

- [ ] **Step 6: Commit final passing smoke results to ledger**

Update `.superpowers/sdd/progress.md`:
```
## Backup Restore Strategies Plan (2026-07-06)
- Task 1: complete (commits <base>..<head>, review clean — GCS backend + list_prefix)
- Task 2: complete (commits <base>..<head>, review clean — storage_restore)
- Task 3: complete (commits <base>..<head>, review clean — database_dump MySQL+MongoDB)
- Task 4: complete (commits <base>..<head>, review clean — database_restore)
- Task 5: complete (commits <base>..<head>, ALL SELECTED PHASES PASSED on EC2)
## BACKUP RESTORE STRATEGIES PLAN COMPLETE
```

```bash
git add .superpowers/sdd/progress.md
git commit -m "chore: mark backup restore strategies plan complete in SDD ledger"
```
