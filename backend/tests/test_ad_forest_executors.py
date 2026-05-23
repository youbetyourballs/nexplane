"""Unit tests for AD forest restore executor suite."""
from __future__ import annotations
import asyncio
import pytest


class _MockConnector:
    def __init__(self, creds=None):
        self.credentials = creds or {}


# ---------------------------------------------------------------------------
# ad_forest_snapshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_raises_without_winrm():
    from app.connectors.executors.active_directory import ad_forest_snapshot
    with pytest.raises(ValueError, match="winrm_hostname"):
        await ad_forest_snapshot.execute(
            {"s3_bucket": "mybucket"},
            [],
            _MockConnector(),
        )


@pytest.mark.asyncio
async def test_snapshot_rollback_no_artifacts_is_noop():
    from app.connectors.executors.active_directory import ad_forest_snapshot
    result = await ad_forest_snapshot.rollback(
        {"s3_bucket": "mybucket"},
        {},
        _MockConnector(),
    )
    assert result["rolled_back"] is False
    assert "No snapshot artifacts" in result["reason"]


@pytest.mark.asyncio
async def test_snapshot_rollback_with_artifacts_deletes(monkeypatch):
    from app.connectors.executors.active_directory import ad_forest_snapshot

    deleted = []

    class _FakeS3:
        def delete_object(self, Bucket, Key):
            deleted.append(Key)

    monkeypatch.setattr(ad_forest_snapshot, "_s3_client", lambda creds: _FakeS3())
    result = await ad_forest_snapshot.rollback(
        {"s3_bucket": "b"},
        {
            "s3_bucket": "b",
            "s3_prefix": "ad-snapshots/20260523T120000Z",
            "artifacts": ["IFM.zip", "GPO-backup.zip"],
        },
        _MockConnector(),
    )
    assert result["rolled_back"] is True
    assert "ad-snapshots/20260523T120000Z/IFM.zip" in deleted
    assert "ad-snapshots/20260523T120000Z/GPO-backup.zip" in deleted
