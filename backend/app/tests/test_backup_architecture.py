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
                s3_mod._delete_prefix("prefix/", config)
            )
        assert result["deleted_count"] == 2
