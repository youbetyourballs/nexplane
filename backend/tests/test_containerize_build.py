"""Tests for containerize_build executor and build_result_service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


class TestBuildResultService:
    @pytest.mark.asyncio
    async def test_writes_image_digest_and_manifests(self):
        from app.services.build_result_service import write_build_result_to_metadata
        from app.models.asset import Asset

        mock_asset = MagicMock(spec=Asset)
        mock_asset.asset_metadata = {}

        mock_db = AsyncMock()
        mock_db.get.return_value = mock_asset

        asset_id = str(uuid.uuid4())
        execution_result = {
            "steps": [{
                "result": {
                    "action": "containerize_build",
                    "app_name": "myapp",
                    "image_name": "registry/myapp:latest",
                    "image_digest": "sha256:abc123",
                    "dockerfile": "FROM debian:bookworm-slim\n",
                    "manifests": {"deployment": "...", "service": "...", "pvc": "", "config_map": ""},
                }
            }]
        }

        await write_build_result_to_metadata(mock_db, [asset_id], execution_result)

        assert mock_asset.asset_metadata is not None
        build = mock_asset.asset_metadata.get("build_results", {})
        assert "myapp" in build
        assert build["myapp"]["image_digest"] == "sha256:abc123"
        assert build["myapp"]["containerization_status"] == "image_pushed"

    @pytest.mark.asyncio
    async def test_noop_when_no_build_result(self):
        from app.services.build_result_service import write_build_result_to_metadata
        mock_db = AsyncMock()
        await write_build_result_to_metadata(mock_db, [], {"steps": []})
        mock_db.commit.assert_not_called()


class TestContainerizeBuildExecutor:
    @pytest.mark.asyncio
    async def test_executor_dispatches_with_app_profile(self):
        from app.connectors.executors.nexplane_agent.containerize_build import execute

        mock_connector = MagicMock()
        asset_id = str(uuid.uuid4())
        parameters = {"app_name": "myapp", "registry": "123.dkr.ecr.us-east-1.amazonaws.com/nexplane", "dry_run": True}

        with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job") as mock_dispatch, \
             patch("app.connectors.executors.nexplane_agent.containerize_build._load_app_profile") as mock_load:
            mock_dispatch.return_value = {"action": "containerize_build", "app_name": "myapp", "image_digest": "sha256:abc", "dockerfile": "FROM debian\n", "manifests": {}}
            mock_load.return_value = {"name": "myapp", "binary": "/usr/local/bin/myapp", "os_family": "debian", "listening_ports": [], "config_files": [], "data_directories": [], "env_vars": []}
            result = await execute(parameters, [asset_id], mock_connector)

        assert result["action"] == "containerize_build"
        dispatched_params = mock_dispatch.call_args[1]["parameters"]
        assert "app_profile" in dispatched_params
        assert dispatched_params["app_profile"]["name"] == "myapp"
