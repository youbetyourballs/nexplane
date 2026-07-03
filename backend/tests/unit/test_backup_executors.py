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
async def test_server_snapshot_rollback_deregisters_ami_and_deletes_snapshots():
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


@pytest.mark.asyncio
async def test_restore_server_rollback_same_target_raises():
    from app.connectors.executors.nexplane_agent.restore_server import rollback, IrreversibleOperationError

    execution_result = {
        "_asset_ids": ["00000000-0000-0000-0000-000000000003"],
        "new_instance_id": "i-0new123",
        "restore_mode": "full",
    }

    with pytest.raises(IrreversibleOperationError):
        await rollback(
            parameters={
                "aws_connector_id": "00000000-0000-0000-0000-000000000001",
                "confirm_same_target": True,
            },
            execution_result=execution_result,
            connector=MagicMock(credentials={}),
        )


@pytest.mark.asyncio
async def test_server_capture_rollback_cleans_all_artifacts():
    from app.connectors.executors.nexplane_agent.server_capture import rollback

    execution_result = {
        "_asset_ids": ["00000000-0000-0000-0000-000000000003"],
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
        "app.connectors.executors.nexplane_agent.server_capture._delete_s3_prefix",
        new_callable=AsyncMock, return_value={"deleted_count": 4},
    ) as mock_s3_delete, patch(
        "app.connectors.executors.nexplane_agent.server_capture._deregister_ami",
        new_callable=AsyncMock, return_value={"rolled_back": True, "ami_id": "ami-0abc", "snapshots_deleted": 1},
    ) as mock_deregister:
        result = await rollback(
            parameters={"aws_connector_id": "00000000-0000-0000-0000-000000000001"},
            execution_result=execution_result,
            connector=MagicMock(credentials={}),
        )

    assert result["rolled_back"] is True
    assert result["errors"] == []
    mock_s3_delete.assert_awaited_once_with({"aws_region": "us-east-1"}, "test-bucket", "captures/org/asset/cr/")
    mock_deregister.assert_awaited_once_with({"aws_region": "us-east-1"}, "ami-0abc", ["snap-789"])
