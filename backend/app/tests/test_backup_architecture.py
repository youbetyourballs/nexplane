# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import asyncio
import os
import tempfile

import pytest


class TestStorageBackendRegistry:
    def test_get_s3_backend_returns_module(self):
        from app.connectors.executors.nexplane_agent.storage_backends import get_backend
        mod = get_backend("s3")
        assert hasattr(mod, "upload")
        assert hasattr(mod, "download")
        assert hasattr(mod, "delete")

    def test_get_unknown_backend_raises(self):
        from app.connectors.executors.nexplane_agent.storage_backends import get_backend
        with pytest.raises(ValueError, match="Unknown storage backend"):
            get_backend("unknown_backend_xyz")

    def test_stub_backends_raise_not_implemented_upload(self):
        from app.connectors.executors.nexplane_agent.storage_backends import get_backend
        for name in ("gcs", "azure_blob", "oci_object_storage", "nfs", "local"):
            mod = get_backend(name)
            with pytest.raises(NotImplementedError):
                asyncio.run(mod.upload("/tmp/file.tar.gz", "dest/key", {}))

    def test_stub_backends_raise_not_implemented_download(self):
        from app.connectors.executors.nexplane_agent.storage_backends import get_backend
        for name in ("gcs", "azure_blob", "oci_object_storage", "nfs", "local"):
            mod = get_backend(name)
            with pytest.raises(NotImplementedError):
                asyncio.run(mod.download("gcs://bucket/key", "/tmp/out", {}))

    def test_stub_backends_raise_not_implemented_delete(self):
        from app.connectors.executors.nexplane_agent.storage_backends import get_backend
        for name in ("gcs", "azure_blob", "oci_object_storage", "nfs", "local"):
            mod = get_backend(name)
            with pytest.raises(NotImplementedError):
                asyncio.run(mod.delete("gcs://bucket/key", {}))

    def test_s3_upload_builds_uri(self):
        """S3 upload returns an s3:// URI."""
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.storage_backends import s3 as s3_mod
        mock_s3 = MagicMock()
        config = {"bucket": "mybucket", "region": "us-east-1"}
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz") as f:
            f.write(b"fake backup data")
            tmp_path = f.name
        try:
            with patch("boto3.client", return_value=mock_s3):
                uri = asyncio.run(
                    s3_mod.upload(tmp_path, "prefix/backup.tar.gz", config)
                )
            assert uri == "s3://mybucket/prefix/backup.tar.gz"
            mock_s3.upload_file.assert_called_once_with(tmp_path, "mybucket", "prefix/backup.tar.gz")
        finally:
            os.unlink(tmp_path)

    def test_s3_download_calls_download_file(self):
        """S3 download calls download_file with parsed bucket/key."""
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.storage_backends import s3 as s3_mod
        mock_s3 = MagicMock()
        config = {"bucket": "mybucket", "region": "us-east-1"}
        with patch("boto3.client", return_value=mock_s3):
            asyncio.run(
                s3_mod.download("s3://mybucket/prefix/backup.tar.gz", "/tmp/out.tar.gz", config)
            )
        mock_s3.download_file.assert_called_once_with("mybucket", "prefix/backup.tar.gz", "/tmp/out.tar.gz")

    def test_s3_delete_prefix_calls_list_and_delete(self):
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
            result = asyncio.run(
                s3_mod.delete_prefix("prefix/", config)
            )
        assert result["deleted_count"] == 2


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
        """No capture-strategy stubs remain — all 6 have been implemented."""
        pass

    def test_ebs_snapshot_artifact_refs_contains_strategy_fields(self):
        """artifact_refs from ebs_snapshot must include the 4 required base fields."""
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
        mock_s3_backend.put_bytes.return_value = "s3://bucket/prefix/manifest.json"

        with patch(
            "app.connectors.executors.nexplane_agent.aws_utils._ec2_client",
            return_value=mock_ec2,
        ), patch(
            "app.connectors.executors.nexplane_agent.backup_strategies.ebs_snapshot._get_storage_backend",
            return_value=mock_s3_backend,
        ):
            result = asyncio.run(
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
        for name in ("import_image", "database_restore", "storage_restore"):
            mod = get_strategy(name)
            with pytest.raises(NotImplementedError):
                asyncio.run(mod.restore({}, [], None))

    def test_launch_ami_restore_calls_run_instances(self):
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
            with patch(
                "app.connectors.executors.nexplane_agent.restore_strategies.launch_ami._load_source_artifact_refs",
                return_value={"ami_id": "ami-test123", "capture_strategy": "ebs_snapshot"},
            ):
                result = asyncio.run(
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


class TestBackupTargetModel:
    def test_backup_target_has_backup_tier_column(self):
        from app.models.backup_target import BackupTarget
        import sqlalchemy.inspection as insp
        cols = {c.name for c in BackupTarget.__table__.columns}
        assert "backup_tier" in cols, "BackupTarget missing backup_tier column"
        assert "capture_strategy" in cols, "BackupTarget missing capture_strategy column"

    def test_backup_target_defaults(self):
        from app.models.backup_target import BackupTarget
        # Verify column defaults exist in the model
        for col in BackupTarget.__table__.columns:
            if col.name == "backup_tier":
                assert col.server_default is not None, "backup_tier missing server_default"
                assert col.server_default.arg == "machine"
            if col.name == "capture_strategy":
                assert col.server_default is not None, "capture_strategy missing server_default"
                assert col.server_default.arg == "ebs_snapshot"


class TestLocalFilesStrategy:
    def test_local_files_backup_produces_correct_artifact_refs(self):
        import asyncio
        from unittest.mock import MagicMock, patch, AsyncMock
        from app.connectors.executors.nexplane_agent.backup_strategies import local_files

        mock_ssh = MagicMock()
        # exec_command is called 3 times: find|wc -l, du -sb, tar czf
        mock_stdout_fc = MagicMock()
        mock_stdout_fc.read.return_value = b"42"
        mock_stdout_sz = MagicMock()
        mock_stdout_sz.read.return_value = b"1024"
        mock_stdout_tar = MagicMock()
        mock_stdout_tar.read.side_effect = [b"fake tar data", b""]
        mock_stdout_tar.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_ssh.exec_command.side_effect = [
            (MagicMock(), mock_stdout_fc, mock_stderr),
            (MagicMock(), mock_stdout_sz, mock_stderr),
            (MagicMock(), mock_stdout_tar, mock_stderr),
        ]

        mock_backend = AsyncMock()
        mock_backend.upload.return_value = "s3://bucket/prefix/archive.tar.gz"

        with patch("paramiko.SSHClient", return_value=mock_ssh), \
             patch("paramiko.Ed25519Key.from_private_key", return_value=MagicMock()), \
             patch("app.connectors.executors.nexplane_agent.storage_backends.get_backend",
                   return_value=mock_backend):
            mock_connector = MagicMock()
            mock_connector.credentials = {
                "host": "10.0.0.1",
                "username": "ec2-user",
                "private_key": "fake-key",
            }
            result = asyncio.run(
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
            result = asyncio.run(
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


class TestFileRestoreToPathStrategy:
    def test_file_restore_to_path_restore_calls_download(self):
        import asyncio
        from unittest.mock import MagicMock, patch, AsyncMock
        from app.connectors.executors.nexplane_agent.restore_strategies import file_restore_to_path

        mock_backend = AsyncMock()
        mock_backend.download.return_value = None

        mock_ssh = MagicMock()
        mock_mkdir_stdout = MagicMock()
        mock_mkdir_stdout.channel.recv_exit_status.return_value = 0
        mock_ssh.exec_command.side_effect = [
            (MagicMock(), mock_mkdir_stdout, MagicMock()),
            (MagicMock(), MagicMock(), MagicMock()),
        ]
        mock_tar_stdout = MagicMock()
        mock_tar_stdout.channel.recv_exit_status.return_value = 0
        # Second exec_command call (tar extract)
        mock_ssh.exec_command.side_effect = [
            (MagicMock(), mock_mkdir_stdout, MagicMock()),
            (MagicMock(), mock_tar_stdout, MagicMock()),
        ]

        mock_sftp = MagicMock()
        mock_ssh.open_sftp.return_value = mock_sftp

        artifact_refs = {
            "artifact_uri": "s3://bucket/prefix/archive.tar.gz",
            "storage_type": "s3",
            "config": {"bucket": "bucket"},
        }

        mock_connector = MagicMock()
        mock_connector.credentials = {
            "host": "10.0.0.1",
            "username": "ec2-user",
            "private_key": "fake-key",
        }

        with patch(
            "app.connectors.executors.nexplane_agent.restore_strategies._load_source_artifact_refs",
            return_value=artifact_refs,
        ), patch(
            "app.connectors.executors.nexplane_agent.storage_backends.get_backend",
            return_value=mock_backend,
        ), patch("paramiko.SSHClient", return_value=mock_ssh), \
           patch("paramiko.RSAKey.from_private_key", return_value=MagicMock()):
            result = asyncio.run(
                file_restore_to_path.restore(
                    {
                        "source_backup_cr_id": "00000000-0000-0000-0000-000000000001",
                        "target_path": "/var/app/data",
                    },
                    ["asset-uuid"],
                    mock_connector,
                )
            )

        assert result["restored"] is True

    def test_file_restore_to_path_rollback_returns_not_reversed(self):
        import asyncio
        from app.connectors.executors.nexplane_agent.restore_strategies import file_restore_to_path

        result = asyncio.run(
            file_restore_to_path.rollback({}, {"artifact_refs": {}}, None)
        )
        assert result["rolled_back"] is False


class TestStorageSyncStrategy:
    def test_storage_sync_backup_copies_and_reports_count(self):
        import asyncio
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.backup_strategies import storage_sync

        mock_s3 = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {"Contents": [
                {"Key": "src/a.txt", "Size": 10},
                {"Key": "src/b.txt", "Size": 20},
                {"Key": "src/c.txt", "Size": 30},
            ]}
        ]
        mock_s3.get_paginator.return_value = mock_paginator

        with patch("boto3.client", return_value=mock_s3):
            result = asyncio.run(
                storage_sync.backup(
                    {
                        "source_prefix": "src/",
                        "_source_config": {"storage_type": "s3", "config": {
                            "bucket": "srcbucket", "region": "us-east-1"}},
                        "_storage_config": {"storage_type": "s3", "config": {
                            "bucket": "dstbucket", "prefix": "backups/", "region": "us-east-1"}},
                    },
                    ["asset-uuid"],
                    None,
                )
            )
        refs = result["artifact_refs"]
        assert refs["capture_strategy"] == "storage_sync"
        assert refs["restore_strategy"] == "storage_restore"
        assert refs["backup_tier"] == "data"
        assert refs["synced_count"] == 3
        assert refs["synced_bytes"] == 60
        assert refs["dest_bucket"] == "dstbucket"
        assert mock_s3.copy_object.call_count == 3

    def test_storage_sync_rollback_deletes_dest_prefix(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.connectors.executors.nexplane_agent.backup_strategies import storage_sync

        mock_backend = AsyncMock()
        mock_backend.delete_prefix.return_value = {"deleted_count": 3}
        with patch("app.connectors.executors.nexplane_agent.storage_backends.get_backend",
                   return_value=mock_backend):
            result = asyncio.run(
                storage_sync.rollback(
                    {},
                    {"artifact_refs": {
                        "dest_storage_type": "s3",
                        "dest_bucket": "dstbucket",
                        "dest_prefix": "backups/asset/2026/",
                        "dest_config": {"bucket": "dstbucket", "region": "us-east-1"},
                    }},
                    None,
                )
            )
        assert result["rolled_back"] is True
        assert result["deleted_prefix"] == "backups/asset/2026/"
        mock_backend.delete_prefix.assert_called_once()


def _mock_ssh_for_download(tar_or_dd_bytes=b"fake image data"):
    """Build a MagicMock SSHClient whose exec_command + open_sftp behave for
    the lvm/nfs backup flow. Returns (mock_ssh, mock_sftp)."""
    from unittest.mock import MagicMock
    mock_ssh = MagicMock()
    mock_sftp = MagicMock()

    def _fake_get(remote, local):
        with open(local, "wb") as f:
            f.write(tar_or_dd_bytes)
    mock_sftp.get.side_effect = _fake_get
    mock_ssh.open_sftp.return_value = mock_sftp

    def _exec(cmd, *a, **k):
        out = MagicMock()
        if "wc -l" in cmd:
            out.read.return_value = b"5"
        elif "du -sb" in cmd:
            out.read.return_value = b"2048"
        elif "stat -c" in cmd or "stat --format" in cmd:
            out.read.return_value = str(len(tar_or_dd_bytes)).encode()
        else:
            out.read.return_value = b""
        out.channel.recv_exit_status.return_value = 0
        return (MagicMock(), out, MagicMock())
    mock_ssh.exec_command.side_effect = _exec
    return mock_ssh, mock_sftp


class TestLvmSnapshotStrategy:
    def test_lvm_snapshot_backup_produces_artifact_refs(self):
        import asyncio
        from unittest.mock import patch, AsyncMock
        from app.connectors.executors.nexplane_agent.backup_strategies import lvm_snapshot

        mock_ssh, _ = _mock_ssh_for_download(b"lvm-image-bytes")
        mock_backend = AsyncMock()
        mock_backend.upload.return_value = "s3://bucket/prefix/lvm.img.gz"

        with patch("paramiko.SSHClient", return_value=mock_ssh), \
             patch("paramiko.Ed25519Key.from_private_key", return_value=object()), \
             patch("app.connectors.executors.nexplane_agent.storage_backends.get_backend",
                   return_value=mock_backend):
            result = asyncio.run(
                lvm_snapshot.backup(
                    {
                        "vg_name": "vg0",
                        "lv_name": "data",
                        "snapshot_size": "1G",
                        "ssh_creds": {"host": "10.0.0.1", "username": "ec2-user",
                                      "private_key": "fake"},
                        "_storage_config": {"storage_type": "s3", "config": {
                            "bucket": "b", "prefix": "p/", "region": "us-east-1"}},
                    },
                    ["asset-uuid"],
                    None,
                )
            )
        refs = result["artifact_refs"]
        assert refs["capture_strategy"] == "lvm_snapshot"
        assert refs["restore_strategy"] == "file_restore_to_path"
        assert refs["backup_tier"] == "machine"
        assert refs["artifact_uri"] == "s3://bucket/prefix/lvm.img.gz"
        assert refs["vg_name"] == "vg0"
        assert refs["lv_name"] == "data"

    def test_lvm_snapshot_rollback_deletes_artifact(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.connectors.executors.nexplane_agent.backup_strategies import lvm_snapshot

        mock_backend = AsyncMock()
        with patch("app.connectors.executors.nexplane_agent.storage_backends.get_backend",
                   return_value=mock_backend):
            result = asyncio.run(
                lvm_snapshot.rollback(
                    {},
                    {"artifact_refs": {
                        "storage_type": "s3",
                        "artifact_uri": "s3://bucket/key.img.gz",
                        "config": {"bucket": "bucket"},
                    }},
                    None,
                )
            )
        assert result["rolled_back"] is True
        mock_backend.delete.assert_called_once_with("s3://bucket/key.img.gz", {"bucket": "bucket"})


class TestNfsFilesStrategy:
    def test_nfs_files_backup_produces_artifact_refs(self):
        import asyncio
        from unittest.mock import patch, AsyncMock
        from app.connectors.executors.nexplane_agent.backup_strategies import nfs_files

        mock_ssh, _ = _mock_ssh_for_download(b"tar-bytes")
        mock_backend = AsyncMock()
        mock_backend.upload.return_value = "s3://bucket/prefix/nfs.tar.gz"

        with patch("paramiko.SSHClient", return_value=mock_ssh), \
             patch("paramiko.Ed25519Key.from_private_key", return_value=object()), \
             patch("app.connectors.executors.nexplane_agent.storage_backends.get_backend",
                   return_value=mock_backend):
            result = asyncio.run(
                nfs_files.backup(
                    {
                        "nfs_export_path": "/srv/nfs-export",
                        "ssh_creds": {"host": "10.0.0.1", "username": "ec2-user",
                                      "private_key": "fake"},
                        "_storage_config": {"storage_type": "s3", "config": {
                            "bucket": "b", "prefix": "p/", "region": "us-east-1"}},
                    },
                    ["asset-uuid"],
                    None,
                )
            )
        refs = result["artifact_refs"]
        assert refs["capture_strategy"] == "nfs_files"
        assert refs["restore_strategy"] == "file_restore_to_path"
        assert refs["backup_tier"] == "data"
        assert refs["artifact_uri"] == "s3://bucket/prefix/nfs.tar.gz"
        assert refs["nfs_export_path"] == "/srv/nfs-export"
        assert refs["file_count"] == 5

    def test_nfs_files_rollback_deletes_artifact(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.connectors.executors.nexplane_agent.backup_strategies import nfs_files

        mock_backend = AsyncMock()
        with patch("app.connectors.executors.nexplane_agent.storage_backends.get_backend",
                   return_value=mock_backend):
            result = asyncio.run(
                nfs_files.rollback(
                    {},
                    {"artifact_refs": {
                        "storage_type": "s3",
                        "artifact_uri": "s3://bucket/key.tar.gz",
                        "config": {"bucket": "bucket"},
                    }},
                    None,
                )
            )
        assert result["rolled_back"] is True
        mock_backend.delete.assert_called_once_with("s3://bucket/key.tar.gz", {"bucket": "bucket"})


class TestManagedDbSnapshotStrategy:
    def test_managed_db_snapshot_backup_creates_and_polls(self):
        import asyncio
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.backup_strategies import managed_db_snapshot

        mock_rds = MagicMock()
        mock_rds.create_db_snapshot.return_value = {"DBSnapshot": {
            "DBSnapshotIdentifier": "nexplane-snap-x",
            "DBSnapshotArn": "arn:aws:rds:us-east-1:1:snapshot:nexplane-snap-x",
            "Engine": "mysql",
            "AllocatedStorage": 20,
            "Status": "creating",
        }}
        mock_rds.describe_db_snapshots.return_value = {"DBSnapshots": [{
            "DBSnapshotIdentifier": "nexplane-snap-x",
            "DBSnapshotArn": "arn:aws:rds:us-east-1:1:snapshot:nexplane-snap-x",
            "Engine": "mysql",
            "AllocatedStorage": 20,
            "Status": "available",
        }]}

        with patch("boto3.client", return_value=mock_rds):
            result = asyncio.run(
                managed_db_snapshot.backup(
                    {"aws_connector_id": "", "db_instance_identifier": "smokedb"},
                    ["asset-uuid"],
                    None,
                )
            )
        refs = result["artifact_refs"]
        assert refs["capture_strategy"] == "managed_db_snapshot"
        assert refs["restore_strategy"] == "database_restore"
        assert refs["backup_tier"] == "data"
        assert refs["snapshot_id"].startswith("nexplane-snap-")
        assert refs["engine"] == "mysql"
        assert refs["allocated_storage_gb"] == 20
        mock_rds.create_db_snapshot.assert_called_once()

    def test_managed_db_snapshot_rollback_deletes_snapshot(self):
        import asyncio
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.backup_strategies import managed_db_snapshot

        mock_rds = MagicMock()

        class _NotFound(Exception):
            pass
        mock_rds.exceptions.DBSnapshotNotFoundFault = _NotFound
        mock_rds.describe_db_snapshots.side_effect = _NotFound()

        with patch("boto3.client", return_value=mock_rds):
            result = asyncio.run(
                managed_db_snapshot.rollback(
                    {"aws_connector_id": ""},
                    {"artifact_refs": {
                        "snapshot_id": "nexplane-snap-x",
                        "aws_connector_id": "",
                    }},
                    None,
                )
            )
        assert result["rolled_back"] is True
        assert result["deleted_snapshot_id"] == "nexplane-snap-x"
        mock_rds.delete_db_snapshot.assert_called_once_with(DBSnapshotIdentifier="nexplane-snap-x")


class TestDisk2VhdStrategy:
    def test_disk2vhd_backup_produces_vhdx_artifact(self):
        import asyncio
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.backup_strategies import disk2vhd

        mock_sess = MagicMock()

        def _run_ps(script):
            resp = MagicMock()
            resp.status_code = 0
            if "Get-Item" in script and "Length" in script:
                resp.std_out = b"104857600"
            else:
                resp.std_out = b"ok"
            resp.std_err = b""
            return resp
        mock_sess.run_ps.side_effect = _run_ps

        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://s3.amazonaws.com/presigned-put"

        with patch("winrm.Session", return_value=mock_sess), \
             patch("boto3.client", return_value=mock_s3):
            result = asyncio.run(
                disk2vhd.backup(
                    {
                        "winrm_host": "10.0.0.9",
                        "winrm_username": "Administrator",
                        "winrm_password": "pw",
                        "disk_list": ["C:"],
                        "aws_connector_id": "",
                        "_storage_config": {"storage_type": "s3", "config": {
                            "bucket": "b", "prefix": "p/", "region": "us-east-1"}},
                        "_disk2vhd_local_path": __file__,
                    },
                    ["asset-uuid"],
                    None,
                )
            )
        refs = result["artifact_refs"]
        assert refs["capture_strategy"] == "disk2vhd"
        assert refs["restore_strategy"] == "import_image"
        assert refs["backup_tier"] == "machine"
        assert refs["artifact_uri"].endswith(".vhdx")
        assert refs["size_bytes"] == 104857600
        assert refs["disk_list"] == ["C:"]

    def test_disk2vhd_rollback_deletes_artifact(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.connectors.executors.nexplane_agent.backup_strategies import disk2vhd

        mock_backend = AsyncMock()
        with patch("app.connectors.executors.nexplane_agent.storage_backends.get_backend",
                   return_value=mock_backend):
            result = asyncio.run(
                disk2vhd.rollback(
                    {},
                    {"artifact_refs": {
                        "storage_type": "s3",
                        "artifact_uri": "s3://bucket/key.vhdx",
                        "config": {"bucket": "bucket"},
                    }},
                    None,
                )
            )
        assert result["rolled_back"] is True
        mock_backend.delete.assert_called_once_with("s3://bucket/key.vhdx", {"bucket": "bucket"})


class TestMgnReplicationStrategy:
    def test_mgn_replication_backup_launches_and_extracts_ami(self):
        import asyncio
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.backup_strategies import mgn_replication

        mock_mgn = MagicMock()
        mock_ec2 = MagicMock()

        mock_mgn.describe_source_servers.return_value = {"items": [{
            "sourceServerID": "s-abc",
            "dataReplicationInfo": {"dataReplicationState": "CONTINUOUS"},
            "lifeCycle": {"state": "READY_FOR_TEST"},
        }]}
        mock_mgn.launch_test_instances.return_value = {"job": {"jobID": "mgnjob-1"}}
        mock_mgn.describe_jobs.return_value = {"items": [{
            "jobID": "mgnjob-1",
            "status": "COMPLETED",
            "participatingServers": [{
                "sourceServerID": "s-abc",
                "launchedEc2InstanceID": "i-test123",
            }],
        }]}
        mock_ec2.describe_instances.return_value = {"Reservations": [{"Instances": [{
            "InstanceId": "i-test123",
            "ImageId": "ami-test123",
        }]}]}

        def _client(service, **kwargs):
            return mock_mgn if service == "mgn" else mock_ec2

        with patch("boto3.client", side_effect=_client):
            result = asyncio.run(
                mgn_replication.backup(
                    {"aws_connector_id": "", "mgn_source_server_id": "s-abc"},
                    ["asset-uuid"],
                    None,
                )
            )
        refs = result["artifact_refs"]
        assert refs["capture_strategy"] == "mgn_replication"
        assert refs["restore_strategy"] == "launch_ami"
        assert refs["backup_tier"] == "machine"
        assert refs["test_instance_id"] == "i-test123"
        assert refs["ami_id"] == "ami-test123"
        assert refs["launch_job_id"] == "mgnjob-1"

    def test_mgn_replication_rollback_is_noop(self):
        import asyncio
        from app.connectors.executors.nexplane_agent.backup_strategies import mgn_replication

        result = asyncio.run(
            mgn_replication.rollback(
                {},
                {"artifact_refs": {"mgn_source_server_id": "s-abc", "ami_id": "ami-x"}},
                None,
            )
        )
        assert result["rolled_back"] is True
        assert result.get("noop") is True
