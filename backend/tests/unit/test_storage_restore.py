# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
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
        "gcs://target-bucket/restored/file1.tar",
        "gcs://target-bucket/restored/file2.tar",
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
                "target_storage_type": "gcs",
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
