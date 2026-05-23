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
    assert "ad-snapshots/20260523T120000Z/manifest.json" in deleted


# ---------------------------------------------------------------------------
# ad_forest_restore
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_restore_rejects_missing_required_params():
    from app.connectors.executors.active_directory import ad_forest_restore
    with pytest.raises((KeyError, ValueError)):
        await ad_forest_restore.execute({}, [], _MockConnector())


@pytest.mark.asyncio
async def test_restore_rejects_legacy_snapshot(monkeypatch):
    from app.connectors.executors.active_directory import ad_forest_restore

    class _FakeS3:
        def get_object(self, Bucket, Key):
            import json, io
            return {"Body": io.BytesIO(json.dumps({"format": "legacy"}).encode())}

    monkeypatch.setattr(ad_forest_restore, "_s3_client", lambda region: _FakeS3())
    with pytest.raises(ValueError, match="legacy"):
        await ad_forest_restore.execute(
            {
                "target_hostname": "clean-dc.corp.local",
                "winrm_username": "Administrator",
                "winrm_password": "P@ssw0rd",
                "snapshot_s3_prefix": "ad-snapshots/20260523T120000Z",
                "s3_bucket": "mybucket",
                "safe_mode_password": "DSRM@P4ss",
            },
            [],
            _MockConnector(),
        )


@pytest.mark.asyncio
async def test_restore_rollback_no_hostname_returns_gracefully():
    from app.connectors.executors.active_directory import ad_forest_restore
    result = await ad_forest_restore.rollback(
        {"winrm_username": "Administrator", "winrm_password": "pw"},
        {},
        _MockConnector(),
    )
    assert result["rolled_back"] is False
    assert "target_hostname" in result["reason"]


# ---------------------------------------------------------------------------
# ad_dc_decommission
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decommission_empty_list_fails():
    from app.connectors.executors.active_directory import ad_dc_decommission
    result = await ad_dc_decommission.execute(
        {"compromised_dcs": []},
        [],
        _MockConnector(),
    )
    assert result["status"] == "failed"
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_decommission_ilodrac_stub_returns_not_implemented():
    from app.connectors.executors.active_directory import ad_dc_decommission
    result = await ad_dc_decommission.execute(
        {"compromised_dcs": [
            {"type": "ilodrac", "hostname": "dc3.corp.local", "name": "dc3",
             "pam_path": "secret/dc-hw/dc3"}
        ]},
        [],
        _MockConnector(),
    )
    assert result["status"] == "failed"
    dc = result["results"][0]
    assert dc["status"] == "not_implemented"
    assert dc["pam_path"] == "secret/dc-hw/dc3"


@pytest.mark.asyncio
async def test_decommission_ec2_terminates(monkeypatch):
    from app.connectors.executors.active_directory import ad_dc_decommission

    terminated = []

    class _FakeEC2:
        def terminate_instances(self, InstanceIds):
            terminated.extend(InstanceIds)
            return {}

    monkeypatch.setattr(ad_dc_decommission, "_ec2_client", lambda creds: _FakeEC2())

    result = await ad_dc_decommission.execute(
        {"compromised_dcs": [
            {"type": "ec2", "instance_id": "i-0abc123", "name": "dc1"}
        ]},
        [],
        _MockConnector(),
    )
    assert result["status"] == "completed"
    assert "i-0abc123" in terminated
    assert result["results"][0]["status"] == "terminated"


@pytest.mark.asyncio
async def test_decommission_rollback_is_not_reversible():
    from app.connectors.executors.active_directory import ad_dc_decommission
    result = await ad_dc_decommission.rollback({}, {}, _MockConnector())
    assert result["rolled_back"] is False
    assert "not reversible" in result["reason"]
