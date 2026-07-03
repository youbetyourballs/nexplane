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
    # tests/unit/test_backup_executors.py -> tests -> . (backend dir)
    test_file = pathlib.Path(__file__)
    backend_dir = test_file.parent.parent.parent
    catalog_path = backend_dir / "app/connectors/catalog/nexplane_agent.json"
    catalog = json.loads(catalog_path.read_text())
    action_ids = {a["action_id"] for a in catalog["actions"]}
    assert "server_backup" in action_ids
    assert "server_snapshot" in action_ids
    assert "server_capture" in action_ids
    assert "restore_server" in action_ids


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
    ), patch(
        "app.connectors.executors.nexplane_agent.server_backup._load_aws_creds",
        new_callable=AsyncMock,
        return_value=mock_connector.credentials,
    ), patch(
        "app.connectors.executors.nexplane_agent.server_backup._load_storage_config",
        new_callable=AsyncMock,
        return_value={"storage_type": "s3", "config": {"bucket": "test-bucket", "prefix": "backups/org/asset/cr/"}},
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
            "artifacts": {"snapshot_ids": ["snap-123", "snap-456"]},
        },
    }

    mock_ec2 = MagicMock()
    mock_ec2.delete_snapshot = MagicMock()

    with patch(
        "app.connectors.executors.nexplane_agent.server_backup._delete_s3_prefix",
        new_callable=AsyncMock,
        return_value={"deleted_count": 1},
    ), patch(
        "app.connectors.executors.nexplane_agent.server_backup._load_aws_creds",
        new_callable=AsyncMock,
        return_value={},
    ), patch(
        "app.connectors.executors.nexplane_agent.server_backup._ec2_client",
        return_value=mock_ec2,
    ):
        result = await rollback(
            parameters={},
            execution_result=execution_result,
            connector=MagicMock(credentials={}),
        )

    assert result.get("rolled_back") is True
    assert result.get("deleted_snapshots") == ["snap-123", "snap-456"]
    mock_ec2.delete_snapshot.assert_any_call(SnapshotId="snap-123")
    mock_ec2.delete_snapshot.assert_any_call(SnapshotId="snap-456")
