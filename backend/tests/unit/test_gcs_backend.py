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
