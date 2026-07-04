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
                asyncio.run(mod.put("key", b"data", {}))

    def test_s3_put_builds_uri(self):
        """S3 put returns an s3:// URI."""
        import asyncio
        from unittest.mock import MagicMock, patch
        from app.connectors.executors.nexplane_agent.storage_backends import s3 as s3_mod
        mock_s3 = MagicMock()
        config = {"bucket": "mybucket", "region": "us-east-1"}
        with patch("boto3.client", return_value=mock_s3):
            uri = asyncio.run(
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
            result = asyncio.run(
                s3_mod.delete_prefix("prefix/", config)
            )
        assert result["deleted_count"] == 2
